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
            'RUNPOD_GPU_TYPE': 'NVIDIA L40S',
            'OPENCODE_VERSION': '1.2.3',
            'VLLM_VERSION': '1.2.3',
            'VLLM_CUDA_VERSION': '129',
            'MODEL_ID': 'org/model',
            'SERVED_MODEL_NAME': 'model',
            'MODEL_DISPLAY_NAME': 'Test Model',
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
            self.parse({'RUNPOD_API_KEY': 'REPLACE_ME', 'MODEL_ID': ''})
        message = str(raised.exception)
        self.assertIn('MODEL_ID is required', message)
        self.assertIn(
            'RUNPOD_API_KEY contains a documented placeholder', message
        )
        self.assertNotIn('REPLACE_ME', message)

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
            self.assertEqual(
                raised.exception.errors,
                ('VLLM_GPU_MEMORY_UTILIZATION must be a finite number',),
            )

    def test_gpu_memory_utilization_range_boundaries(self) -> None:
        with self.assertRaises(ConfigurationError) as raised:
            self.parse({'VLLM_GPU_MEMORY_UTILIZATION': '0'})
        self.assertEqual(
            raised.exception.errors,
            (
                'VLLM_GPU_MEMORY_UTILIZATION must be greater than 0 '
                'and at most 1',
            ),
        )

        for value in ('5e-324', '1'):
            with self.subTest(value=value):
                settings = self.parse({'VLLM_GPU_MEMORY_UTILIZATION': value})
                self.assertEqual(
                    settings.vllm_gpu_memory_utilization, float(value)
                )

    def test_boundaries_relationships_and_defaults(self) -> None:
        settings = self.parse(
            {
                'LOCAL_PROXY_PORT': '1',
                'LOCAL_TUNNEL_PORT': '65535',
                'MAX_OUTPUT_TOKENS': '1',
                'CONTEXT_SIZE': '1',
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

    def test_checked_in_example_is_valid_with_real_secret(self) -> None:
        root = Path(__file__).parents[2]
        examples = root / 'python-src/llm_coding/assets/config'
        settings = parse_settings(
            examples / 'config.env.example',
            examples / 'secrets.env.example',
            {'RUNPOD_API_KEY': 'real-test-token'},
        )
        self.assertEqual(settings.local_proxy_port, 18000)


if __name__ == '__main__':
    unittest.main()
