# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise inprocess.process_arrays against a fake backend (no GPU/torch needed).

Run from the folder that CONTAINS the node directory:  python -m unittest <node>.tests.test_worker_bridge
or simply:  python tests/test_worker_bridge.py
"""
import importlib
import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import numpy as np

NODE = Path(__file__).resolve().parents[1]
# Import only the torch-free modules (the package __init__ pulls in comfy_node/torch).
sys.modules["wtivo_node"] = type(sys)("wtivo_node")
sys.modules["wtivo_node"].__path__ = [str(NODE)]
inprocess = importlib.import_module("wtivo_node.inprocess")
wtivo_env = importlib.import_module("wtivo_node.wtivo_env")

FAKE = textwrap.dedent('''
    import sys, time, numpy as np
    a = dict(zip(sys.argv[1::2], sys.argv[2::2]))
    mode = open(__file__.replace("wtivo.py", "mode.txt")).read().strip()
    v = np.load(a["--input-vertices-npy"]); f = np.load(a["--input-faces-npy"])
    assert a["--proxy_points"] == "123456" and a["--proxy_eps_scale"] == "1.5", a
    print("[WTiVo-PointBudget] input v/f=%d/%d" % (len(v), len(f)), flush=True)
    if mode == "crash":
        print("boom: CUDA error", flush=True); sys.exit(3)
    if mode == "hang":
        time.sleep(60)
    print("Tetrahedralizing + exporting neighbors...", flush=True)
    print("[FINAL] v/f=1/1", flush=True)
    print("[FINAL] watertight=True | bad_edge_groups=0 | native_edge_watertight=True | trimesh_closed=True", flush=True)
    np.save(a["--output-vertices-npy"], (v * 2).astype(np.float32)); np.save(a["--output-faces-npy"], f.astype(np.int32))
    print("[DONE] x", flush=True)
''')

V = np.random.rand(8, 3)
F = np.array([[0, 1, 2], [2, 3, 4]], dtype=np.int64)
KW = dict(proxy_points=123456, proxy_eps_scale=1.5, threads=2)


class Bridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "build").mkdir()
        for n in ("wtivo_core.cpython-312-x86_64-linux-gnu.so", "wtivo_vdb.cpython-312-x86_64-linux-gnu.so", "wtivo_gpupr.so"):
            (root / "build" / n).touch()
        (root / "wtivo.py").write_text(FAKE)
        self.root = root
        os.environ["WTIVO_HOME"] = str(root)
        os.environ.pop("WTIVO_BUILD_DIR", None)
        self.mode("ok")

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("WTIVO_HOME", None)

    def mode(self, m):
        (self.root / "mode.txt").write_text(m)

    def test_roundtrip_and_progress(self):
        seen = []
        v, f, wt, bad, _ = inprocess.process_arrays(V, F, progress=seen.append, **KW)
        self.assertTrue(wt); self.assertEqual(bad, 0)
        np.testing.assert_allclose(v, (V * 2).astype(np.float32)); self.assertEqual(f.dtype, np.int32)
        self.assertEqual(seen, sorted(seen)); self.assertEqual(seen[-1], 100)

    def test_failure_reports_tail(self):
        self.mode("crash")
        with self.assertRaisesRegex(RuntimeError, "exit code 3(.|\n)*boom: CUDA error"):
            inprocess.process_arrays(V, F, **KW)

    def test_interrupt_kills_worker(self):
        self.mode("hang")
        t0 = time.time()

        class Cancel(Exception):
            pass

        def check():
            if time.time() - t0 > 1.0:
                raise Cancel()

        with self.assertRaises(Cancel):
            inprocess.process_arrays(V, F, check_interrupt=check, **KW)
        self.assertLess(time.time() - t0, 20)

    def test_missing_backend_message(self):
        os.environ["WTIVO_HOME"] = str(self.root / "nope")
        with self.assertRaisesRegex(wtivo_env.WTiVoSetupError, "install_linux.sh"):
            inprocess.process_arrays(V, F, **KW)

    def test_old_backend_message(self):
        (self.root / "wtivo.py").write_text("print('v1.0')\n")
        with self.assertRaisesRegex(wtivo_env.WTiVoSetupError, "too old"):
            inprocess.process_arrays(V, F, **KW)

    def test_missing_extensions_message(self):
        for p in (self.root / "build").iterdir():
            p.unlink()
        with self.assertRaisesRegex(wtivo_env.WTiVoSetupError, "wtivo_core"):
            inprocess.process_arrays(V, F, **KW)


if __name__ == "__main__":
    unittest.main()
