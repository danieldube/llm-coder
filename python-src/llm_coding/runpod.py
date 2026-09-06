"""Typed RunPod provider requests and response validation."""

from typing import Any

import requests

from .config import Settings
from .interfaces import HTTPTransport


def _value(
    config: Settings | dict[str, str], name: str, default: Any = None
) -> Any:
    if isinstance(config, dict):
        return config.get(name.upper(), default)
    return getattr(config, name)


JSONResult = dict[str, Any] | list[Any]


class RunPodProtocolError(RuntimeError):
    """Raised when RunPod returns JSON that violates an endpoint contract."""


class RunPodAPIError(RuntimeError):
    """Raised when RunPod returns an unsuccessful HTTP response."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class RunPodClient:
    """Validate all interactions with the RunPod REST API."""

    def __init__(
        self, api_key: str, transport: HTTPTransport | None = None
    ) -> None:
        self.api_key = api_key
        self.base_url = 'https://rest.runpod.io/v1'
        self._transport = transport

    def _make_request(
        self, method: str, path: str, data: dict[str, Any] | None = None
    ) -> JSONResult:
        transport = self._transport or requests.request
        response = transport(
            method,
            f'{self.base_url}{path}',
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
            },
            json=data,
            timeout=120,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text.strip()
            suffix = f': {detail}' if detail else ''
            raise RunPodAPIError(
                f'RunPod API {method} {path} failed with HTTP '
                f'{response.status_code}{suffix}',
                response.status_code,
            ) from exc
        try:
            result = response.json()
        except ValueError as exc:
            raise RunPodProtocolError(
                f'RunPod API {method} {path} returned invalid JSON'
            ) from exc
        if not isinstance(result, (dict, list)):
            raise RunPodProtocolError(
                f'RunPod API {method} {path} returned '
                f'{type(result).__name__}; expected an object or array'
            )
        return result

    @staticmethod
    def _validate_pod(value: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise RunPodProtocolError(
                f'RunPod API {endpoint} returned a pod that is not an object'
            )
        for field in ('id', 'name', 'desiredStatus'):
            if not isinstance(value.get(field), str) or not value[field]:
                raise RunPodProtocolError(
                    f'RunPod API {endpoint} returned a pod without a valid '
                    f'{field}'
                )
        return value

    def get_pods(self) -> list[dict[str, Any]]:
        payload = self._make_request('GET', '/pods')
        if isinstance(payload, list):
            pods = payload
        elif set(payload) == {'data'} and isinstance(payload['data'], list):
            pods = payload['data']
        else:
            raise RunPodProtocolError(
                'RunPod API GET /pods expected an array (or legacy '
                '{"data": [...]} envelope)'
            )
        return [self._validate_pod(pod, 'GET /pods') for pod in pods]

    def find_pod_by_name(self, name: str) -> dict[str, Any] | None:
        matches = [pod for pod in self.get_pods() if pod['name'] == name]
        if len(matches) > 1:
            raise ValueError(f"More than one RunPod named '{name}' exists.")
        return matches[0] if matches else None

    def create_pod(self, pod_config: dict[str, Any]) -> str:
        response = self._make_request('POST', '/pods', pod_config)
        if not isinstance(response, dict):
            raise RunPodProtocolError(
                'RunPod API POST /pods expected an object'
            )
        pod_id = response.get('id')
        if not isinstance(pod_id, str) or not pod_id:
            raise RunPodProtocolError(
                'RunPod API POST /pods returned no valid pod id'
            )
        return pod_id

    def start_pod(self, pod_id: str) -> None:
        self._validate_action_response('start', pod_id)

    def stop_pod(self, pod_id: str) -> None:
        self._validate_action_response('stop', pod_id)

    def _validate_action_response(self, action: str, pod_id: str) -> None:
        endpoint = f'/pods/{pod_id}/{action}'
        response = self._make_request('POST', endpoint)
        pod = self._validate_pod(response, f'POST {endpoint}')
        if pod['id'] != pod_id:
            raise RunPodProtocolError(
                f'RunPod API POST {endpoint} returned a different pod id'
            )

    def get_pod(self, pod_id: str) -> dict[str, Any]:
        endpoint = f'/pods/{pod_id}'
        pod = self._validate_pod(
            self._make_request('GET', endpoint), f'GET {endpoint}'
        )
        if pod['id'] != pod_id:
            raise RunPodProtocolError(
                f'RunPod API GET {endpoint} returned a different pod id'
            )
        return pod


def pod_create_body(
    config: Settings | dict[str, str], public_key: str
) -> dict[str, Any]:
    """Build the provider create request without credential leakage."""
    body: dict[str, Any] = {
        'name': _value(config, 'runpod_pod_name'),
        'imageName': _value(config, 'runpod_image'),
        'cloudType': _value(config, 'runpod_cloud_type', 'SECURE'),
        'computeType': 'GPU',
        'gpuTypeIds': [_value(config, 'runpod_gpu_type')],
        'gpuTypePriority': 'availability',
        'gpuCount': 1,
        'interruptible': False,
        'supportPublicIp': True,
        'containerDiskInGb': int(
            _value(config, 'runpod_container_disk_gb', 40)
        ),
        'volumeMountPath': str(
            _value(config, 'runpod_volume_mount_path', '/workspace')
        ),
        'minRAMPerGPU': int(_value(config, 'runpod_min_ram_per_gpu', 48)),
        'minVCPUPerGPU': int(_value(config, 'runpod_min_vcpu_per_gpu', 8)),
        'ports': ['22/tcp'],
        'env': {'SSH_PUBLIC_KEY': public_key},
    }
    if _value(config, 'runpod_container_registry_auth_id', ''):
        body['containerRegistryAuthId'] = _value(
            config, 'runpod_container_registry_auth_id', ''
        )
    volume = _value(config, 'runpod_network_volume_id', '')
    body['networkVolumeId' if volume else 'volumeInGb'] = volume or int(
        _value(config, 'runpod_volume_gb', 100)
    )
    return body
