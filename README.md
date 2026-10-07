# WTiVo — WatertightVoxel (ComfyUI Node, Ubuntu/Linux fork)

Turns defective, non-manifold or open triangle meshes (e.g. raw TRELLIS output) into dense, closed, manifold meshes using sparse voxel fields, CGAL tetrahedral cell cuts, a CUDA push-relabel graph cut and FaithC-style manifold contouring.

This is a Linux fork of [Mstafa-awad/WTiVo-WatertightVoxel-ComfyuiNode](https://github.com/Mstafa-awad/WTiVo-WatertightVoxel-ComfyuiNode) (original author: MostAadTech). It targets **Ubuntu 24.04 · Python 3.12 · PyTorch 2.8.0+cu128 · NVIDIA RTX A6000 (sm_86)**. The native backend is built from source: [nick-knack-knight/WTiVo-WatertightVoxel](https://github.com/nick-knack-knight/WTiVo-WatertightVoxel).

## How it works

```text
ComfyUI MESH ─► comfy_node.py ─► inprocess.py ──(.npy files)──► backend/wtivo.py   (isolated worker process)
                                                                 ├─ wtivo_vdb   (OpenVDB, FaithC)
                                                                 ├─ wtivo_core  (CGAL/TBB)
                                                                 └─ wtivo_gpupr (CUDA graph cut)
```

Each run spawns a fresh worker that exits afterwards, so CGAL/OpenVDB memory is always returned to the OS. Worker logs stream into the ComfyUI console, the progress bar follows the worker stages, and the Cancel button kills the worker.

## Requirements

* Ubuntu 24.04 x86_64, NVIDIA driver ≥ 570, **CUDA Toolkit 12.8 with `nvcc`** (needed only to compile)
* ComfyUI running in a **Python 3.12** environment with **torch 2.8.x (cu128)**
* `git`, `sudo` (for apt packages) — the installer handles the rest

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/nick-knack-knight/WTiVo-WatertightVoxel-ComfyuiNode.git
cd WTiVo-WatertightVoxel-ComfyuiNode
scripts/install_linux.sh --python /path/to/ComfyUI/venv/bin/python
```

The installer checks ComfyUI's Python/torch, installs `requirements.txt`, clones the backend into `./backend`, builds it (OpenVDB, CGAL, `wtivo_core`, `wtivo_vdb`, `wtivo_gpupr` for your GPU's architecture) and runs `verify_install.py --e2e` with ComfyUI's interpreter. Restart ComfyUI afterwards; the node is **3d/mesh/WTiVo → WTiVo - Mesh Watertight**.

Already built the backend elsewhere? Point the installer at it (no rebuild; it symlinks `./backend` to that checkout and runs the verification), or skip the installer and `export WTIVO_HOME=/path/to/WTiVo-WatertightVoxel` before starting ComfyUI:

```bash
scripts/install_linux.sh --python /path/to/ComfyUI/venv/bin/python --backend-dir /path/to/WTiVo-WatertightVoxel
```

Installer options: `--skip-apt`, `--cuda-arch 8.6`, `--ref <backend branch/tag/commit>`, `--backend-dir <existing checkout>`, `--no-e2e`.

### Build once, copy elsewhere

On the build machine, in the backend checkout: `scripts/package_build.sh` → `wtivo-build-linux.tar.gz`. On the target:

```bash
scripts/install_linux.sh --python /path/to/ComfyUI/venv/bin/python --prebuilt /path/to/wtivo-build-linux.tar.gz
sudo apt-get install -y libtbb12 libgmp10 libmpfr6 libboost-iostreams1.83.0
```

The target must match `BUILD_INFO.json` in the tarball (Python 3.12, same torch minor, same GPU architecture).

### Environment variables

| Variable | Purpose |
| :-- | :-- |
| `WTIVO_HOME` | Backend checkout (default `<node>/backend`) |
| `WTIVO_BUILD_DIR` | Directory holding the compiled `.so` files (default `<backend>/build`) |
| `WTIVO_PYTHON` | Interpreter for the worker (default: ComfyUI's) |

## Node parameters

| Parameter | Default | Description |
| :-- | :--: | :-- |
| `mesh` | — | Input ComfyUI `MESH` (batch size 1). |
| `input_res` | 1536 | Resolution of the thick UDF / graph-labelling pass. |
| `final_res` | 1536 | Resolution of the final signed field and FaithC reconstruction. |
| `proxy_points` | 12,000,000 | Proxy-point budget sent to CGAL. ~25M for 2K detail if memory allows. |
| `proxy_eps_scale` | 1.0 | Inset scale of the graph proxy geometry relative to `input_res`. |
| `proxy_feature_weight` | 1.5 | Retention priority for corners/creases. |
| `lambda_fill` | 20.0 | Fill regularisation of the tetrahedral cell cut. |
| `threads` | all CPUs | CPU threads for CGAL/OpenVDB. |
| `thin_iso_vox` | 0.0 | Surface offset (voxels) of the final field. |
| `faithc_component_mode` | keep_all | `auto`, `keep_all` or `largest`. |
| `unload_models` | on | Free ComfyUI's loaded models/VRAM before the worker starts. |

Starting points: standard `1536 / 1536 / 12M`; high detail `2048 / 2048 / 25M` (this configuration is not yet benchmarked on the A6000 — watch host RAM as well as VRAM).

## Troubleshooting

* **"WTiVo backend not found" / "Compiled WTiVo extension(s) missing"** — run `scripts/install_linux.sh`, or set `WTIVO_HOME` / `WTIVO_BUILD_DIR`.
* **`ImportError: libopenvdb.so` / `libtbb.so`** — install the apt runtime libraries listed above; `libopenvdb.so` ships in `backend/build`.
* **Warning "extensions built for python X / torch Y"** — the worker's interpreter differs from the one used to build; rebuild or set `WTIVO_PYTHON`.
* **Out of memory** — lower `proxy_points`, then the resolutions.
* **`watertight=False`** — the full worker log is in the ComfyUI console; report `bad_edge_groups`, resolutions and versions.

Run the bridge tests (no GPU needed): `python tests/test_worker_bridge.py`.

## License & attribution

GPL-3.0-or-later, derived from WTiVo (Apache-2.0 / GPL components, see `LICENSE`, `LICENSING.md`, `NOTICE`, `LICENSES/`). WTiVo builds on **CelloCut** (Xuan Yang et al.), **FaithC** (Yihao Luo et al.), OpenVDB, CGAL, oneTBB, Eigen and PyTorch.
