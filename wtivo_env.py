# SPDX-License-Identifier: GPL-3.0-or-later
"""Locate the WTiVo backend (wtivo.py + compiled extensions) and build the worker environment."""

from __future__ import annotations

import os
import sys
from pathlib import Path

NODE_ROOT = Path(__file__).resolve().parent
EXT_GLOBS = ("wtivo_core*.so", "wtivo_vdb*.so", "wtivo_gpupr*.so")
INSTALL_HINT = "Run scripts/install_linux.sh in the WTiVo node folder."


class WTiVoSetupError(RuntimeError):
    pass


def backend_dir() -> Path:
    """WTIVO_HOME if set, otherwise <node>/backend (cloned by scripts/install_linux.sh)."""
    return Path(os.environ.get("WTIVO_HOME") or (NODE_ROOT / "backend")).expanduser().resolve()


def build_dir(backend: Path | None = None) -> Path:
    """WTIVO_BUILD_DIR if set (prebuilt .so files kept elsewhere), otherwise <backend>/build."""
    env = os.environ.get("WTIVO_BUILD_DIR")
    return Path(env).expanduser().resolve() if env else (backend or backend_dir()) / "build"


def worker_python() -> str:
    """WTIVO_PYTHON if set, otherwise the interpreter running ComfyUI."""
    return os.environ.get("WTIVO_PYTHON") or sys.executable


def resolve():
    """Return (python, wtivo_script, build_dir), raising WTiVoSetupError with a fix hint."""
    backend = backend_dir()
    script = backend / "wtivo.py"
    if not script.is_file():
        raise WTiVoSetupError(f"WTiVo backend not found at {backend} (missing wtivo.py). {INSTALL_HINT}")
    if "--input-vertices-npy" not in script.read_text(encoding="utf-8", errors="replace"):
        raise WTiVoSetupError(
            f"The backend at {backend} is too old (no .npy bridge). "
            "Update it: git -C <backend> pull, then rebuild with scripts/setup_ubuntu.sh."
        )
    build = build_dir(backend)
    missing = [g.split("*")[0] for g in EXT_GLOBS if not list(build.glob(g))]
    if missing:
        raise WTiVoSetupError(
            f"Compiled WTiVo extension(s) missing in {build}: {', '.join(missing)}. {INSTALL_HINT}"
        )
    return worker_python(), script, build


def worker_env(build: Path) -> dict:
    env = dict(os.environ)
    env["WTIVO_SUBPROCESS"] = "1"
    env["WTIVO_BUILD_DIR"] = str(build)
    env["PYTHONUNBUFFERED"] = "1"
    # libopenvdb.so is bundled in build/ (also found via $ORIGIN rpath); keep it on the path
    # for builds that were copied without the rpath.
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        p for p in (str(build), env.get("LD_LIBRARY_PATH", "")) if p
    )
    return env
