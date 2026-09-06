"""Validation for vLLM's OpenAI-compatible model-list response."""

import json
from collections.abc import Mapping
from typing import Any, Protocol


class VLLMProtocolError(RuntimeError):
    """Raised when vLLM returns a malformed model-list response."""


class JSONResponse(Protocol):
    """The response behavior needed by :func:`response_model_ids`."""

    def json(self) -> Any: ...


def parse_model_ids(payload: object) -> tuple[str, ...]:
    """Validate a decoded ``GET /v1/models`` response and return its IDs."""
    if not isinstance(payload, Mapping):
        raise VLLMProtocolError(
            'Invalid vLLM models response: expected a JSON object'
        )

    data = payload.get('data')
    if not isinstance(data, list):
        raise VLLMProtocolError(
            'Invalid vLLM models response: field "data" must be a list'
        )

    model_ids: list[str] = []
    for index, item in enumerate(data):
        if not isinstance(item, Mapping):
            raise VLLMProtocolError(
                'Invalid vLLM models response: '
                f'data[{index}] must be an object'
            )
        model_id = item.get('id')
        if not isinstance(model_id, str):
            raise VLLMProtocolError(
                'Invalid vLLM models response: '
                f'data[{index}].id must be a string'
            )
        model_ids.append(model_id)
    return tuple(model_ids)


def parse_model_ids_json(payload: str) -> tuple[str, ...]:
    """Decode and validate a JSON model-list response body."""
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise VLLMProtocolError(
            f'Invalid vLLM models response: malformed JSON ({exc.msg})'
        ) from None
    return parse_model_ids(decoded)


def response_model_ids(response: JSONResponse) -> tuple[str, ...]:
    """Decode and validate an HTTP model-list response."""
    try:
        decoded = response.json()
    except ValueError as exc:
        raise VLLMProtocolError(
            f'Invalid vLLM models response: malformed JSON ({exc})'
        ) from None
    return parse_model_ids(decoded)
