"""Validate the downloaded CUDA 12.8 vLLM wheel release artifact."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

EXPECTED = {
    'vllm_version': '0.28.0',
    'vllm_commit': '2cf0a6915ce544dc493a0990f2ea38d81601128a',
    'base_image': 'runpod/pytorch:1.3.1-cu1281-torch2130-ubuntu2404',
    'base_image_digest': (
        'sha256:8ee5a5d7c421cedb3fc3a9550f1360cf385af3986d9fd60ca14b0c25ec7cc5a3'
    ),
    'torch_version': '2.13.0',
    'torch_cuda': '12.8',
    'target_platform': 'linux/amd64',
    'architectures': ['8.9', '9.0', '12.0'],
}


def verify(directory: Path) -> Path:
    """Return the wheel only when its manifest matches the reviewed ABI."""
    manifest_path = directory / 'vllm-cu128-wheel-manifest.json'
    if not manifest_path.is_file():
        raise ValueError('CUDA 12.8 wheel manifest is missing')
    wheels = list(directory.glob('*.whl'))
    if len(wheels) != 1:
        raise ValueError('Expected exactly one downloaded CUDA 12.8 wheel')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    wheel = wheels[0]
    if manifest.get('wheel_filename') != wheel.name:
        raise ValueError('Wheel filename does not match release manifest')
    actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if manifest.get('wheel_sha256') != actual:
        raise ValueError('CUDA 12.8 wheel SHA256 does not match manifest')
    for key, value in EXPECTED.items():
        if manifest.get(key) != value:
            raise ValueError(f'CUDA 12.8 wheel manifest has unexpected {key}')
    return wheel


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f'Usage: {Path(sys.argv[0]).name} WHEEL_DIRECTORY')
    print(verify(Path(sys.argv[1])).name)


if __name__ == '__main__':
    main()
