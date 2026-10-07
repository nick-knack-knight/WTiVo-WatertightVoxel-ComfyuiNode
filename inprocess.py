# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the WTiVo backend in an isolated worker process and exchange meshes as .npy files.

The native CGAL/OpenVDB code is memory hungry and fragments the heap, so every job runs in
a fresh process that exits when the mesh is done.  This module deliberately imports neither
torch nor ComfyUI so it can be tested stand-alone; the node passes in the ComfyUI hooks.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

from .wtivo_env import resolve, worker_env

# Stable internals. These are intentionally not ComfyUI widgets.
THICK_BAND_VOXELS = 3.0
THIN_BAND_VOXELS = 3.0
FAITHC_TRI_MODE = "auto"
FAITHC_CLAMP_ANCHORS = True
FAITHC_LAMBDA_N = 1.0
FAITHC_LAMBDA_D = 0.1

# (substring of a worker log line, progress percent when it is seen)
_STAGES = (
    ("[WTiVo-PointBudget] input", 3),
    ("[WTiVo-PointBudget] CGAL proxy points", 25),
    ("Tetrahedralizing", 28),
    ("[WTiVo-Timing] tetrahedralize", 45),
    ("Graph cutting", 48),
    ("[WTiVo-Timing] graph_cut", 70),
    ("[WTiVo-Timing] surface extraction", 78),
    ("[FINAL] v/f", 95),
    ("[DONE]", 100),
)
_FINAL_RE = re.compile(r"\[FINAL\] watertight=(\w+) \| bad_edge_groups=(\d+)")


def _kill(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait()


def process_arrays(
    vertices,
    faces,
    *,
    input_res: int = 1536,
    final_res: int = 1536,
    proxy_points: int = 12_000_000,
    proxy_eps_scale: float = 1.0,
    proxy_feature_weight: float = 1.5,
    lambda_fill: float = 20.0,
    threads: int = 16,
    thin_iso_vox: float = 0.0,
    faithc_component_mode: str = "auto",
    progress: Optional[Callable[[int], None]] = None,
    check_interrupt: Optional[Callable[[], None]] = None,
):
    """Return (vertices, faces, watertight, bad_edge_groups, seconds).

    ``progress(percent)`` is called as worker stages complete.  ``check_interrupt()`` is called
    about 4x/second and should raise to cancel; the worker process group is then killed.
    """
    if faithc_component_mode not in ("auto", "keep_all", "largest"):
        raise ValueError("faithc_component_mode must be auto, keep_all, or largest")
    if float(proxy_feature_weight) < 0.0:
        raise ValueError("proxy_feature_weight must be >= 0")
    if int(threads) < 1:
        raise ValueError("threads must be >= 1")

    vertices = np.asarray(vertices)
    faces = np.asarray(faces)
    if vertices.ndim != 2 or vertices.shape[1] != 3:
        raise ValueError(f"WTiVo vertices must be Nx3, got {vertices.shape}")
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError(f"WTiVo faces must be Mx3, got {faces.shape}")
    if len(vertices) == 0 or len(faces) == 0:
        raise ValueError("WTiVo received an empty mesh")

    python, script, build = resolve()

    with tempfile.TemporaryDirectory(prefix="wtivo_") as tmp_dir:
        v_in = os.path.join(tmp_dir, "v_in.npy")
        f_in = os.path.join(tmp_dir, "f_in.npy")
        v_out = os.path.join(tmp_dir, "v_out.npy")
        f_out = os.path.join(tmp_dir, "f_out.npy")
        np.save(v_in, np.ascontiguousarray(vertices, dtype=np.float64))
        np.save(f_in, np.ascontiguousarray(faces, dtype=np.int32))

        cmd = [
            str(python), str(script),
            "--input-vertices-npy", v_in,
            "--input-faces-npy", f_in,
            "--output-vertices-npy", v_out,
            "--output-faces-npy", f_out,
            "--input-res", str(int(input_res)),
            "--final-res", str(int(final_res)),
            "--proxy_points", str(int(proxy_points)),
            "--proxy_eps_scale", str(float(proxy_eps_scale)),
            "--proxy_feature_weight", str(float(proxy_feature_weight)),
            "--lambda_fill", str(float(lambda_fill)),
            "--threads", str(int(threads)),
            "--thick_band_voxels", str(THICK_BAND_VOXELS),
            "--thin_band_voxels", str(THIN_BAND_VOXELS),
            "--thin_iso_vox", str(float(thin_iso_vox)),
            "--faithc_component_mode", str(faithc_component_mode),
            "--faithc_tri_mode", FAITHC_TRI_MODE,
            "--faithc_clamp_anchors", str(int(FAITHC_CLAMP_ANCHORS)),
            "--faithc_lambda_n", str(FAITHC_LAMBDA_N),
            "--faithc_lambda_d", str(FAITHC_LAMBDA_D),
        ]

        logging.info("[WTiVo] Spawning isolated worker: %s", python)
        t_all = time.perf_counter()
        process = subprocess.Popen(
            cmd,
            env=worker_env(build),
            cwd=os.path.dirname(str(script)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=True,  # own process group so cancel kills CGAL/TBB threads too
        )

        lines: "queue.Queue[Optional[str]]" = queue.Queue()

        def _reader():
            try:
                for line in process.stdout:
                    lines.put(line)
            finally:
                lines.put(None)

        threading.Thread(target=_reader, name="wtivo-log", daemon=True).start()

        tail = deque(maxlen=40)
        watertight, bad_edges = False, 0
        reached = 0
        done = False
        try:
            while not done:
                if check_interrupt is not None:
                    check_interrupt()
                try:
                    line = lines.get(timeout=0.25)
                except queue.Empty:
                    continue
                if line is None:
                    done = True
                    continue
                print(line, end="", flush=True)  # stream to the ComfyUI console
                tail.append(line.rstrip("\n"))
                m = _FINAL_RE.search(line)
                if m:
                    watertight, bad_edges = m.group(1) == "True", int(m.group(2))
                if progress is not None:
                    for marker, pct in _STAGES:
                        if pct > reached and marker in line:
                            reached = pct
                            progress(pct)
            process.wait()
        except BaseException:
            _kill(process)
            raise
        finally:
            if process.poll() is None:
                _kill(process)
            process.stdout.close()

        if process.returncode != 0:
            raise RuntimeError(
                f"WTiVo worker failed with exit code {process.returncode}.\n"
                "Last output:\n" + "\n".join(tail)
            )
        if not os.path.exists(v_out) or not os.path.exists(f_out):
            raise RuntimeError("WTiVo worker finished but did not write its output .npy files.")

        final_v = np.load(v_out)
        final_f = np.load(f_out)
        total_time = time.perf_counter() - t_all
        logging.info(
            "[WTiVo] Done: %s vertices / %s faces | watertight=%s | %.2fs",
            f"{len(final_v):,}", f"{len(final_f):,}", watertight, total_time,
        )
        return final_v, final_f, watertight, bad_edges, total_time
