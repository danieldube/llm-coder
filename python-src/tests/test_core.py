#!/usr/bin/env python3
"""Regression tests for focused runtime services and injected dependencies."""

# ruff: noqa: PT009

import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from llm_coding import runtime
from llm_coding.config import Settings
from llm_coding.core import RunPodAPIError
from llm_coding.opencode import create_config, ensure_acp_registration, launch
from llm_coding.runpod import pod_create_body
from llm_coding.ssh import (
    command,
    is_host_key_mismatch,
    prepare_endpoint,
    public_key_path,
)
from llm_coding.state import FileStateStore, atomic_write_private
from llm_coding.systemd import render_units


def settings(root: Path) -> Settings:
    return Settings(
        runpod_api_key='secret',
        runpod_ssh_key=root / 'id_ed25519',
        runpod_pod_name='model',
        runpod_image='image',
        runpod_gpu_type='gpu',
        opencode_version='1',
        vllm_version='1',
        vllm_cuda_version='124',
        model_id='model',
        served_model_name='served',
        model_display_name='Served Model',
    )


class FocusedModuleTests(unittest.TestCase):
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
                patch('llm_coding.opencode.subprocess.run') as run,
                patch('llm_coding.opencode.threading.Thread', thread),
                redirect_stderr(stderr),
            ):
                launch(settings(home), home, ['--help'])

        self.assertEqual(stderr.getvalue(), 'capacity diagnostic\n')
        run.assert_called_once_with([str(binary), '--help'], check=True)

    def test_packaged_remote_launcher_is_read_with_package_and_name(
        self,
    ) -> None:
        self.assertIn(
            'Starting prebuilt vLLM',
            runtime._asset_text('remote', 'ensure-vllm.sh'),
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
                self.assertIn('not expected pod pod-2', str(exc))
            else:
                self.fail('Expected different pod identity to fail')
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

    def test_opencode_config_uses_local_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = create_config(settings(Path(temporary)), Path(temporary))
            rendered = json.loads(path.read_text())
        options = rendered['provider']['runpod']['options']
        self.assertEqual(options['baseURL'], 'http://127.0.0.1:18000/v1')

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


if __name__ == '__main__':
    unittest.main()
