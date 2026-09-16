"""Tests for CUDA 12.8 wheel release artifact verification."""

# ruff: noqa: PT009, PT027

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


def _verifier() -> ModuleType:
    path = Path(__file__).parents[2] / 'docker/verify-vllm-cu128-wheel.py'
    spec = importlib.util.spec_from_file_location('wheel_verifier', path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WheelArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.wheel = self.directory / 'vllm-0.28.0-py3-none-any.whl'
        self.wheel.write_bytes(b'verified native wheel')
        self.manifest = dict(_verifier().EXPECTED)
        self.manifest.update(
            {
                'wheel_filename': self.wheel.name,
                'wheel_sha256': hashlib.sha256(
                    self.wheel.read_bytes()
                ).hexdigest(),
            }
        )
        (self.directory / 'vllm-cu128-wheel-manifest.json').write_text(
            json.dumps(self.manifest)
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_valid_manifest_returns_the_wheel(self) -> None:
        self.assertEqual(_verifier().verify(self.directory), self.wheel)

    def test_wrong_checksum_fails_before_installation(self) -> None:
        self.manifest['wheel_sha256'] = '0' * 64
        (self.directory / 'vllm-cu128-wheel-manifest.json').write_text(
            json.dumps(self.manifest)
        )
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            _verifier().verify(self.directory)

    def test_missing_wheel_fails_clearly(self) -> None:
        self.wheel.unlink()
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            _verifier().verify(self.directory)


if __name__ == '__main__':
    unittest.main()
