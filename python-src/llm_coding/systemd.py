"""User-systemd unit rendering and command execution."""

import os
import shutil
import subprocess
import sys
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


def render_units(config: Settings) -> dict[str, str]:
    runtime = runtime_command()
    proxy = (
        shutil.which('systemd-socket-proxyd')
        or '/usr/lib/systemd/systemd-socket-proxyd'
    )
    if not Path(proxy).exists():
        raise RuntimeError(
            'systemd-socket-proxyd is required but was not found'
        )
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
