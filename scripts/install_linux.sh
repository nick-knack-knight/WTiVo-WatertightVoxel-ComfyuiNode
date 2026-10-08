#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install the WTiVo backend for this ComfyUI node on Ubuntu 24.04 (Python 3.12, torch 2.8.0+cu128).
#
#   scripts/install_linux.sh --python /path/to/ComfyUI/venv/bin/python [options]
#
#   --python PATH      interpreter ComfyUI runs with (default: python3 on PATH). It runs the worker.
#   --prebuilt FILE    unpack a tarball from the backend's scripts/package_build.sh instead of compiling
#   --backend-dir DIR  use an existing backend checkout instead of cloning into ./backend
#   --ref REF          backend branch/tag/commit to clone (default: $WTIVO_BACKEND_REF or main)
#   --skip-apt         pass through to the backend setup (no apt-get)
#   --cuda-arch 8.6    pass through to the backend setup (A6000 = 8.6)
#   --no-e2e           skip the GPU end-to-end verification
set -euo pipefail
NODE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_URL="${WTIVO_BACKEND_URL:-https://github.com/nick-knack-knight/WTiVo-WatertightVoxel.git}"
REF="${WTIVO_BACKEND_REF:-main}"
PY="$(command -v python3 || true)"; PREBUILT=""; BACKEND="$NODE/backend"; SETUP_ARGS=(); E2E=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python) PY="$2"; shift ;;
    --prebuilt) PREBUILT="$(readlink -f "$2")"; shift ;;
    --backend-dir) BACKEND="$(readlink -f "$2")"; shift ;;
    --ref) REF="$2"; shift ;;
    --skip-apt) SETUP_ARGS+=(--skip-apt) ;;
    --cuda-arch) SETUP_ARGS+=(--cuda-arch "$2"); shift ;;
    --no-e2e) E2E=0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac; shift
done
info() { echo "[WTiVo node] $*"; }
fail() { echo "[WTiVo node ERROR] $*" >&2; exit 1; }

[[ -x "$PY" ]] || fail "Python interpreter not found: '$PY' (use --python)"
"$PY" - <<'PYEOF' || fail "ComfyUI's Python must be 3.12 with torch 2.8.x (cu128)"
import sys, torch
assert sys.version_info[:2] == (3, 12), f"python {sys.version.split()[0]}"
assert torch.__version__.startswith("2.8."), f"torch {torch.__version__}"
print(f"[WTiVo node] ComfyUI python {sys.version.split()[0]} / torch {torch.__version__} / cuda {torch.version.cuda}")
PYEOF
"$PY" -m pip install -r "$NODE/requirements.txt"

if [[ ! -f "$BACKEND/wtivo.py" ]]; then
  info "Cloning backend ($REF) into $BACKEND"
  git clone "$BACKEND_URL" "$BACKEND"
  git -C "$BACKEND" checkout "$REF"
else
  info "Using existing backend at $BACKEND"
fi

# The node looks in ./backend (or $WTIVO_HOME); make an out-of-tree checkout discoverable.
if [[ "$BACKEND" != "$NODE/backend" ]]; then
  if [[ -e "$NODE/backend" && ! -L "$NODE/backend" ]]; then
    fail "$NODE/backend already exists and is not a symlink; remove it or export WTIVO_HOME=$BACKEND"
  fi
  ln -sfn "$BACKEND" "$NODE/backend"
  info "Linked $NODE/backend -> $BACKEND"
fi

if [[ -n "$PREBUILT" ]]; then
  info "Unpacking prebuilt extensions: $PREBUILT"
  mkdir -p "$BACKEND/build"
  tar -xzf "$PREBUILT" -C "$BACKEND/build"
  info "Prebuilt libraries need: sudo apt-get install -y libtbb12 libgmp10 libmpfr6 libboost-iostreams1.83.0"
elif compgen -G "$BACKEND/build/wtivo_gpupr*.so" >/dev/null && compgen -G "$BACKEND/build/wtivo_core*.so" >/dev/null \
     && compgen -G "$BACKEND/build/wtivo_vdb*.so" >/dev/null; then
  info "Compiled extensions already present in $BACKEND/build; skipping build (delete them to force a rebuild)"
else
  info "Building backend (OpenVDB + CGAL + CUDA extension; this takes a while the first time)"
  "$BACKEND/scripts/setup_ubuntu.sh" "${SETUP_ARGS[@]}"
fi

info "Verifying with ComfyUI's interpreter"
VERIFY=("$PY" "$BACKEND/scripts/verify_install.py"); [[ $E2E -eq 1 ]] && VERIFY+=(--e2e)
WTIVO_BUILD_DIR="$BACKEND/build" LD_LIBRARY_PATH="$BACKEND/build${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "${VERIFY[@]}"
info "Done. Restart ComfyUI; the node is under 3d/mesh/WTiVo. For a non-default backend location export WTIVO_HOME."
