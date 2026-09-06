"""Tests for validation of the vLLM model-list protocol."""

# ruff: noqa: PT009

import unittest

from llm_coding.vllm import VLLMProtocolError, parse_model_ids


class VLLMResponseTests(unittest.TestCase):
    def test_rejects_non_object_json(self) -> None:
        with self.assertRaisesRegex(
            VLLMProtocolError, 'expected a JSON object'
        ):
            parse_model_ids([])

    def test_rejects_non_list_data(self) -> None:
        with self.assertRaisesRegex(
            VLLMProtocolError, 'field "data" must be a list'
        ):
            parse_model_ids({'data': {}})

    def test_rejects_malformed_list_item(self) -> None:
        with self.assertRaisesRegex(
            VLLMProtocolError, r'data\[1\] must be an object'
        ):
            parse_model_ids({'data': [{'id': 'expected'}, None]})

    def test_rejects_missing_model_id(self) -> None:
        with self.assertRaisesRegex(
            VLLMProtocolError, r'data\[0\]\.id must be a string'
        ):
            parse_model_ids({'data': [{}]})

    def test_accepts_valid_expected_model_response(self) -> None:
        self.assertEqual(
            parse_model_ids(
                {'data': [{'id': 'other'}, {'id': 'expected-model'}]}
            ),
            ('other', 'expected-model'),
        )


if __name__ == '__main__':
    unittest.main()
