"""SSH host authentication, endpoint rotation, and tunnel helpers."""

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .interfaces import CommandRunner
from .state import atomic_write_private

ENDPOINT_STATE_FILE = 'runtime.ssh-endpoint.json'


def _known_hosts(state_dir: Path) -> Path:
    path = state_dir / 'known_hosts'
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    return path


def _endpoint_name(host: str, port: int) -> str:
    return f'[{host}]:{port}'


def _read_endpoint(state_dir: Path) -> dict[str, Any] | None:
    path = state_dir / ENDPOINT_STATE_FILE
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f'Persisted SSH endpoint state at {path} is invalid'
        ) from exc
    if (
        not isinstance(value, dict)
        or not isinstance(value.get('pod_id'), str)
        or not isinstance(value.get('host'), str)
        or not isinstance(value.get('port'), int)
    ):
        raise RuntimeError(
            f'Persisted SSH endpoint state at {path} is invalid'
        )
    return value


def _write_endpoint(
    state_dir: Path, pod_id: str, host: str, port: int
) -> None:
    path = state_dir / ENDPOINT_STATE_FILE
    try:
        content = json.dumps({'pod_id': pod_id, 'host': host, 'port': port})
        atomic_write_private(path, content + '\n')
    except OSError as exc:
        raise RuntimeError(
            f'Cannot persist SSH endpoint state at {path}'
        ) from exc


def prepare_endpoint(
    state_dir: Path,
    pod_id: str,
    host: str,
    port: int,
    run: CommandRunner,
    audit: Callable[[str], None],
) -> None:
    """Authorize first use or an identity-preserving endpoint rotation."""
    known_hosts = _known_hosts(state_dir)
    previous = _read_endpoint(state_dir)
    if previous is None:
        audit(
            f'Enrolling SSH endpoint {_endpoint_name(host, port)} '
            f'for pod {pod_id}'
        )
        _write_endpoint(state_dir, pod_id, host, port)
        return
    if previous['pod_id'] != pod_id:
        raise RuntimeError(
            'Refusing SSH endpoint enrollment: persisted endpoint belongs to '
            f'RunPod {previous["pod_id"]}, not expected pod {pod_id}'
        )
    if (previous['host'], previous['port']) == (host, port):
        return
    old_name = _endpoint_name(previous['host'], previous['port'])
    new_name = _endpoint_name(host, port)
    audit(f'Rotating SSH endpoint for pod {pod_id}: {old_name} -> {new_name}')
    result = run(
        ['ssh-keygen', '-R', old_name, '-f', str(known_hosts)],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(
            f'Could not remove obsolete SSH endpoint {old_name}'
        )
    _write_endpoint(state_dir, pod_id, host, port)


def command(
    config: Settings, state_dir: Path, host: str, port: int
) -> list[str]:
    """Create an SSH command using the shared strict host-key policy."""
    known_hosts = _known_hosts(state_dir)
    return [
        'ssh',
        '-T',
        '-i',
        str(config.runpod_ssh_key),
        '-p',
        str(port),
        '-o',
        'BatchMode=yes',
        '-o',
        'ConnectTimeout=5',
        '-o',
        'ServerAliveInterval=30',
        '-o',
        'ServerAliveCountMax=3',
        '-o',
        'StrictHostKeyChecking=accept-new',
        '-o',
        f'UserKnownHostsFile={known_hosts}',
        f'root@{host}',
    ]


def is_host_key_mismatch(stderr: str | None) -> bool:
    """Recognize OpenSSH's fail-closed changed-host-key diagnostic."""
    return 'REMOTE HOST IDENTIFICATION HAS CHANGED' in (stderr or '')


def exec_tunnel(
    config: Settings, state_dir: Path, host: str, port: int
) -> None:
    args = command(config, state_dir, host, port)
    destination = args.pop()
    os.execvp(
        'ssh',
        [
            *args,
            '-N',
            '-o',
            'ExitOnForwardFailure=yes',
            '-L',
            f'127.0.0.1:{config.local_tunnel_port}:127.0.0.1:{config.remote_vllm_port}',
            destination,
        ],
    )
