"""Status aggregation contracts."""

# ruff: noqa: PT009

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from llm_coding.config import Settings
from llm_coding.runpod import RunPodAPIError, RunPodProtocolError
from llm_coding.status import (
    EndpointState,
    ProviderState,
    _inspect_endpoint,
    inspect_provider,
)


def _settings() -> Settings:
    return Settings(
        runpod_api_key='key',
        runpod_ssh_key=Path('/key'),
        runpod_pod_name='model',
        runpod_image='image',
        runpod_gpu_type='gpu',
    )


class TestProviderStatus(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_absent_pod_selection(self) -> None:
        client = MagicMock()
        client.get_pods.return_value = []
        result = inspect_provider(_settings(), self.state_dir, client)
        self.assertEqual(result.state, ProviderState.NO_SELECTED_POD)

    def test_selected_pod_no_longer_exists(self) -> None:
        (self.state_dir / 'runtime.pod-id').write_text('gone\n')
        client = MagicMock()
        client.get_pod.side_effect = RunPodAPIError('missing', 404)
        result = inspect_provider(_settings(), self.state_dir, client)
        self.assertEqual(result.state, ProviderState.SELECTED_POD_MISSING)
        self.assertEqual(result.pod_id, 'gone')

    def test_ambiguous_legacy_name(self) -> None:
        client = MagicMock()
        client.get_pods.return_value = [
            {'id': 'one', 'name': 'model', 'desiredStatus': 'EXITED'},
            {'id': 'two', 'name': 'model', 'desiredStatus': 'RUNNING'},
        ]
        result = inspect_provider(_settings(), self.state_dir, client)
        self.assertEqual(result.state, ProviderState.AMBIGUOUS_LEGACY_NAME)

    def test_http_and_protocol_failures_remain_distinct(self) -> None:
        cases = (
            (
                RunPodAPIError('unavailable', 503),
                ProviderState.API_UNAVAILABLE,
            ),
            (
                RunPodProtocolError('bad payload'),
                ProviderState.PROTOCOL_FAILURE,
            ),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                client = MagicMock()
                client.get_pods.side_effect = error
                result = inspect_provider(_settings(), self.state_dir, client)
                self.assertEqual(result.state, expected)

    def test_each_provider_lifecycle_is_preserved(self) -> None:
        (self.state_dir / 'runtime.pod-id').write_text('selected\n')
        for lifecycle in ('RUNNING', 'EXITED', 'CREATED', 'STARTING'):
            with self.subTest(lifecycle=lifecycle):
                client = MagicMock()
                client.get_pod.return_value = {
                    'id': 'selected',
                    'name': 'model',
                    'desiredStatus': lifecycle,
                }
                result = inspect_provider(_settings(), self.state_dir, client)
                self.assertEqual(result.state, ProviderState.AVAILABLE)
                self.assertEqual(result.lifecycle, lifecycle)


class TestEndpointStatus(unittest.TestCase):
    def test_http_errors_are_never_healthy(self) -> None:
        for status_code in (400, 503):
            for body_kind in ('json', 'non-json'):
                with self.subTest(
                    status_code=status_code, body_kind=body_kind
                ):
                    response = MagicMock()
                    response.status_code = status_code
                    response.text = (
                        '{"data": [{"id": "model"}]}'
                        if body_kind == 'json'
                        else 'service unavailable'
                    )
                    response.raise_for_status.side_effect = requests.HTTPError(
                        str(status_code)
                    )
                    with patch(
                        'llm_coding.status.requests.get',
                        return_value=response,
                    ):
                        result = _inspect_endpoint(18001)

                    self.assertEqual(result, EndpointState.UNREACHABLE)
                    response.raise_for_status.assert_called_once_with()
