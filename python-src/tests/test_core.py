#!/usr/bin/env python3
"""Regression tests for focused runtime services and injected dependencies."""

# ruff: noqa: PT009

import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

from llm_coding import runtime
from llm_coding.config import ConfigurationError, Settings
from llm_coding.core import LegacySettingsAdapter, create_opencode_config
from llm_coding.opencode import (
    create_config,
    ensure_acp_registration,
    launch,
    remove_acp_registration,
)
from llm_coding.runpod import RunPodAPIError, pod_create_body
from llm_coding.ssh import (
    command,
    forget_endpoint_for_deleted_pod,
    is_host_key_mismatch,
    prepare_endpoint,
    public_key_path,
)
from llm_coding.state import FileStateStore, atomic_write_private
from llm_coding.systemd import escape_unit_argument, render_units


def settings(root: Path) -> Settings:
    return Settings(
        runpod_api_key='secret',
        runpod_ssh_key=root / 'id_ed25519',
        runpod_pod_name='model',
        runpod_image='image-repository:latest',
        runpod_gpu_type='NVIDIA L40S',
        runpod_image_repository='image-repository',
        model='qwen3-coder-30b-a3b-fp8',
        opencode_version='1',
        vllm_version='0.28.0',
        vllm_cuda_version='129',
        model_id='Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8',
        model_revision='e8ab3f2db9e388999a004eea5a31c16a8b517bc0',
        served_model_name='qwen3-coder',
        model_display_name='Qwen3-Coder 30B A3B FP8',
    )


class FocusedModuleTests(unittest.TestCase):
    def test_remote_startup_error_uses_only_known_marker(self) -> None:
        error = subprocess.CalledProcessError(
            1,
            ['ssh', 'secret-argument'],
            stderr='untrusted output\nLLM_CODING_REMOTE_FAILURE=no_cuda\n',
        )
        message = str(runtime._remote_startup_error(error))
        self.assertIn('cannot access a CUDA device', message)
        self.assertNotIn('untrusted output', message)
        self.assertNotIn('secret-argument', message)

    def test_remote_startup_error_uses_safe_fallback(self) -> None:
        error = subprocess.CalledProcessError(
            1, ['ssh', 'secret-argument'], stderr='unexpected output'
        )
        message = str(runtime._remote_startup_error(error))
        self.assertIn('without a diagnostic code', message)
        self.assertNotIn('unexpected output', message)
        self.assertNotIn('secret-argument', message)

    def test_legacy_settings_adapter_validates_and_constructs_settings(
        self,
    ) -> None:
        legacy = {
            field.upper(): str(value).lower()
            if isinstance(value, bool)
            else str(value)
            for field, value in vars(settings(Path('/keys'))).items()
        }

        adapted = LegacySettingsAdapter(legacy).to_settings()

        self.assertEqual(adapted, settings(Path('/keys')))

    def test_legacy_settings_adapter_rejects_non_string_values(self) -> None:
        legacy = cast(dict[str, str], {'RUNPOD_API_KEY': 42})
        try:
            LegacySettingsAdapter(legacy).to_settings()
        except ConfigurationError as exc:
            self.assertIn('keys and values must be strings', str(exc))
        else:
            self.fail('Expected invalid legacy mapping to be rejected')

    def test_core_mapping_entrypoint_is_deprecated(self) -> None:
        legacy = {
            field.upper(): str(value).lower()
            if isinstance(value, bool)
            else str(value)
            for field, value in vars(settings(Path('/keys'))).items()
        }
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertWarns(DeprecationWarning),
        ):
            path = create_opencode_config(legacy, Path(temporary))
            self.assertTrue(path.is_file())

    def test_public_key_path_appends_pub_to_complete_filename(self) -> None:
        cases = {
            Path('/keys/id_ed25519'): Path('/keys/id_ed25519.pub'),
            Path('/keys/id_ed25519.pem'): Path('/keys/id_ed25519.pem.pub'),
            Path('/keys/team.user.key'): Path('/keys/team.user.key.pub'),
            Path('/keys/.identity'): Path('/keys/.identity.pub'),
        }

        for private_key, expected in cases.items():
            with self.subTest(private_key=private_key):
                self.assertEqual(public_key_path(private_key), expected)

    def test_opencode_reports_failed_background_prewarm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            binary = home / '.opencode/bin/opencode'
            binary.parent.mkdir(parents=True)
            binary.touch()
            prewarm = MagicMock(returncode=1)
            prewarm.communicate.return_value = ('', 'capacity diagnostic\n')
            thread = MagicMock()

            def run_reporter() -> None:
                reporter = thread.call_args.kwargs['target']
                reporter()

            thread.return_value.start.side_effect = run_reporter
            stderr = StringIO()
            with (
                patch('llm_coding.opencode.Path.home', return_value=home),
                patch(
                    'llm_coding.opencode.subprocess.Popen',
                    return_value=prewarm,
                ),
                patch('llm_coding.opencode.run_command') as run,
                patch('llm_coding.opencode.threading.Thread', thread),
                redirect_stderr(stderr),
            ):
                launch(settings(home), home, ['--help'])

        self.assertEqual(
            stderr.getvalue(),
            'OpenCode prewarm failed (exit code 1); run llm-up for details.\n',
        )
        self.assertNotIn('capacity diagnostic', stderr.getvalue())
        run.assert_called_once_with([str(binary), '--help'])

    def test_packaged_remote_launcher_is_read_with_package_and_name(
        self,
    ) -> None:
        self.assertIn(
            'Starting prebuilt vLLM',
            runtime._asset_text('remote', 'ensure-vllm.sh'),
        )

    def test_remote_vllm_arguments_include_launch_configuration(self) -> None:
        config = replace(
            settings(Path('/keys')),
            context_size=32768,
            vllm_tensor_parallel_size=1,
            vllm_kv_cache_dtype='fp8',
            vllm_enforce_eager=True,
            vllm_language_model_only=True,
            vllm_max_num_seqs=8,
            vllm_reasoning_parser='qwen3',
            vllm_tool_call_parser='qwen3_coder',
        )

        self.assertEqual(
            runtime._remote_vllm_arguments(config),
            (
                '0.28.0',
                '129',
                'Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8',
                'e8ab3f2db9e388999a004eea5a31c16a8b517bc0',
                'qwen3-coder',
                '32768',
                '0.92',
                '1',
                'fp8',
                'true',
                'true',
                '8',
                'qwen3',
                'qwen3_coder',
                '8000',
                '1800',
            ),
        )

    def test_create_pod_explains_only_known_capacity_failures(self) -> None:
        client = MagicMock()
        client.create_pod.side_effect = RunPodAPIError(
            'HTTP 500: create pod: There are no instances currently available',
            500,
        )
        try:
            runtime._create_pod_or_raise(
                client, settings(Path('/tmp')), 'ssh-ed25519 public'
            )
        except RuntimeError as exc:
            self.assertIn('RUNPOD_GPU_TYPE', str(exc))
            self.assertIn('RUNPOD_CLOUD_TYPE', str(exc))
        else:
            self.fail('Expected capacity failure')

        client.create_pod.side_effect = RunPodAPIError(
            'HTTP 500: internal', 500
        )
        try:
            runtime._create_pod_or_raise(
                client, settings(Path('/tmp')), 'ssh-ed25519 public'
            )
        except RunPodAPIError as exc:
            self.assertIn('internal', str(exc))
        else:
            self.fail('Expected provider error')

    def test_resume_pod_explains_capacity_without_renaming_pod(self) -> None:
        client = MagicMock()
        client.start_pod.side_effect = RuntimeError(
            'not enough free gpus on the host machine'
        )
        try:
            runtime._start_pod_or_raise(
                client, 'pod-1', settings(Path('/tmp'))
            )
        except RuntimeError as exc:
            self.assertIn('Retry llm-up later', str(exc))
            self.assertIn('deliberately replace the persisted pod', str(exc))
        else:
            self.fail('Expected resume capacity failure')

    def test_create_request_keeps_registry_secret_out_of_environment(
        self,
    ) -> None:
        config = settings(Path('/tmp'))
        body = pod_create_body(config, 'ssh-ed25519 public')
        self.assertEqual(body['env'], {'SSH_PUBLIC_KEY': 'ssh-ed25519 public'})
        self.assertNotIn('containerRegistryAuthId', body)
        self.assertEqual(body['gpuTypePriority'], 'availability')

    def test_state_store_writes_private_pod_identity_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = FileStateStore(Path(temporary))
            store.write_pod_id('pod-1')
            path = Path(temporary) / 'runtime.pod-id'
            self.assertEqual(store.read_pod_id(), 'pod-1')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.glob(f'.{path.name}.*.tmp')), [])

    def test_private_atomic_write_cleans_up_after_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'state'
            destination.write_text('previous\n')

            with patch(
                'llm_coding.state.os.write', side_effect=OSError('full')
            ):
                try:
                    atomic_write_private(destination, 'replacement\n')
                except OSError:
                    pass
                else:
                    self.fail('Expected write failure')

            self.assertEqual(destination.read_text(), 'previous\n')
            self.assertEqual(
                list(destination.parent.glob(f'.{destination.name}.*.tmp')),
                [],
            )

    def test_private_atomic_write_cleans_up_after_replace_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'state'
            destination.write_text('previous\n')

            with patch(
                'llm_coding.state.os.replace',
                side_effect=OSError('replace failed'),
            ):
                try:
                    atomic_write_private(destination, 'replacement\n')
                except OSError:
                    pass
                else:
                    self.fail('Expected replace failure')

            self.assertEqual(destination.read_text(), 'previous\n')
            self.assertEqual(
                list(destination.parent.glob(f'.{destination.name}.*.tmp')),
                [],
            )

    def test_private_atomic_write_uses_unique_temporary_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'state'
            sources: list[Path] = []
            replace = os.replace

            def record_replace(source: Path, target: Path) -> None:
                sources.append(Path(source))
                replace(source, target)

            with patch(
                'llm_coding.state.os.replace', side_effect=record_replace
            ):
                atomic_write_private(destination, 'first\n')
                atomic_write_private(destination, 'second\n')

            self.assertEqual(len(set(sources)), 2)
            self.assertTrue(
                all(path.parent == destination.parent for path in sources)
            )
            self.assertEqual(destination.read_text(), 'second\n')
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_ssh_uses_private_known_hosts_and_accept_new(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = command(settings(root), root, 'host', 22022)
            self.assertIn('StrictHostKeyChecking=accept-new', args)
            known_hosts = root / 'known_hosts'
            self.assertEqual(stat.S_IMODE(known_hosts.stat().st_mode), 0o600)

    def test_ssh_first_connection_records_pod_and_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MagicMock()
            audit = MagicMock()
            prepare_endpoint(root, 'pod-1', 'host-a', 22022, runner, audit)
            state = json.loads(
                (root / 'runtime.ssh-endpoint.json').read_text()
            )
            self.assertEqual(
                state, {'pod_id': 'pod-1', 'host': 'host-a', 'port': 22022}
            )
            self.assertEqual(
                stat.S_IMODE(
                    (root / 'runtime.ssh-endpoint.json').stat().st_mode
                ),
                0o600,
            )
            self.assertEqual(
                stat.S_IMODE((root / 'known_hosts').stat().st_mode), 0o600
            )
            runner.assert_not_called()
            audit.assert_called_once()

    def test_ssh_repeat_connection_preserves_host_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MagicMock()
            prepare_endpoint(root, 'pod-1', 'host-a', 22, runner, MagicMock())
            runner.reset_mock()
            prepare_endpoint(root, 'pod-1', 'host-a', 22, runner, MagicMock())
            runner.assert_not_called()

    def test_ssh_verified_endpoint_change_removes_only_old_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MagicMock(return_value=SimpleNamespace(returncode=0))
            prepare_endpoint(root, 'pod-1', 'host-a', 22, runner, MagicMock())
            audit = MagicMock()
            prepare_endpoint(root, 'pod-1', 'host-b', 22022, runner, audit)
            runner.assert_called_once_with(
                [
                    'ssh-keygen',
                    '-R',
                    '[host-a]:22',
                    '-f',
                    str(root / 'known_hosts'),
                ],
                check=False,
                capture_output=True,
            )
            self.assertIn('Rotating SSH endpoint', audit.call_args.args[0])

    def test_ssh_unchanged_endpoint_mismatch_fails_closed(self) -> None:
        diagnostic = '@ WARNING @ REMOTE HOST IDENTIFICATION HAS CHANGED!'
        self.assertTrue(is_host_key_mismatch(diagnostic))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MagicMock()
            prepare_endpoint(root, 'pod-1', 'host-a', 22, runner, MagicMock())
            prepare_endpoint(root, 'pod-1', 'host-a', 22, runner, MagicMock())
            runner.assert_not_called()

    def test_ssh_different_pod_identity_fails_without_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MagicMock()
            prepare_endpoint(root, 'pod-1', 'host-a', 22, runner, MagicMock())
            try:
                prepare_endpoint(
                    root, 'pod-2', 'host-b', 22, runner, MagicMock()
                )
            except RuntimeError as exc:
                self.assertIn(
                    'saved SSH trust record is for RunPod pod-1', str(exc)
                )
                self.assertIn('retry llm-up', str(exc))
            else:
                self.fail('Expected different pod identity to fail')
            runner.assert_not_called()

    def test_deleted_pod_clears_only_its_ssh_endpoint_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MagicMock(return_value=SimpleNamespace(returncode=0))
            prepare_endpoint(root, 'pod-1', 'host-a', 22, runner, MagicMock())
            forget_endpoint_for_deleted_pod(root, 'pod-1', runner)
            self.assertFalse((root / 'runtime.ssh-endpoint.json').exists())
            runner.assert_called_once_with(
                [
                    'ssh-keygen',
                    '-R',
                    '[host-a]:22',
                    '-f',
                    str(root / 'known_hosts'),
                ],
                check=False,
                capture_output=True,
            )

    def test_deleted_pod_refuses_to_clear_another_pod_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MagicMock()
            prepare_endpoint(root, 'pod-1', 'host-a', 22, runner, MagicMock())
            try:
                forget_endpoint_for_deleted_pod(root, 'pod-2', runner)
            except RuntimeError as exc:
                self.assertIn('belongs to RunPod pod-1', str(exc))
            else:
                self.fail('Expected mismatched Pod endpoint cleanup to fail')
            self.assertTrue((root / 'runtime.ssh-endpoint.json').exists())
            runner.assert_not_called()

    def test_systemd_units_bind_public_endpoint_to_loopback(self) -> None:
        with (
            patch(
                'llm_coding.systemd.runtime_command', return_value='runtime'
            ),
            patch('llm_coding.systemd.shutil.which', return_value='/proxy'),
            patch('llm_coding.systemd.Path.exists', return_value=True),
        ):
            units = render_units(settings(Path('/tmp')))
        self.assertIn(
            'ListenStream=127.0.0.1:18000', units['llm-coding.socket']
        )
        self.assertIn(
            'TimeoutStartSec=3120s', units['llm-coding-proxy.service']
        )

    def test_systemd_units_escape_executable_paths(self) -> None:
        cases = {
            '/opt/llm coding/runtime': '"/opt/llm coding/runtime"',
            '/opt/llm%coding/runtime': '"/opt/llm%%coding/runtime"',
            '/opt/llm\\coding/runtime': '"/opt/llm\\\\coding/runtime"',
            '/opt/llm"coding/runtime': '"/opt/llm\\"coding/runtime"',
        }

        for path, escaped in cases.items():
            with (
                self.subTest(path=path),
                patch('llm_coding.systemd.runtime_command', return_value=path),
                patch('llm_coding.systemd.shutil.which', return_value=path),
                patch('llm_coding.systemd.Path.exists', return_value=True),
            ):
                units = render_units(settings(Path('/tmp')))
            proxy = units['llm-coding-proxy.service']
            tunnel = units['llm-coding-tunnel.service']
            self.assertIn(f'ExecStartPre={escaped} up', proxy)
            self.assertIn(f'ExecStart={escaped} --exit-idle-time=', proxy)
            self.assertIn(f'ExecStopPost={escaped} down', proxy)
            self.assertIn(f'ExecStart={escaped} tunnel', tunnel)
            self.assertIn(
                'ListenStream=127.0.0.1:18000',
                units['llm-coding.socket'],
            )
            self.assertIn('127.0.0.1:18001', proxy)

    def test_systemd_argument_rejects_unrepresentable_paths(self) -> None:
        for path in ('', '/opt/llm\nruntime', '/opt/llm\0runtime'):
            with self.subTest(path=path):
                try:
                    escape_unit_argument(path)
                except ValueError:
                    pass
                else:
                    self.fail('Expected unrepresentable path to fail')

    def test_opencode_config_uses_local_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = create_config(settings(Path(temporary)), Path(temporary))
            rendered = json.loads(path.read_text())
        options = rendered['provider']['runpod']['options']
        self.assertEqual(options['baseURL'], 'http://127.0.0.1:18000/v1')

    def test_opencode_config_is_private_and_leaves_no_temporary_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = create_config(settings(root), root)

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(root.glob(f'.{path.name}.*.tmp')), [])

    def test_acp_registration_preserves_unrelated_agents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / '.jetbrains/acp.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'agent_servers': {'keep': {}}}))
            with patch('llm_coding.opencode.Path.home', return_value=home):
                ensure_acp_registration(settings(home))
            result = json.loads(path.read_text())
        self.assertIn('keep', result['agent_servers'])
        self.assertIn('OpenCode RunPod', result['agent_servers'])

    def test_acp_registration_failure_preserves_file_and_cleans_temporary(
        self,
    ) -> None:
        for operation in ('write', 'replace'):
            with (
                self.subTest(operation=operation),
                tempfile.TemporaryDirectory() as temporary,
            ):
                home = Path(temporary)
                path = home / '.jetbrains/acp.json'
                path.parent.mkdir()
                original = json.dumps({'agent_servers': {'keep': {}}})
                path.write_text(original)
                target = f'llm_coding.state.os.{operation}'
                with (
                    patch('llm_coding.opencode.Path.home', return_value=home),
                    patch(target, side_effect=OSError(f'{operation} failed')),
                ):
                    try:
                        ensure_acp_registration(settings(home))
                    except RuntimeError:
                        pass
                    else:
                        self.fail(f'Expected {operation} failure')

                self.assertEqual(path.read_text(), original)
                self.assertEqual(
                    list(path.parent.glob(f'.{path.name}.*.tmp')),
                    [],
                )

    def test_acp_registration_replaces_file_with_private_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / '.jetbrains/acp.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'agent_servers': {'keep': {}}}))
            path.chmod(0o644)

            with patch('llm_coding.opencode.Path.home', return_value=home):
                ensure_acp_registration(settings(home))

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.glob(f'.{path.name}.*.tmp')), [])

    def test_acp_removal_preserves_unrelated_entries_and_private_mode(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            path = home / '.jetbrains/acp.json'
            path.parent.mkdir()
            path.write_text(
                json.dumps(
                    {
                        'agent_servers': {
                            'keep': {'command': 'keep'},
                            'OpenCode RunPod': {'command': 'remove'},
                        },
                        'unrelated': {'value': True},
                    }
                )
            )
            path.chmod(0o644)

            with patch('llm_coding.opencode.Path.home', return_value=home):
                remove_acp_registration(settings(home))

            result = json.loads(path.read_text())
            self.assertEqual(
                result['agent_servers'], {'keep': {'command': 'keep'}}
            )
            self.assertEqual(result['unrelated'], {'value': True})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(path.parent.glob(f'.{path.name}.*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
