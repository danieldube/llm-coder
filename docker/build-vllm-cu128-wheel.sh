#!/usr/bin/env bash
set -euo pipefail

readonly base_image='runpod/pytorch:1.3.1-cu1281-torch2130-ubuntu2404'
readonly base_digest='sha256:8ee5a5d7c421cedb3fc3a9550f1360cf385af3986d9fd60ca14b0c25ec7cc5a3'
readonly target_platform='linux/amd64'
readonly architectures='8.9 9.0 12.0'

: "${GIT_REVISION:?GIT_REVISION must identify the initiating repository commit}"
: "${VLLM_SOURCE_REVISION:?VLLM_SOURCE_REVISION is required}"
: "${VLLM_VERSION:?VLLM_VERSION is required}"

echo '[wheel-build] verifying base environment'
"${VLLM_PYTHON}" - <<'PY'
import torch

assert torch.__version__.split('+', 1)[0] == '2.13.0'
assert torch.version.cuda is not None
assert torch.version.cuda.startswith('12.8')
PY

echo '[wheel-build] cloning vLLM'
git clone --filter=blob:none https://github.com/vllm-project/vllm.git /build/vllm
cd /build/vllm
git checkout --detach "${VLLM_SOURCE_REVISION}"
actual_revision="$(git rev-parse HEAD)"
if [[ "$actual_revision" != "$VLLM_SOURCE_REVISION" ]]; then
    echo 'Pinned vLLM source revision did not resolve exactly.' >&2
    exit 1
fi
echo '[wheel-build] checking source revision'

echo '[wheel-build] preparing existing Torch build'
"${VLLM_PYTHON}" use_existing_torch.py

echo '[wheel-build] installing build requirements'
# use_existing_torch.py removes Torch requirements. Dependencies are otherwise
# resolved by the exact revision's wheel metadata; no CUDA requirement is
# patched. The builder prerequisites were installed by the builder Dockerfile.

export CMAKE_C_COMPILER_LAUNCHER=sccache
export CMAKE_CXX_COMPILER_LAUNCHER=sccache
export CMAKE_CUDA_COMPILER_LAUNCHER=sccache
export MAX_JOBS="${MAX_JOBS:-8}"
export NVCC_THREADS="${NVCC_THREADS:-4}"
sccache --start-server

echo '[wheel-build] starting native compilation'
"${VLLM_PYTHON}" -m build --wheel --no-isolation --outdir /release

wheel="$(find /release -maxdepth 1 -type f -name '*.whl' -print -quit)"
test -n "$wheel"
echo '[wheel-build] wheel produced'

echo '[wheel-build] calculating digest'
wheel_sha256="$(sha256sum "$wheel" | awk '{print $1}')"
wheel_name="${wheel##*/}"
python_abi="$("${VLLM_PYTHON}" - <<'PY'
import sys
print(f'cp{sys.version_info.major}{sys.version_info.minor}')
PY
)"

echo '[wheel-build] validating wheel'
# Torch was removed from the exact source metadata by use_existing_torch.py.
# Install the wheel normally so its CUDA-aware dependency logic is exercised,
# then assert that the inherited Torch/CUDA contract did not change.
"${VLLM_PYTHON}" -m pip install --break-system-packages "$wheel"
"${VLLM_PYTHON}" - <<'PY'
import torch
import vllm

assert torch.__version__.split('+', 1)[0] == '2.13.0'
assert torch.version.cuda is not None and torch.version.cuda.startswith('12.8')
assert vllm.__version__ == '0.28.0'
PY

WHEEL_NAME="$wheel_name" WHEEL_SHA256="$wheel_sha256" \
PYTHON_ABI="$python_abi" ACTUAL_REVISION="$actual_revision" \
"${VLLM_PYTHON}" - <<PY
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

metadata = {
    'wheel_filename': os.environ['WHEEL_NAME'],
    'wheel_sha256': os.environ['WHEEL_SHA256'],
    'vllm_version': '${VLLM_VERSION}',
    'vllm_commit': os.environ['ACTUAL_REVISION'],
    'base_image': '${base_image}',
    'base_image_digest': '${base_digest}',
    'python_version': os.sys.version.split()[0],
    'python_abi': os.environ['PYTHON_ABI'],
    'torch_version': '2.13.0',
    'torch_cuda': '12.8',
    'target_platform': '${target_platform}',
    'architectures': '${architectures}'.split(),
    'compiler': subprocess.check_output(
        ['nvcc', '--version'], text=True
    ).strip(),
    'build_timestamp': datetime.now(UTC).isoformat(),
    'repository_commit': '${GIT_REVISION}',
    'installed_packages': subprocess.check_output(
        [os.environ['VLLM_PYTHON'], '-m', 'pip', 'freeze'], text=True
    ).splitlines(),
}
Path('/release/vllm-cu128-wheel-manifest.json').write_text(
    json.dumps(metadata, indent=2, sort_keys=True) + '\n'
)
PY

echo '[wheel-build] compiler-cache statistics'
sccache --show-stats || true
