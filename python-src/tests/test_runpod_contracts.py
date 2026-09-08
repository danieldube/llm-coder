"""Contract and persisted-identity tests for the RunPod runtime."""

# ruff: noqa: PT009, PT027

import stat
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import requests
from llm_coding import runtime
from llm_coding.config import Settings
from llm_coding.runpod import RunPodAPIError, RunPodClient, RunPodProtocolError
from llm_coding.ssh import prepare_endpoint
from llm_coding.state import FileStateStore


def _pod(pod_id: str = 'pod-1', name: str = 'model') -> dict[str, str]:
    return {'id': pod_id, 'name': name, 'desiredStatus': 'RUNNING'}


def _settings(root: Path) -> Settings:
    return Settings(
        runpod_api_key='key',
        runpod_ssh_key=root / 'id_ed25519',
        runpod_pod_name='model',
        runpod_image='image',
        runpod_gpu_type='gpu',
    )


class TestRunPodContracts(unittest.TestCase):
    def _response(self, payload: object) -> MagicMock:
        response = MagicMock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def test_get_pods_accepts_direct_list_and_empty_list(self) -> None:
        with patch(
            'llm_coding.runpod.requests.request',
            side_effect=[self._response([_pod()]), self._response([])],
        ):
            client = RunPodClient('key')
            self.assertEqual(client.get_pods(), [_pod()])
            self.assertEqual(client.get_pods(), [])

    def test_get_pods_supports_only_explicit_legacy_envelope(self) -> None:
        with patch(
            'llm_coding.runpod.requests.request',
            return_value=self._response({'data': [_pod()]}),
        ):
            self.assertEqual(RunPodClient('key').get_pods(), [_pod()])

    def test_get_pods_rejects_malformed_item_and_wrong_top_level(self) -> None:
        malformed_payloads: tuple[object, ...] = (
            [{'id': 'pod-1'}],
            {'pods': []},
        )
        for payload in malformed_payloads:
            with (
                self.subTest(payload=payload),
                patch(
                    'llm_coding.runpod.requests.request',
                    return_value=self._response(payload),
                ),
            ):
                with self.assertRaises(RunPodProtocolError):
                    RunPodClient('key').get_pods()

    def test_invalid_json_is_not_translated_to_an_empty_result(self) -> None:
        response = self._response(None)
        response.json.side_effect = ValueError('bad JSON')
        with patch(
            'llm_coding.runpod.requests.request', return_value=response
        ):
            with self.assertRaisesRegex(RunPodProtocolError, 'invalid JSON'):
                RunPodClient('key').get_pods()

    def test_http_error_remains_distinct_from_protocol_error(self) -> None:
        response = self._response(None)
        response.status_code = 503
        response.text = 'unavailable'
        response.raise_for_status.side_effect = requests.HTTPError('503')
        with patch(
            'llm_coding.runpod.requests.request', return_value=response
        ):
            with self.assertRaises(RunPodAPIError) as raised:
                RunPodClient('key').get_pods()
        self.assertEqual(raised.exception.status_code, 503)

    def test_object_endpoints_validate_their_specific_contracts(self) -> None:
        client = RunPodClient('key')
        endpoint_cases: tuple[tuple[Callable[[], object], object], ...] = (
            (lambda: client.create_pod({}), []),
            (lambda: client.get_pod('pod-1'), [_pod()]),
            (lambda: client.start_pod('pod-1'), {}),
            (lambda: client.stop_pod('pod-1'), {'id': 'another'}),
        )
        for method, payload in endpoint_cases:
            with (
                self.subTest(method=method),
                patch(
                    'llm_coding.runpod.requests.request',
                    return_value=self._response(payload),
                ),
            ):
                with self.assertRaises(RunPodProtocolError):
                    method()

    def test_ssh_endpoint_waits_for_incomplete_mapping(self) -> None:
        pending: tuple[object, ...] = (None, {}, {'22': None})
        for mappings in pending:
            pod: dict[str, object] = {
                **_pod(),
                'publicIp': '203.0.113.1',
                'portMappings': mappings,
            }
            with (
                self.subTest(port_mappings=mappings),
                patch(
                    'llm_coding.runpod.requests.request',
                    return_value=self._response(pod),
                ),
            ):
                self.assertIsNone(
                    RunPodClient('key').get_pod_endpoint('pod-1')
                )

    def test_ssh_endpoint_rejects_malformed_port_mappings(self) -> None:
        malformed: tuple[object, ...] = (
            [],
            '22',
            {'22': []},
            {'22': '22022'},
            {'22': True},
        )
        for mappings in malformed:
            pod: dict[str, object] = {
                **_pod(),
                'publicIp': '203.0.113.1',
            }
            pod['portMappings'] = mappings
            with (
                self.subTest(port_mappings=mappings),
                patch(
                    'llm_coding.runpod.requests.request',
                    return_value=self._response(pod),
                ),
                self.assertRaises(RunPodProtocolError),
            ):
                RunPodClient('key').get_pod_endpoint('pod-1')

    def test_ssh_endpoint_waits_for_missing_ip_when_running(self) -> None:
        pod: dict[str, object] = {
            **_pod(),
            'publicIp': None,
            'portMappings': {'22': 22022},
        }
        with patch(
            'llm_coding.runpod.requests.request',
            return_value=self._response(pod),
        ):
            self.assertIsNone(RunPodClient('key').get_pod_endpoint('pod-1'))

    def test_ssh_endpoint_requires_a_non_empty_string_host(self) -> None:
        invalid_hosts: tuple[object, ...] = ('', [], True)
        for host in invalid_hosts:
            pod: dict[str, object] = {
                **_pod(),
                'publicIp': host,
                'portMappings': {'22': 22022},
            }
            with (
                self.subTest(host=host),
                patch(
                    'llm_coding.runpod.requests.request',
                    return_value=self._response(pod),
                ),
                self.assertRaises(RunPodProtocolError),
            ):
                RunPodClient('key').get_pod_endpoint('pod-1')

    def test_ssh_endpoint_rejects_out_of_range_ports(self) -> None:
        for port in (0, 65536, -1):
            pod: dict[str, object] = {
                **_pod(),
                'publicIp': '203.0.113.1',
                'portMappings': {'22': port},
            }
            with (
                self.subTest(port=port),
                patch(
                    'llm_coding.runpod.requests.request',
                    return_value=self._response(pod),
                ),
                self.assertRaises(RunPodProtocolError),
            ):
                RunPodClient('key').get_pod_endpoint('pod-1')


class TestPersistedPodIdentity(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.manager = MagicMock()
        self.manager.state_dir = Path(self.temporary.name)
        self.state = FileStateStore(self.manager.state_dir)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_repeated_selection_uses_id_without_name_lookup(self) -> None:
        client = MagicMock()
        client.find_pod_by_name.return_value = _pod()
        self.assertEqual(
            runtime._select_pod(self.state, client, 'model', MagicMock()),
            _pod(),
        )
        client.get_pod.return_value = _pod()
        self.assertEqual(
            runtime._select_pod(self.state, client, 'model', MagicMock()),
            _pod(),
        )
        client.find_pod_by_name.assert_called_once_with('model')
        self.assertEqual(client.get_pod.call_count, 1)
        state = self.manager.state_dir / 'runtime.pod-id'
        self.assertEqual(state.read_text(), 'pod-1\n')
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)

    def test_ambiguous_initial_adoption_fails_closed(self) -> None:
        client = MagicMock()
        client.find_pod_by_name.side_effect = ValueError('ambiguous')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            runtime._select_pod(self.state, client, 'model', MagicMock())
        self.assertFalse((self.manager.state_dir / 'runtime.pod-id').exists())

    def test_failed_atomic_write_removes_temporary_state(self) -> None:
        with (
            patch(
                'llm_coding.state.os.replace',
                side_effect=OSError('disk error'),
            ),
            self.assertRaisesRegex(RuntimeError, 'Cannot persist'),
        ):
            self.state.write_pod_id('pod-1')

        self.assertEqual(
            list(self.manager.state_dir.glob('.runtime.pod-id.*.tmp')), []
        )

    def test_api_or_protocol_failure_does_not_fall_back_to_name(self) -> None:
        self.state.write_pod_id('pod-1')
        for error in (
            RunPodAPIError('unavailable', 503),
            RunPodProtocolError('wrong shape'),
        ):
            client = MagicMock()
            client.get_pod.side_effect = error
            with self.subTest(error=error), self.assertRaises(type(error)):
                runtime._select_pod(self.state, client, 'model', MagicMock())
            client.find_pod_by_name.assert_not_called()

    def test_missing_persisted_pod_allows_deliberate_replacement(self) -> None:
        self.state.write_pod_id('gone')
        runner = MagicMock(return_value=SimpleNamespace(returncode=0))
        prepare_endpoint(
            self.manager.state_dir,
            'gone',
            'host-a',
            22,
            runner,
            MagicMock(),
        )
        replacement = _pod('replacement')
        client = MagicMock()
        client.get_pod.side_effect = RunPodAPIError('not found', 404)
        client.find_pod_by_name.return_value = replacement
        self.assertEqual(
            runtime._select_pod(self.state, client, 'model', runner),
            replacement,
        )
        self.assertEqual(
            (self.manager.state_dir / 'runtime.pod-id').read_text(),
            'replacement\n',
        )
        self.assertFalse(
            (self.manager.state_dir / 'runtime.ssh-endpoint.json').exists()
        )

    def test_replacement_clears_stale_endpoint_after_old_pod_404(self) -> None:
        runner = MagicMock(return_value=SimpleNamespace(returncode=0))
        prepare_endpoint(
            self.manager.state_dir,
            'deleted-pod',
            'host-a',
            22,
            runner,
            MagicMock(),
        )
        client = MagicMock()
        client.get_pod.side_effect = RunPodAPIError('not found', 404)
        runtime._forget_endpoint_after_confirmed_replacement(
            self.state, client, 'replacement', runner
        )
        self.assertFalse(
            (self.manager.state_dir / 'runtime.ssh-endpoint.json').exists()
        )
        client.get_pod.assert_called_once_with('deleted-pod')

    def test_replacement_clears_chained_stale_pod_state(self) -> None:
        self.state.write_pod_id('first-deleted-pod')
        runner = MagicMock(return_value=SimpleNamespace(returncode=0))
        prepare_endpoint(
            self.manager.state_dir,
            'second-deleted-pod',
            'host-a',
            22,
            runner,
            MagicMock(),
        )
        client = MagicMock()
        client.get_pod.side_effect = [
            RunPodAPIError('not found', 404),
            RunPodAPIError('not found', 404),
        ]
        client.find_pod_by_name.return_value = _pod('replacement')
        self.assertEqual(
            runtime._select_pod(self.state, client, 'model', runner),
            _pod('replacement'),
        )
        self.assertEqual(
            (self.manager.state_dir / 'runtime.pod-id').read_text(),
            'replacement\n',
        )
        self.assertFalse(
            (self.manager.state_dir / 'runtime.ssh-endpoint.json').exists()
        )
        self.assertEqual(
            client.get_pod.call_args_list,
            [
                call('first-deleted-pod'),
                call('second-deleted-pod'),
            ],
        )

    def test_down_stops_persisted_pod_and_retains_identity(self) -> None:
        self.state.write_pod_id('pod-1')
        self.manager.load_settings.return_value = _settings(
            self.manager.state_dir
        )
        client = MagicMock()
        client.get_pod.return_value = _pod()
        systemctl = MagicMock()
        systemctl.return_value.returncode = 0
        with (
            patch.object(runtime, 'ConfigManager', return_value=self.manager),
            patch.object(runtime, 'RunPodClient', return_value=client),
            patch(
                'llm_coding.runtime.Path.home',
                return_value=self.manager.state_dir / 'home',
            ),
        ):
            runtime.down(
                runtime.RuntimeDependencies(
                    systemd=SimpleNamespace(run=systemctl)
                ),
                provider=client,
            )
        client.get_pod.assert_called_once_with('pod-1')
        client.find_pod_by_name.assert_not_called()
        client.stop_pod.assert_called_once_with('pod-1')
        self.assertEqual(
            (self.manager.state_dir / 'runtime.pod-id').read_text(),
            'pod-1\n',
        )


if __name__ == '__main__':
    unittest.main()
