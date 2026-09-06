"""Tests for validation of the vLLM model-list protocol."""

# ruff: noqa: PT009

import unittest
from collections.abc import Callable

from llm_coding.vllm import VLLMProtocolError, parse_model_ids


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


if __name__ == '__main__':
    unittest.main()
