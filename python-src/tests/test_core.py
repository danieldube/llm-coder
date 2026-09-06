#!/usr/bin/env python3
"""
Unit tests for llm-coding Python implementation
"""

import io
import json
import os

# Add the src directory to the path
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_coding import cli as cli_module
from llm_coding import runtime as runtime_module
from llm_coding.core import ConfigManager, RunPodClient, create_opencode_config


class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.temp_dir.name) / 'config'
        self.state_dir = Path(self.temp_dir.name) / 'state'
        self.config_dir.mkdir(parents=True)
        self.state_dir.mkdir()

        # Mock environment variables
        self.env_patch = patch.dict(
            os.environ,
            {
                'XDG_CONFIG_HOME': str(self.config_dir),
                'XDG_STATE_HOME': str(self.state_dir),
                'XDG_DATA_HOME': str(self.temp_dir.name),
            },
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def test_config_loading(self):
        # Create mock config files
        config_content = """# Test config
TEST_VAR=test_value
ANOTHER_VAR=another_value
"""
        secrets_content = """# Test secrets
SECRET_KEY=secret123
"""

        (self.config_dir / 'llm-coding').mkdir(parents=True)
        with open(self.config_dir / 'llm-coding' / 'config.env', 'w') as f:
            f.write(config_content)

        with open(self.config_dir / 'llm-coding' / 'secrets.env', 'w') as f:
            f.write(secrets_content)

        config_manager = ConfigManager()
        config = config_manager.load_config()

        self.assertEqual(config['TEST_VAR'], 'test_value')
        self.assertEqual(config['ANOTHER_VAR'], 'another_value')
        self.assertEqual(config['SECRET_KEY'], 'secret123')

    def test_default_paths_expand_home_directory(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(Path, 'mkdir'),
        ):
            config_manager = ConfigManager()

        self.assertEqual(
            config_manager.config_dir, Path.home() / '.config' / 'llm-coding'
        )
        self.assertEqual(
            config_manager.state_dir,
            Path.home() / '.local' / 'state' / 'llm-coding',
        )


class TestOpencodeConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name) / 'state'
        self.state_dir.mkdir()

        self.config = {
            'SERVED_MODEL_NAME': 'test-model',
            'MODEL_DISPLAY_NAME': 'Test Model',
            'CONTEXT_SIZE': '8192',
            'MAX_OUTPUT_TOKENS': '2048',
            'LOCAL_PROXY_PORT': '18000',
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_opencode_config(self):
        # Test basic config creation
        config_path = create_opencode_config(self.config, self.state_dir)

        # Check that config file was created
        self.assertTrue(config_path.exists())

        # Check content
        with open(config_path) as f:
            content = f.read()
            self.assertIn('test-model', content)
        self.assertIn('Test Model', content)


class TestRunPodClient(unittest.TestCase):
    def test_api_errors_include_runpod_response_body(self):
        response = MagicMock()
        response.status_code = 500
        response.text = '{"error":"capacity unavailable"}'
        response.raise_for_status.side_effect = requests.HTTPError(
            '500 Server Error'
        )

        with patch('llm_coding.core.requests.request', return_value=response):
            with self.assertRaisesRegex(
                RuntimeError,
                r'POST /pods/test/start failed with HTTP 500: .*capacity unavailable',
            ):
                RunPodClient('test-key')._make_request(
                    'POST', '/pods/test/start'
                )


class TestRuntimeStartPod(unittest.TestCase):
    def test_pod_create_body_includes_configured_registry_auth(self):
        config = {
            'RUNPOD_POD_NAME': 'test-pod',
            'RUNPOD_IMAGE': 'ghcr.io/example/llm-coding-runtime:1.0.0',
            'RUNPOD_GPU_TYPE': 'NVIDIA L40S',
            'RUNPOD_CONTAINER_REGISTRY_AUTH_ID': 'registry-auth-id',
            'RUNPOD_NETWORK_VOLUME_ID': 'network-volume-id',
        }

        body = runtime_module._pod_create_body(config, 'ssh-ed25519 AAAA')

        self.assertEqual(body['containerRegistryAuthId'], 'registry-auth-id')
        self.assertEqual(body['networkVolumeId'], 'network-volume-id')
        self.assertEqual(body['env'], {'SSH_PUBLIC_KEY': 'ssh-ed25519 AAAA'})
        self.assertEqual(
            body['ports'],
            ['22/tcp'],
        )

    def test_pod_create_body_omits_empty_registry_auth(self):
        config = {
            'RUNPOD_POD_NAME': 'test-pod',
            'RUNPOD_IMAGE': 'ghcr.io/example/llm-coding-runtime:1.0.0',
            'RUNPOD_GPU_TYPE': 'NVIDIA L40S',
            'RUNPOD_CONTAINER_REGISTRY_AUTH_ID': '',
        }

        body = runtime_module._pod_create_body(config, 'ssh-ed25519 AAAA')

        self.assertNotIn('containerRegistryAuthId', body)
        self.assertEqual(body['volumeInGb'], 100)

    def test_capacity_failure_explains_safe_recovery_for_local_storage(self):
        client = MagicMock()
        client.start_pod.side_effect = RuntimeError(
            'RunPod API POST /pods/test/start failed with HTTP 500: '
            '{"error":"There are not enough free GPUs on the host machine"}'
        )

        with self.assertRaisesRegex(
            RuntimeError,
            r'Wait a few minutes.*new RUNPOD_POD_NAME.*pod-local storage',
        ):
            runtime_module._start_pod_or_raise(client, 'test', {})

    def test_capacity_failure_confirms_network_volume_is_safe_to_reuse(self):
        client = MagicMock()
        client.start_pod.side_effect = RuntimeError(
            'not enough free GPUs on the host machine'
        )

        with self.assertRaisesRegex(
            RuntimeError, r'detachable network volume'
        ):
            runtime_module._start_pod_or_raise(
                client, 'test', {'RUNPOD_NETWORK_VOLUME_ID': 'vol-1'}
            )


class TestSystemdInstall(unittest.TestCase):
    def test_proxy_start_timeout_matches_configured_activation_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            runtime = bin_dir / 'llm-runtime'
            runtime.write_text('#!/bin/sh\n')
            runtime.chmod(0o755)

            config_home = root / 'config'
            config = {
                'LOCAL_PROXY_PORT': '19000',
                'LOCAL_TUNNEL_PORT': '19001',
                'RUNPOD_START_TIMEOUT_SECONDS': '10',
                'VLLM_START_TIMEOUT_SECONDS': '20',
            }
            systemctl = MagicMock()
            systemctl.return_value.returncode = 0

            with (
                patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config_home)}),
                patch.object(sys, 'argv', [str(bin_dir / 'llm-up')]),
                patch.object(runtime_module, '_systemctl', systemctl),
            ):
                runtime_module.install_systemd(config)

            unit = (
                config_home / 'systemd' / 'user' / 'llm-coding-proxy.service'
            )
            self.assertIn('TimeoutStartSec=150s', unit.read_text())

    def test_ensure_socket_reinstalls_stale_unit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            runtime = bin_dir / 'llm-runtime'
            runtime.write_text('#!/bin/sh\n')
            runtime.chmod(0o755)

            config_home = root / 'config'
            unit_dir = config_home / 'systemd' / 'user'
            unit_dir.mkdir(parents=True)
            (unit_dir / 'llm-coding-proxy.service').write_text(
                'TimeoutStartSec=35min\n'
            )

            config = {
                'LOCAL_PROXY_PORT': '19000',
                'LOCAL_TUNNEL_PORT': '19001',
                'RUNPOD_START_TIMEOUT_SECONDS': '10',
                'VLLM_START_TIMEOUT_SECONDS': '20',
            }
            systemctl = MagicMock()
            systemctl.return_value.returncode = 0

            with (
                patch.dict(os.environ, {'XDG_CONFIG_HOME': str(config_home)}),
                patch.object(sys, 'argv', [str(bin_dir / 'llm-up')]),
                patch.object(runtime_module, '_systemctl', systemctl),
            ):
                runtime_module.ensure_socket(config)

            self.assertIn(
                'TimeoutStartSec=150s',
                (unit_dir / 'llm-coding-proxy.service').read_text(),
            )


class TestAcpRegistration(unittest.TestCase):
    def test_preserves_existing_agents_and_registers_opencode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            acp_file = home / '.jetbrains' / 'acp.json'
            acp_file.parent.mkdir()
            acp_file.write_text(
                json.dumps(
                    {'agent_servers': {'Keep Me': {'command': 'other-agent'}}}
                )
            )
            with (
                patch.object(runtime_module.Path, 'home', return_value=home),
                patch.object(sys, 'argv', ['/opt/llm-coding/bin/llm-runtime']),
            ):
                runtime_module._ensure_acp_registration({})

            acp = json.loads(acp_file.read_text())
            self.assertEqual(acp_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                acp['agent_servers']['Keep Me']['command'], 'other-agent'
            )
            self.assertEqual(
                acp['agent_servers']['OpenCode RunPod'],
                {
                    'command': '/opt/llm-coding/bin/opencode-runpod',
                    'args': ['acp'],
                },
            )

    def test_runtime_up_registers_acp_before_other_setup(self):
        manager = MagicMock()
        manager.load_config.return_value = {'RUNPOD_SSH_KEY': '/tmp/test-key'}
        manager.validate_config.return_value = True
        with (
            patch.object(
                runtime_module, 'ConfigManager', return_value=manager
            ),
            patch.object(runtime_module, '_clear_activation_failure'),
            patch.object(
                runtime_module, '_ensure_acp_registration'
            ) as register,
        ):
            with self.assertRaisesRegex(
                RuntimeError, 'SSH public key not found'
            ):
                runtime_module.up()

        register.assert_called_once_with({'RUNPOD_SSH_KEY': '/tmp/test-key'})


class TestRuntimeReadySummary(unittest.TestCase):
    def test_reports_verified_runtime_and_integrations(self):
        response = MagicMock()
        response.json.return_value = {'data': [{'id': 'test-model'}]}
        pod = {'name': 'test-pod', 'desiredStatus': 'RUNNING', 'id': 'pod-1'}
        config = {
            'SERVED_MODEL_NAME': 'test-model',
            'LOCAL_PROXY_PORT': '18000',
            'LOCAL_TUNNEL_PORT': '18001',
            'RUNPOD_API_KEY': 'test-key',
            'RUNPOD_POD_NAME': 'test-pod',
        }
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(cli_module.requests, 'get', return_value=response),
            patch.object(
                cli_module,
                'get_systemd_service_info',
                return_value=('active', 'id'),
            ),
            patch.object(
                cli_module.RunPodClient, 'find_pod_by_name', return_value=pod
            ),
            patch.object(
                cli_module,
                'create_opencode_config',
                return_value=Path(temp_dir) / 'opencode.json',
            ),
            patch('builtins.print') as print_mock,
        ):
            cli_module.print_runtime_ready_summary(
                config, Path(temp_dir), {'data': [{'id': 'test-model'}]}
            )

        output = '\n'.join(
            str(call.args[0]) for call in print_mock.call_args_list
        )
        self.assertIn('RunPod pod:     test-pod (RUNNING, id pod-1)', output)
        self.assertIn('SSH tunnel:     127.0.0.1:18001 (active)', output)
        self.assertIn('CLion ACP:', output)


class TestActivationFailureDisplay(unittest.TestCase):
    def test_prints_recorded_actionable_error_without_journal(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(sys, 'stderr', new_callable=io.StringIO) as stderr,
        ):
            Path(temp_dir, 'runtime.activation-error').write_text(
                'No GPU capacity. Retry later.'
            )
            displayed = cli_module.print_activation_failure(Path(temp_dir))

        self.assertTrue(displayed)
        self.assertIn('No GPU capacity. Retry later.', stderr.getvalue())
        self.assertNotIn('journalctl', stderr.getvalue())

    def test_reads_latest_activation_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, 'runtime.activation-status').write_text(
                'Waiting for RunPod SSH\n'
            )
            self.assertEqual(
                cli_module.read_activation_status(Path(temp_dir)),
                'Waiting for RunPod SSH',
            )


class TestLlmUp(unittest.TestCase):
    def test_starts_socket_request_before_waiting_for_activation(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(cli_module, 'check_dependencies'),
            patch.object(cli_module, 'ensure_socket'),
            patch.object(cli_module, 'ConfigManager') as config_manager_class,
            patch.object(cli_module, 'print_runtime_ready_summary'),
            patch.object(cli_module.subprocess, 'Popen') as popen,
        ):
            config_manager = config_manager_class.return_value
            config_manager.load_config.return_value = {
                'RUNPOD_API_KEY': 'test-key',
                'RUNPOD_SSH_KEY': '/tmp/test-key',
                'RUNPOD_POD_NAME': 'test-pod',
            }
            config_manager.validate_config.return_value = True
            config_manager.state_dir = Path(temp_dir)

            request = popen.return_value
            request.poll.return_value = 0
            request.communicate.return_value = ('{"data": []}', '')
            request.returncode = 0

            cli_module.llm_up.callback()

        command = popen.call_args.args[0]
        self.assertEqual(command[0], 'curl')
        self.assertEqual(command[-1], 'http://127.0.0.1:18000/v1/models')

    def test_recovers_active_proxy_when_tunnel_is_unavailable(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(cli_module, 'check_dependencies'),
            patch.object(cli_module, 'ensure_socket'),
            patch.object(cli_module, 'ConfigManager') as config_manager_class,
            patch.object(
                cli_module,
                'get_systemd_service_info',
                side_effect=[('active', 'old'), ('active', 'old')],
            ),
            patch.object(
                cli_module.requests,
                'get',
                side_effect=cli_module.requests.ConnectionError,
            ),
            patch.object(cli_module, 'run_command') as run_command,
            patch.object(cli_module, 'print_runtime_ready_summary'),
            patch.object(cli_module.subprocess, 'Popen') as popen,
        ):
            config_manager = config_manager_class.return_value
            config_manager.load_config.return_value = {
                'RUNPOD_API_KEY': 'test-key',
                'RUNPOD_SSH_KEY': '/tmp/test-key',
                'RUNPOD_POD_NAME': 'test-pod',
            }
            config_manager.validate_config.return_value = True
            config_manager.state_dir = Path(temp_dir)

            run_command.return_value.returncode = 0
            request = popen.return_value
            request.poll.return_value = 0
            request.communicate.return_value = ('{"data": []}', '')
            request.returncode = 0

            cli_module.llm_up.callback()

        self.assertEqual(
            run_command.call_args.args[0],
            ['systemctl', '--user', 'restart', 'llm-coding-proxy.service'],
        )

    def test_llm_down_stops_proxy_before_stopping_runtime(self):
        with (
            patch.object(
                cli_module,
                '_shutdown_summary_items',
                return_value=(
                    ['socket listener', 'local proxy'],
                    ['runtime.env'],
                ),
            ),
            patch.object(cli_module, 'run_command') as run_command,
            patch.object(cli_module, 'runtime_down') as runtime_down,
            patch('builtins.print') as print_mock,
        ):
            run_command.return_value.returncode = 0
            cli_module.llm_down.callback(remove_integration=False)

        self.assertEqual(
            run_command.call_args_list[1].args[0],
            ['systemctl', '--user', 'stop', 'llm-coding-proxy.service'],
        )
        runtime_down.assert_called_once()
        self.assertEqual(
            [call.args[0] for call in print_mock.call_args_list],
            [
                'Stopped: socket listener',
                'Stopped: local proxy',
                'Removed: runtime.env',
            ],
        )


class TestRuntimeDown(unittest.TestCase):
    def test_idle_down_retains_integration_and_pod_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_dir = root / 'state'
            state_dir.mkdir()
            (state_dir / 'runtime.env').write_text('SSH_HOST=example\n')
            (state_dir / 'opencode.json').write_text('{}\n')
            (state_dir / 'runtime.pod-id').write_text('pod-1\n')
            home = root / 'home'
            acp_file = home / '.jetbrains' / 'acp.json'
            acp_file.parent.mkdir(parents=True)
            acp_file.write_text(
                json.dumps(
                    {
                        'agent_servers': {
                            'OpenCode RunPod': {'command': 'opencode-runpod'},
                            'Keep Me': {'command': 'other-agent'},
                        }
                    }
                )
            )
            manager = MagicMock()
            manager.state_dir = state_dir
            manager.load_config.return_value = {
                'RUNPOD_API_KEY': 'test-key',
                'RUNPOD_POD_NAME': 'test-pod',
                'JETBRAINS_AGENT_NAME': 'OpenCode RunPod',
            }
            systemctl = MagicMock()
            systemctl.return_value.returncode = 0
            client = MagicMock()
            client.get_pod.return_value = {
                'id': 'pod-1',
                'desiredStatus': 'RUNNING',
            }
            with (
                patch.object(
                    runtime_module, 'ConfigManager', return_value=manager
                ),
                patch.object(runtime_module, '_systemctl', systemctl),
                patch.object(runtime_module.Path, 'home', return_value=home),
                patch.object(
                    runtime_module, 'RunPodClient', return_value=client
                ),
            ):
                runtime_module.down()

            systemctl.assert_called_once_with(
                'stop', 'llm-coding-tunnel.service', check=False
            )
            client.stop_pod.assert_called_once_with('pod-1')
            self.assertFalse((state_dir / 'runtime.env').exists())
            self.assertTrue((state_dir / 'opencode.json').exists())
            self.assertEqual(
                (state_dir / 'runtime.pod-id').read_text(), 'pod-1\n'
            )
            servers = json.loads(acp_file.read_text())['agent_servers']
            self.assertIn('OpenCode RunPod', servers)
            self.assertIn('Keep Me', servers)

    def test_remove_integration_deletes_only_project_registration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_dir = root / 'state'
            state_dir.mkdir()
            (state_dir / 'opencode.json').write_text('{}\n')
            (state_dir / 'runtime.pod-id').write_text('pod-1\n')
            home = root / 'home'
            acp_file = home / '.jetbrains' / 'acp.json'
            acp_file.parent.mkdir(parents=True)
            acp_file.write_text(
                json.dumps(
                    {
                        'agent_servers': {
                            'OpenCode RunPod': {'command': 'managed'},
                            'Keep Me': {'command': 'unrelated'},
                        }
                    }
                )
            )
            manager = MagicMock()
            manager.state_dir = state_dir
            manager.load_config.return_value = {
                'JETBRAINS_AGENT_NAME': 'OpenCode RunPod'
            }
            with (
                patch.object(
                    runtime_module, 'ConfigManager', return_value=manager
                ),
                patch.object(runtime_module.Path, 'home', return_value=home),
            ):
                runtime_module.remove_integration()

            self.assertFalse((state_dir / 'opencode.json').exists())
            self.assertTrue((state_dir / 'runtime.pod-id').exists())
            servers = json.loads(acp_file.read_text())['agent_servers']
            self.assertNotIn('OpenCode RunPod', servers)
            self.assertIn('Keep Me', servers)

    def test_reports_pod_failure_after_local_cleanup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_dir = root / 'state'
            state_dir.mkdir()
            (state_dir / 'opencode.json').write_text('{}\n')
            manager = MagicMock()
            manager.state_dir = state_dir
            manager.load_config.return_value = {
                'RUNPOD_API_KEY': 'test-key',
                'RUNPOD_POD_NAME': 'test-pod',
            }
            systemctl = MagicMock()
            systemctl.return_value.returncode = 0
            with (
                patch.object(
                    runtime_module, 'ConfigManager', return_value=manager
                ),
                patch.object(runtime_module, '_systemctl', systemctl),
                patch.object(
                    runtime_module.Path, 'home', return_value=root / 'home'
                ),
                patch.object(
                    runtime_module.RunPodClient,
                    'find_pod_by_name',
                    side_effect=RuntimeError('API unavailable'),
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    r'Shutdown incomplete: could not stop RunPod pod test-pod',
                ):
                    runtime_module.down()

            self.assertTrue((state_dir / 'opencode.json').exists())


if __name__ == '__main__':
    unittest.main()
