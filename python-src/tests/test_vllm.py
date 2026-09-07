"""Tests for validation of the vLLM model-list protocol."""

# ruff: noqa: PT009

import unittest
from collections.abc import Callable
from unittest.mock import MagicMock

import requests
from llm_coding.vllm import (
    VLLMProtocolError,
    VLLMStatusError,
    parse_model_ids,
    response_model_ids,
)


class VLLMResponseTests(unittest.TestCase):
    def assert_protocol_error(
        self, pattern: str, operation: Callable[[], object]
    ) -> None:
        try:
            operation()
        except VLLMProtocolError as exc:
            self.assertRegex(str(exc), pattern)
        else:
            self.fail('Expected malformed vLLM response to be rejected')

    def test_rejects_non_object_json(self) -> None:
        self.assert_protocol_error(
            'expected a JSON object', lambda: parse_model_ids([])
        )

    def test_rejects_non_list_data(self) -> None:
        self.assert_protocol_error(
            'field "data" must be a list',
            lambda: parse_model_ids({'data': {}}),
        )

    def test_rejects_malformed_list_item(self) -> None:
        self.assert_protocol_error(
            r'data\[1\] must be an object',
            lambda: parse_model_ids({'data': [{'id': 'expected'}, None]}),
        )

    def test_rejects_missing_model_id(self) -> None:
        self.assert_protocol_error(
            r'data\[0\]\.id must be a string',
            lambda: parse_model_ids({'data': [{}]}),
        )

    def test_accepts_valid_expected_model_response(self) -> None:
        self.assertEqual(
            parse_model_ids(
                {'data': [{'id': 'other'}, {'id': 'expected-model'}]}
            ),
            ('other', 'expected-model'),
        )

    def test_http_errors_are_rejected_before_json_parsing(self) -> None:
        for status_code in (400, 503):
            for body_kind in ('json', 'non-json'):
                with self.subTest(
                    status_code=status_code, body_kind=body_kind
                ):
                    response = MagicMock()
                    response.status_code = status_code
                    response.text = (
                        '{"data": [{"id": "expected-model"}]}'
                        if body_kind == 'json'
                        else 'service unavailable'
                    )
                    response.raise_for_status.side_effect = requests.HTTPError(
                        f'{status_code}: {response.text}'
                    )

                    try:
                        response_model_ids(response)
                    except VLLMStatusError as exc:
                        self.assertEqual(str(exc), f'HTTP {status_code}')
                    else:
                        self.fail('Expected HTTP error to be rejected')

                    response.raise_for_status.assert_called_once_with()
                    response.json.assert_not_called()


if __name__ == '__main__':
    unittest.main()
