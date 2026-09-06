"""SSH host authentication, readiness, and local tunnel helpers."""

import os
from pathlib import Path

from .config import Settings
from .interfaces import CommandRunner


def command(
    config: Settings, state_dir: Path, host: str, port: int, run: CommandRunner
) -> list[str]:
    """Create a host-authenticated SSH command for a transient endpoint."""
    known_hosts = state_dir / 'known_hosts'
    known_hosts.touch(mode=0o600, exist_ok=True)
    run(
        ['ssh-keygen', '-R', f'[{host}]:{port}', '-f', str(known_hosts)],
        check=False,
        capture_output=True,
    )
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


def exec_tunnel(
    config: Settings, state_dir: Path, host: str, port: int, run: CommandRunner
) -> None:
    args = command(config, state_dir, host, port, run)
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
