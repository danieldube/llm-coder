"""Tests for reviewed model and runtime contracts."""

# ruff: noqa: PT009, PT027

import unittest
from dataclasses import replace

from llm_coding.models import (
    MODEL_SPECS,
    model_spec,
    validate_runtime_contract,
)


class ModelContractTests(unittest.TestCase):
    def test_profiles_resolve_deterministically_to_valid_runtime_contracts(
        self,
    ) -> None:
        for key, expected in MODEL_SPECS.items():
            with self.subTest(key=key):
                self.assertEqual(model_spec(key), expected)
                validate_runtime_contract(expected)

    def test_unknown_model_has_clear_supported_keys_error(self) -> None:
        with self.assertRaisesRegex(ValueError, 'MODEL must name a supported'):
            model_spec('unreviewed')

    def test_runtime_version_mismatch_is_rejected(self) -> None:
        invalid = replace(
            MODEL_SPECS['qwen3.8-nvfp4'],
            runtime_image_tag='cuda129',
        )
        with self.assertRaisesRegex(ValueError, 'requires vLLM 0.28.0 cu129'):
            validate_runtime_contract(invalid)


if __name__ == '__main__':
    unittest.main()
