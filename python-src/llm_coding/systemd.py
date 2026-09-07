"""User-systemd unit rendering and command execution."""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .config import Settings
from .interfaces import CommandRunner, SystemdController


def run_command(
    args: list[str], **kwargs: object
) -> subprocess.CompletedProcess[str]:
    kwargs.setdefault('check', True)
    kwargs.setdefault('text', True)
    return subprocess.run(args, **kwargs)  # type: ignore[call-overload,no-any-return]  # noqa: PLW1510


class Systemd:
    """Thin injectable facade over user systemd."""

    def __init__(self, runner: CommandRunner = run_command) -> None:
        self._runner = runner

    def run(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return self._runner(
            ['systemctl', '--user', *args], check=check, capture_output=True
        )


class UnitState(str, Enum):
    """Normalized states exposed by status inspection."""

    ACTIVE = 'active'
    ACTIVATING = 'activating'
    INACTIVE = 'inactive'
    FAILED = 'failed'
    NOT_FOUND = 'not-found'
    INSPECTION_FAILED = 'inspection-failed'


@dataclass(frozen=True)
class UnitStatus:
    """Typed result of inspecting one user-systemd unit."""

    name: str
    state: UnitState
    sub_state: str = ''
    invocation_id: str = ''
    detail: str = ''


def inspect_unit(
    name: str, controller: SystemdController | None = None
) -> UnitStatus:
    """Inspect a unit without treating normal inactive states as failures."""
    controller = controller or Systemd()
    try:
        result = controller.run(
            'show',
            '--property=LoadState',
            '--property=ActiveState',
            '--property=SubState',
            '--property=InvocationID',
            name,
            check=False,
        )
    except OSError as exc:
        return UnitStatus(name, UnitState.INSPECTION_FAILED, detail=str(exc))

    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            properties[key] = value
    if properties.get('LoadState') == 'not-found':
        return UnitStatus(name, UnitState.NOT_FOUND)
    active_state = properties.get('ActiveState', '')
    try:
        state = UnitState(active_state)
    except ValueError:
        detail = (result.stderr or result.stdout).strip()
        if not detail:
            detail = f'systemctl exited {result.returncode}'
        return UnitStatus(name, UnitState.INSPECTION_FAILED, detail=detail)
    return UnitStatus(
        name,
        state,
        properties.get('SubState', ''),
        properties.get('InvocationID', ''),
    )


def runtime_command() -> str:
    candidate = Path(sys.argv[0]).resolve().parent / 'llm-runtime'
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    command = shutil.which('llm-runtime')
    if command:
        return command
    raise RuntimeError('llm-runtime is not installed; reinstall llm-coding')


def user_units_dir() -> Path:
    return (
        Path(os.environ.get('XDG_CONFIG_HOME', '~/.config')).expanduser()
        / 'systemd/user'
    )


def escape_unit_argument(argument: str) -> str:
    """Quote one argument using systemd's unit command-line syntax."""
    if not argument or not argument.isprintable():
        raise ValueError(
            'systemd unit arguments must be nonempty and printable'
        )
    escaped = (
        argument.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    )
    return f'"{escaped}"'


def render_units(config: Settings) -> dict[str, str]:
    runtime = escape_unit_argument(runtime_command())
    proxy_path = (
        shutil.which('systemd-socket-proxyd')
        or '/usr/lib/systemd/systemd-socket-proxyd'
    )
    if not Path(proxy_path).exists():
        raise RuntimeError(
            'systemd-socket-proxyd is required but was not found'
        )
    proxy = escape_unit_argument(proxy_path)
    return {
        'llm-coding.socket': f'[Unit]\nDescription=On-demand self-hosted LLM endpoint\n\n[Socket]\nListenStream=127.0.0.1:{config.local_proxy_port}\nNoDelay=true\nService=llm-coding-proxy.service\n\n[Install]\nWantedBy=sockets.target\n',  # noqa: E501
        'llm-coding-proxy.service': f'[Unit]\nDescription=On-demand RunPod LLM proxy\nRequires=llm-coding.socket\nAfter=network-online.target llm-coding.socket\n\n[Service]\nType=notify\nExecStartPre={runtime} up\nExecStart={proxy} --exit-idle-time={config.idle_shutdown} 127.0.0.1:{config.local_tunnel_port}\nExecStopPost={runtime} down\nTimeoutStartSec={config.startup_timeout_seconds}s\nTimeoutStopSec=3min\n',  # noqa: E501
        'llm-coding-tunnel.service': f'[Unit]\nDescription=RunPod vLLM SSH tunnel\nAfter=network-online.target\n\n[Service]\nType=simple\nExecStart={runtime} tunnel\nRestart=on-failure\nRestartSec=5\n',  # noqa: E501
    }


def install(
    config: Settings, controller: SystemdController | None = None
) -> None:
    controller = controller or Systemd()
    directory = user_units_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for name, contents in render_units(config).items():
        (directory / name).write_text(contents)
    controller.run('daemon-reload')
    controller.run(
        'reset-failed', 'llm-coding.socket', 'llm-coding-proxy.service'
    )
    controller.run('enable', '--now', 'llm-coding.socket')


def ensure_socket(
    config: Settings, controller: SystemdController | None = None
) -> None:
    controller = controller or Systemd()
    expected = render_units(config)
    directory = user_units_dir()
    if any(
        not (directory / name).exists()
        or (directory / name).read_text() != value
        for name, value in expected.items()
    ):
        install(config, controller)
    elif controller.run(
        'is-active', 'llm-coding.socket', check=False
    ).returncode:
        install(config, controller)
