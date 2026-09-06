"""Typed, presentation-independent runtime status inspection."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .runpod import RunPodAPIError, RunPodClient, RunPodProtocolError
from .state import FileStateStore
from .systemd import Systemd, UnitState, UnitStatus, inspect_unit


class ProviderState(str, Enum):
    NO_SELECTED_POD = 'no-selected-pod'
    SELECTED_POD_MISSING = 'selected-pod-missing'
    AMBIGUOUS_LEGACY_NAME = 'ambiguous-legacy-name'
    API_UNAVAILABLE = 'api-unavailable'
    PROTOCOL_FAILURE = 'protocol-failure'
    AVAILABLE = 'available'


class EndpointState(str, Enum):
    REACHABLE = 'reachable'
    UNREACHABLE = 'unreachable'
    INSPECTION_FAILED = 'inspection-failed'


class OverallState(str, Enum):
    HEALTHY = 'healthy'
    INACTIVE = 'inactive'
    DEGRADED = 'degraded'
    INSPECTION_FAILED = 'inspection-failed'


@dataclass(frozen=True)
class ProviderStatus:
    state: ProviderState
    lifecycle: str = ''
    pod_id: str = ''
    gpu: str = ''
    detail: str = ''


@dataclass(frozen=True)
class RuntimeStatus:
    units: tuple[UnitStatus, ...]
    provider: ProviderStatus
    endpoint: EndpointState
    overall: OverallState

    @property
    def exit_code(self) -> int:
        return {
            OverallState.HEALTHY: 0,
            OverallState.INACTIVE: 1,
            OverallState.DEGRADED: 2,
            OverallState.INSPECTION_FAILED: 3,
        }[self.overall]


def _pod_status(pod: dict[str, Any]) -> ProviderStatus:
    gpu_value = pod.get('gpu', {})
    gpu = ''
    if isinstance(gpu_value, dict):
        gpu = str(gpu_value.get('displayName') or gpu_value.get('id') or '')
    return ProviderStatus(
        ProviderState.AVAILABLE,
        lifecycle=str(pod['desiredStatus']),
        pod_id=str(pod['id']),
        gpu=gpu,
    )


def inspect_provider(
    config: Settings,
    state_dir: Path,
    client: RunPodClient | None = None,
) -> ProviderStatus:
    """Inspect the persisted selection, never silently replacing it by name."""
    try:
        pod_id = FileStateStore(state_dir).read_pod_id()
        provider = client or RunPodClient(config.runpod_api_key)
        if pod_id:
            try:
                return _pod_status(provider.get_pod(pod_id))
            except RunPodAPIError as exc:
                if exc.status_code == 404:
                    return ProviderStatus(
                        ProviderState.SELECTED_POD_MISSING, pod_id=pod_id
                    )
                raise

        matches = [
            pod
            for pod in provider.get_pods()
            if pod['name'] == config.runpod_pod_name
        ]
        if len(matches) > 1:
            return ProviderStatus(ProviderState.AMBIGUOUS_LEGACY_NAME)
        detail = ''
        if matches:
            detail = f'unselected legacy pod {matches[0]["id"]} exists'
        return ProviderStatus(ProviderState.NO_SELECTED_POD, detail=detail)
    except RunPodProtocolError as exc:
        return ProviderStatus(ProviderState.PROTOCOL_FAILURE, detail=str(exc))
    except (RunPodAPIError, requests.RequestException, OSError) as exc:
        return ProviderStatus(ProviderState.API_UNAVAILABLE, detail=str(exc))
    except RuntimeError as exc:
        return ProviderStatus(ProviderState.PROTOCOL_FAILURE, detail=str(exc))


def inspect_runtime(
    config: Settings,
    state_dir: Path,
    *,
    systemd: Systemd | None = None,
    provider: RunPodClient | None = None,
) -> RuntimeStatus:
    units = tuple(
        inspect_unit(name, systemd)
        for name in (
            'llm-coding.socket',
            'llm-coding-proxy.service',
            'llm-coding-tunnel.service',
        )
    )
    endpoint = _inspect_endpoint(config.local_tunnel_port)
    remote = inspect_provider(config, state_dir, provider)
    inspection_failed = (
        any(unit.state is UnitState.INSPECTION_FAILED for unit in units)
        or remote.state
        in {ProviderState.API_UNAVAILABLE, ProviderState.PROTOCOL_FAILURE}
        or endpoint is EndpointState.INSPECTION_FAILED
    )
    healthy = (
        all(unit.state is UnitState.ACTIVE for unit in units)
        and remote.state is ProviderState.AVAILABLE
        and remote.lifecycle == 'RUNNING'
        and endpoint is EndpointState.REACHABLE
    )
    units_inactive = all(
        unit.state in {UnitState.INACTIVE, UnitState.NOT_FOUND}
        for unit in units
    )
    remote_inactive = remote.state in {
        ProviderState.NO_SELECTED_POD,
        ProviderState.SELECTED_POD_MISSING,
    } or (
        remote.state is ProviderState.AVAILABLE
        and remote.lifecycle in {'EXITED', 'STOPPED'}
    )
    inactive = units_inactive and remote_inactive
    overall = (
        OverallState.INSPECTION_FAILED
        if inspection_failed
        else OverallState.HEALTHY
        if healthy
        else OverallState.INACTIVE
        if inactive
        else OverallState.DEGRADED
    )
    return RuntimeStatus(units, remote, endpoint, overall)


def _inspect_endpoint(port: int) -> EndpointState:
    try:
        response = requests.get(
            f'http://127.0.0.1:{port}/v1/models', timeout=2
        )
        return (
            EndpointState.REACHABLE
            if response.ok
            else EndpointState.UNREACHABLE
        )
    except requests.RequestException:
        return EndpointState.UNREACHABLE
    except OSError:
        return EndpointState.INSPECTION_FAILED
