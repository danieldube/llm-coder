#!/usr/bin/env python3
"""Regression tests for focused runtime services and injected dependencies."""

# ruff: noqa: PT009

import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from llm_coding.config import Settings
from llm_coding.opencode import create_config, ensure_acp_registration
from llm_coding.runpod import pod_create_body
from llm_coding.ssh import command
from llm_coding.state import FileStateStore
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
            self.assertFalse(path.with_suffix('.pod-id.tmp').exists())

    def test_ssh_uses_private_known_hosts_and_accept_new(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runner = MagicMock(return_value=SimpleNamespace(returncode=0))
            args = command(settings(root), root, 'host', 22022, runner)
            self.assertIn('StrictHostKeyChecking=accept-new', args)
            known_hosts = root / 'known_hosts'
            self.assertEqual(stat.S_IMODE(known_hosts.stat().st_mode), 0o600)
            runner.assert_called_once()

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
