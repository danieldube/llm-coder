"""Regression contracts for the authoritative configuration schema."""

# ruff: noqa: PT009, PT027

import tempfile
import unittest
from pathlib import Path

from llm_coding.config import ConfigurationError, Settings, parse_settings


class TestConfigValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config_file = root / 'config.env'
        self.secrets_file = root / 'secrets.env'
        self.valid = {
            'RUNPOD_API_KEY': 'real-test-token',
            'RUNPOD_SSH_KEY': '$HOME/.ssh/runpod_test',
            'RUNPOD_POD_NAME': 'test-pod',
            'RUNPOD_IMAGE': 'example.invalid/runtime:sha-test',
            'MODEL': 'qwen3-coder-next-fp8',
            'OPENCODE_VERSION': '1.2.3',
            'VLLM_VERSION': '1.2.3',
            'VLLM_CUDA_VERSION': '129',
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def parse(self, changes: dict[str, str] | None = None) -> Settings:
        values = dict(self.valid)
        values.update(changes or {})
        self.config_file.write_text(
            ''.join(f'{key}={value}\n' for key, value in values.items())
        )
        self.secrets_file.write_text('')
        return parse_settings(self.config_file, self.secrets_file, {})

    def test_missing_and_placeholder_values_are_aggregated(self) -> None:
        with self.assertRaises(ConfigurationError) as raised:
            self.parse({'RUNPOD_API_KEY': 'REPLACE_ME', 'MODEL': ''})
        message = str(raised.exception)
        self.assertIn('MODEL is required', message)
        self.assertIn(
            'RUNPOD_API_KEY contains a documented placeholder', message
        )
        self.assertNotIn('REPLACE_ME', message)

    def test_incompatible_runtime_image_values_are_rejected(self) -> None:
        cases = {
            'documented placeholder': (
                'ghcr.io/example/runtime:sha-REPLACE_WITH_COMMIT',
                'RUNPOD_IMAGE contains a documented placeholder',
            ),
            'RunPod base image': (
                'runpod/pytorch:1.1.0-cu1290-torch291-ubuntu2404',
                'RUNPOD_IMAGE must reference an llm-coding runtime image, '
                'not a runpod/pytorch base image',
            ),
        }
        for name, (image, expected) in cases.items():
            with (
                self.subTest(name=name),
                self.assertRaises(ConfigurationError) as raised,
            ):
                self.parse({'RUNPOD_IMAGE': image})
            self.assertEqual(raised.exception.errors, (expected,))

    def test_malformed_values_are_rejected(self) -> None:
        cases = {
            'LOCAL_PROXY_PORT': 'zero',
            'ENABLE_IDEA_MCP': 'yes',
            'RUNPOD_CLOUD_TYPE': 'PRIVATE',
            'VLLM_GPU_MEMORY_UTILIZATION': '1.01',
            'IDLE_SHUTDOWN': 'forever',
            'LIFECYCLE_LOCK_TIMEOUT_SECONDS': '301',
        }
        for key, value in cases.items():
            with self.subTest(key=key), self.assertRaises(ConfigurationError):
                self.parse({key: value})

    def test_gpu_memory_utilization_must_be_finite(self) -> None:
        for value in ('nan', 'NaN', 'inf', '-inf', '1e309'):
            with (
                self.subTest(value=value),
                self.assertRaises(ConfigurationError) as raised,
            ):
                self.parse({'VLLM_GPU_MEMORY_UTILIZATION': value})
            self.assertIn(
                'VLLM_GPU_MEMORY_UTILIZATION is selected internally by MODEL',
                raised.exception.errors,
            )

    def test_gpu_memory_utilization_range_boundaries(self) -> None:
        with self.assertRaises(ConfigurationError) as raised:
            self.parse({'VLLM_GPU_MEMORY_UTILIZATION': '0'})
        self.assertIn(
            'VLLM_GPU_MEMORY_UTILIZATION is selected internally by MODEL',
            raised.exception.errors,
        )

        settings = self.parse()
        self.assertEqual(settings.vllm_gpu_memory_utilization, 0.90)

    def test_boundaries_relationships_and_defaults(self) -> None:
        settings = self.parse(
            {
                'LOCAL_PROXY_PORT': '1',
                'LOCAL_TUNNEL_PORT': '65535',
            }
        )
        self.assertEqual(settings.local_proxy_port, 1)
        self.assertEqual(settings.runpod_volume_gb, 100)
        self.assertEqual(settings.lifecycle_lock_timeout_seconds, 30)
        self.assertEqual(settings.startup_timeout_seconds, 3120)

    def test_paths_expand_environment_and_home(self) -> None:
        settings = self.parse()
        self.assertEqual(
            settings.runpod_ssh_key, Path.home() / '.ssh' / 'runpod_test'
        )

    def test_model_profile_resolves_h200_runtime_contract(self) -> None:
        settings = self.parse()
        self.assertEqual(settings.model_id, 'Qwen/Qwen3-Coder-Next-FP8')
        self.assertEqual(
            settings.model_revision,
            'da6e2ed27304dd39abadd9c82ef50e8de67bdd4c',
        )
        self.assertEqual(settings.runpod_gpu_type, 'NVIDIA H200')
        self.assertEqual(settings.runpod_gpu_count, 1)
        self.assertEqual(settings.context_size, 32768)
        self.assertEqual(settings.vllm_tool_call_parser, 'qwen3_coder')

    def test_model_owned_settings_are_rejected(self) -> None:
        with self.assertRaises(ConfigurationError) as raised:
            self.parse({'RUNPOD_GPU_TYPE': 'NVIDIA L40S'})
        self.assertIn(
            'RUNPOD_GPU_TYPE is selected internally by MODEL',
            raised.exception.errors,
        )

    def test_checked_in_example_requires_a_runtime_image(self) -> None:
        root = Path(__file__).parents[2]
        examples = root / 'python-src/llm_coding/assets/config'
        with self.assertRaises(ConfigurationError) as raised:
            parse_settings(
                examples / 'config.env.example',
                examples / 'secrets.env.example',
                {'RUNPOD_API_KEY': 'real-test-token'},
            )
        self.assertEqual(
            raised.exception.errors,
            ('RUNPOD_IMAGE contains a documented placeholder',),
        )


if __name__ == '__main__':
    unittest.main()
