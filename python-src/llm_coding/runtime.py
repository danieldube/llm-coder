"""Runtime and systemd integration for the on-demand RunPod endpoint."""

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

from .core import ConfigManager, RunPodClient

_ACTIVATION_FAILURE_FILE = 'runtime.activation-error'
_ACTIVATION_STATUS_FILE = 'runtime.activation-status'


def _value(config, key, default=''):
    return os.path.expandvars(os.path.expanduser(config.get(key, default)))


def _run(args, **kwargs):
    kwargs.setdefault('check', True)
    kwargs.setdefault('text', True)
    return subprocess.run(args, **kwargs)


def _systemctl(*args, check=True):
    return _run(
        ['systemctl', '--user', *args], check=check, capture_output=True
    )


def _command_error(result):
    """Extract error message from command result"""
    detail = (
        result.stderr or result.stdout or 'unknown systemd error'
    ).strip()
    return detail.splitlines()[-1] if detail else 'unknown systemd error'


def _try_acquire_lock(
    lock_path: Path, timeout_seconds: int = 30
) -> Optional[object]:
    """
    Try to acquire a file lock with timeout.

    Args:
        lock_path: Path to the lock file
        timeout_seconds: Maximum time to wait for lock

    Returns:
        Lock file descriptor if successful, None if timeout
    """
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        try:
            lock_file = open(lock_path, 'w')
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_file
        except OSError:
            # Another process holds the lock, wait a bit and try again
            time.sleep(0.1)
            continue
    return None


def _executable():
    """Return the interpreter used by the installed console scripts."""
    return str(Path(sys.executable).resolve())


def _runtime_command():
    """Find the runtime console script installed beside the invoking command."""
    candidate = Path(sys.argv[0]).resolve().parent / 'llm-runtime'
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    command = shutil.which('llm-runtime')
    if command:
        return command
    raise RuntimeError('llm-runtime is not installed; reinstall llm-coding')


def _startup_timeout_seconds(config):
    """Return the full socket activation budget used by llm-up."""
    return (
        int(config.get('RUNPOD_START_TIMEOUT_SECONDS', 1200))
        + int(config.get('VLLM_START_TIMEOUT_SECONDS', 1800))
        + 120
    )


def _user_units_dir():
    return (
        Path(os.environ.get('XDG_CONFIG_HOME', '~/.config')).expanduser()
        / 'systemd/user'
    )


def _render_systemd_units(config):
    runtime = _runtime_command()
    proxy = (
        shutil.which('systemd-socket-proxyd')
        or '/usr/lib/systemd/systemd-socket-proxyd'
    )
    if not Path(proxy).exists():
        raise RuntimeError(
            'systemd-socket-proxyd is required but was not found'
        )
    port = _value(config, 'LOCAL_PROXY_PORT', '18000')
    idle = _value(config, 'IDLE_SHUTDOWN', '30min')
    startup_timeout = _startup_timeout_seconds(config)
    return {
        'llm-coding.socket': f"""[Unit]\nDescription=On-demand self-hosted LLM endpoint\n\n[Socket]\nListenStream=127.0.0.1:{port}\nNoDelay=true\nService=llm-coding-proxy.service\n\n[Install]\nWantedBy=sockets.target\n""",
        'llm-coding-proxy.service': f"""[Unit]\nDescription=On-demand RunPod LLM proxy\nRequires=llm-coding.socket\nAfter=network-online.target llm-coding.socket\n\n[Service]\nType=notify\nExecStartPre={runtime} up\nExecStart={proxy} --exit-idle-time={idle} 127.0.0.1:{_value(config, 'LOCAL_TUNNEL_PORT', '18001')}\nExecStopPost={runtime} down\nTimeoutStartSec={startup_timeout}s\nTimeoutStopSec=3min\n""",
        'llm-coding-tunnel.service': f"""[Unit]\nDescription=RunPod vLLM SSH tunnel\nAfter=network-online.target\n\n[Service]\nType=simple\nExecStart={runtime} tunnel\nRestart=on-failure\nRestartSec=5\n""",
    }


def _asset(*parts):
    """Locate an asset in an editable checkout or a wheel installation."""
    relative = Path(*parts)
    candidates = [
        Path(__file__).parents[2] / relative,
        Path(sys.prefix) / 'llm_coding/assets' / relative,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f'Packaged asset is missing: {relative}')


def install_systemd(config=None):
    """Install/update the user units required for lazy socket activation."""
    config = config or ConfigManager().load_config()
    user_units = _user_units_dir()
    user_units.mkdir(parents=True, exist_ok=True)
    units = _render_systemd_units(config)
    for name, contents in units.items():
        (user_units / name).write_text(contents)
    _systemctl('daemon-reload')
    _systemctl('reset-failed', 'llm-coding.socket', 'llm-coding-proxy.service')
    _systemctl('enable', '--now', 'llm-coding.socket')


def install():
    """Provision the complete local integration from packaged assets."""
    manager = ConfigManager()
    for destination, template in (
        (manager.config_file, 'config/config.env.example'),
        (manager.secrets_file, 'config/secrets.env.example'),
    ):
        if not destination.exists():
            destination.write_text(_asset(template).read_text())
            destination.chmod(0o600)
    config = manager.load_config()
    key = Path(_value(config, 'RUNPOD_SSH_KEY'))
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                'ssh-keygen',
                '-q',
                '-t',
                'ed25519',
                '-f',
                str(key),
                '-N',
                '',
                '-C',
                'runpod-llm-coding',
            ]
        )
    opencode = Path.home() / '.opencode/bin/opencode'
    if not opencode.exists() or _run(
        [str(opencode), '--version'], check=False, capture_output=True
    ).stdout.strip() != config.get('OPENCODE_VERSION'):
        installer = requests.get(
            'https://opencode.ai/install', timeout=60
        ).text
        _run(
            [
                'bash',
                '-s',
                '--',
                '--version',
                config['OPENCODE_VERSION'],
                '--no-modify-path',
            ],
            input=installer,
        )
    acp_file = Path.home() / '.jetbrains/acp.json'
    acp_file.parent.mkdir(parents=True, exist_ok=True)
    acp = (
        json.loads(acp_file.read_text())
        if acp_file.exists()
        else {'default_mcp_settings': {}, 'agent_servers': {}}
    )
    defaults = acp.setdefault('default_mcp_settings', {})
    defaults['use_idea_mcp'] = (
        config.get('ENABLE_IDEA_MCP', 'true').lower() == 'true'
    )
    defaults['use_custom_mcp'] = (
        config.get('ENABLE_CUSTOM_MCP', 'false').lower() == 'true'
    )
    acp.setdefault('agent_servers', {})[
        config.get('JETBRAINS_AGENT_NAME', 'OpenCode RunPod')
    ] = {
        'command': str(Path(sys.argv[0]).resolve().parent / 'opencode-runpod'),
        'args': ['acp'],
    }
    acp_file.write_text(json.dumps(acp, indent=2) + '\n')
    acp_file.chmod(0o600)
    install_systemd(config)


def ensure_socket(config):
    user_units = _user_units_dir()
    expected_units = _render_systemd_units(config)
    if any(
        (user_units / name).read_text() != contents
        if (user_units / name).exists()
        else True
        for name, contents in expected_units.items()
    ):
        install_systemd(config)
        return
    result = _systemctl('is-active', 'llm-coding.socket', check=False)
    if result.returncode != 0:
        install_systemd(config)


def _ssh_base(config, host, port):
    state = ConfigManager().state_dir
    known_hosts = state / 'known_hosts'
    known_hosts.touch(mode=0o600, exist_ok=True)
    _run(
        ['ssh-keygen', '-R', f'[{host}]:{port}', '-f', str(known_hosts)],
        check=False,
        capture_output=True,
    )
    return [
        'ssh',
        '-T',
        '-i',
        _value(config, 'RUNPOD_SSH_KEY'),
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


def _start_pod_or_raise(client, pod_id, config):
    """Start a stopped pod and translate known provider failures into guidance."""
    try:
        client.start_pod(pod_id)
    except RuntimeError as exc:
        message = str(exc)
        if 'not enough free gpus on the host machine' in message.lower():
            storage_warning = (
                'This pod uses a detachable network volume, so it can be recreated '
                "without losing the volume's contents."
                if config.get('RUNPOD_NETWORK_VOLUME_ID')
                else 'This pod uses pod-local storage (RUNPOD_NETWORK_VOLUME_ID is empty); '
                'deleting it can discard the cached model and files in /workspace.'
            )
            raise RuntimeError(
                f'RunPod cannot resume pod {pod_id}: its assigned host has no free GPU. '
                'Wait a few minutes and run llm-up again. If capacity does not return, '
                'create a replacement pod with a new RUNPOD_POD_NAME on an available GPU. '
                f'{storage_warning}'
            ) from None
        raise


def _clear_activation_failure(manager):
    """Remove a previous activation error before a new runtime attempt."""
    try:
        (manager.state_dir / _ACTIVATION_FAILURE_FILE).unlink(missing_ok=True)
    except OSError:
        # The actual activation error is more useful than a best-effort
        # cleanup failure, and will still be written by main below.
        pass


def _set_activation_status(manager, message):
    """Publish one human-readable lifecycle stage for the local CLI."""
    try:
        path = manager.state_dir / _ACTIVATION_STATUS_FILE
        path.write_text(message.strip() + '\n')
        path.chmod(0o600)
    except OSError:
        # Status reporting must never prevent the runtime from starting.
        pass


def _record_activation_failure(message):
    """Persist the actionable pre-start failure for the llm-up client."""
    try:
        path = ConfigManager().state_dir / _ACTIVATION_FAILURE_FILE
        path.write_text(message.strip() + '\n')
        path.chmod(0o600)
    except OSError:
        # systemd's stderr remains available as a fallback diagnostic.
        pass


def up():
    manager = ConfigManager()
    _clear_activation_failure(manager)
    config = manager.load_config()
    if not manager.validate_config(config):
        raise RuntimeError('configuration validation failed')
    key = Path(_value(config, 'RUNPOD_SSH_KEY'))
    if not key.with_suffix(key.suffix + '.pub').is_file():
        raise RuntimeError(f'SSH public key not found: {key}.pub')
    with open(manager.state_dir / 'runtime.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _set_activation_status(manager, 'Checking the RunPod pod')
        client = RunPodClient(config['RUNPOD_API_KEY'])
        pod = client.find_pod_by_name(config['RUNPOD_POD_NAME'])
        if pod is None:
            _set_activation_status(manager, 'Creating a RunPod pod')
            body = {
                'name': config['RUNPOD_POD_NAME'],
                'imageName': config['RUNPOD_IMAGE'],
                'cloudType': config.get('RUNPOD_CLOUD_TYPE', 'SECURE'),
                'computeType': 'GPU',
                'gpuTypeIds': [config['RUNPOD_GPU_TYPE']],
                'gpuTypePriority': 'availability',
                'gpuCount': 1,
                'interruptible': False,
                'supportPublicIp': True,
                'containerDiskInGb': int(
                    config.get('RUNPOD_CONTAINER_DISK_GB', 40)
                ),
                'volumeMountPath': config.get(
                    'RUNPOD_VOLUME_MOUNT_PATH', '/workspace'
                ),
                'minRAMPerGPU': int(config.get('RUNPOD_MIN_RAM_PER_GPU', 48)),
                'minVCPUPerGPU': int(config.get('RUNPOD_MIN_VCPU_PER_GPU', 8)),
                'ports': ['22/tcp'],
                'env': {
                    'SSH_PUBLIC_KEY': key.with_suffix(key.suffix + '.pub')
                    .read_text()
                    .strip()
                },
            }
            volume = config.get('RUNPOD_NETWORK_VOLUME_ID')
            body['networkVolumeId' if volume else 'volumeInGb'] = (
                volume or int(config.get('RUNPOD_VOLUME_GB', 100))
            )
            pod_id = client.create_pod(body)
        else:
            pod_id = pod['id']
            status = pod.get('desiredStatus')
            if status == 'TERMINATED':
                raise RuntimeError(
                    f'RunPod {pod_id} is TERMINATED; delete it or change RUNPOD_POD_NAME'
                )
            if status == 'EXITED':
                _set_activation_status(
                    manager, 'Starting the stopped RunPod pod'
                )
                _start_pod_or_raise(client, pod_id, config)
        deadline = time.monotonic() + int(
            config.get('RUNPOD_START_TIMEOUT_SECONDS', 1200)
        )
        host = port = None
        _set_activation_status(manager, 'Waiting for RunPod to expose SSH')
        while time.monotonic() < deadline:
            pod = client.get_pod(pod_id)
            host, port = (
                pod.get('publicIp'),
                pod.get('portMappings', {}).get('22'),
            )
            if host and port:
                break
            time.sleep(5)
        if not host or not port:
            raise RuntimeError('RunPod did not expose SSH before timeout')
        ssh = _ssh_base(config, host, port)
        _set_activation_status(manager, 'Waiting for the RunPod SSH service')
        while time.monotonic() < deadline:
            if (
                _run(
                    [*ssh, 'true'], check=False, capture_output=True
                ).returncode
                == 0
            ):
                break
            # A resumed RunPod may be assigned a new external SSH endpoint.
            refreshed = client.get_pod(pod_id)
            refreshed_host = refreshed.get('publicIp')
            refreshed_port = refreshed.get('portMappings', {}).get('22')
            if (
                refreshed_host
                and refreshed_port
                and (refreshed_host, refreshed_port) != (host, port)
            ):
                host, port = refreshed_host, refreshed_port
                ssh = _ssh_base(config, host, port)
            time.sleep(5)
        else:
            raise RuntimeError('SSH did not become available before timeout')
        script = _asset('remote/ensure-vllm.sh')
        _set_activation_status(
            manager,
            'Preparing vLLM on RunPod (first start can take several minutes)',
        )
        _run(
            [
                *ssh,
                'bash',
                '-s',
                '--',
                config['VLLM_VERSION'],
                config['VLLM_CUDA_VERSION'],
                config['MODEL_ID'],
                config.get('MODEL_REVISION', ''),
                config['SERVED_MODEL_NAME'],
                config.get('CONTEXT_SIZE', '65536'),
                config.get('VLLM_GPU_MEMORY_UTILIZATION', '0.92'),
                config.get('VLLM_TOOL_CALL_PARSER', 'qwen3_xml'),
                config.get('REMOTE_VLLM_PORT', '8000'),
                config.get('VLLM_START_TIMEOUT_SECONDS', '1800'),
            ],
            input=script.read_text(),
        )
        (manager.state_dir / 'runtime.env').write_text(
            f'SSH_HOST={host}\nSSH_PORT={port}\nSSH_KEY={key}\n'
        )
        _set_activation_status(manager, 'Starting the local SSH tunnel')
        _systemctl('restart', 'llm-coding-tunnel.service')
        endpoint = f"http://127.0.0.1:{config.get('LOCAL_TUNNEL_PORT', '18001')}/v1/models"
        healthy_until = time.monotonic() + 60
        _set_activation_status(
            manager, 'Verifying vLLM through the SSH tunnel'
        )
        while time.monotonic() < healthy_until:
            try:
                models = (
                    requests.get(endpoint, timeout=2).json().get('data', [])
                )
                if any(
                    model.get('id') == config['SERVED_MODEL_NAME']
                    for model in models
                ):
                    _set_activation_status(manager, 'Runtime ready')
                    return
            except (requests.RequestException, ValueError):
                pass
            time.sleep(2)
        raise RuntimeError('SSH tunnel did not become healthy')


def _remove_clion_opencode_config(config):
    """Remove only this installation's ACP agent, preserving other agents."""
    acp_file = Path.home() / '.jetbrains' / 'acp.json'
    if not acp_file.exists():
        return
    try:
        acp = json.loads(acp_file.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f'Cannot remove the CLion OpenCode entry: repair {acp_file} and run llm-down again'
        ) from exc
    if not isinstance(acp, dict):
        raise RuntimeError(
            f'Cannot remove the CLion OpenCode entry: {acp_file} must contain a JSON object'
        )
    servers = acp.get('agent_servers', {})
    if not isinstance(servers, dict):
        raise RuntimeError(
            f'Cannot remove the CLion OpenCode entry: {acp_file} has an invalid agent_servers section'
        )
    if (
        servers.pop(
            config.get('JETBRAINS_AGENT_NAME', 'OpenCode RunPod'), None
        )
        is None
    ):
        return
    temporary = acp_file.with_name(acp_file.name + '.llm-coding.tmp')
    try:
        temporary.write_text(json.dumps(acp, indent=2) + '\n')
        temporary.chmod(0o600)
        temporary.replace(acp_file)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(
            f'Cannot update {acp_file}; check that it is writable and run llm-down again'
        ) from exc


def down():
    """Stop remote/local runtime pieces and remove generated integrations.

    Each cleanup step is attempted even when an earlier one fails, allowing a
    failed RunPod request to be retried without leaving a usable-looking ACP
    integration or stale tunnel state behind.
    """
    manager = ConfigManager()
    config = manager.load_config()
    errors = []
    with open(manager.state_dir / 'runtime.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            result = _systemctl(
                'stop', 'llm-coding-tunnel.service', check=False
            )
            if result.returncode:
                errors.append(
                    f'could not stop the SSH tunnel ({_command_error(result)})'
                )
        except OSError as exc:
            errors.append(f'could not stop the SSH tunnel ({exc})')

        pod_name = config.get('RUNPOD_POD_NAME', '')
        api_key = config.get('RUNPOD_API_KEY', '')
        if not pod_name or not api_key:
            errors.append(
                'could not stop the RunPod pod (set RUNPOD_POD_NAME and RUNPOD_API_KEY, then run llm-down again)'
            )
        else:
            try:
                client = RunPodClient(api_key)
                pod = client.find_pod_by_name(pod_name)
                if pod and pod.get('desiredStatus') == 'RUNNING':
                    client._make_request('POST', f"/pods/{pod['id']}/stop")
            except (
                requests.RequestException,
                RuntimeError,
                ValueError,
            ) as exc:
                errors.append(f'could not stop RunPod pod {pod_name} ({exc})')

        for generated_file in (
            manager.state_dir / 'runtime.env',
            manager.state_dir / 'opencode.json',
        ):
            try:
                generated_file.unlink(missing_ok=True)
            except OSError as exc:
                errors.append(f'could not remove {generated_file} ({exc})')
        try:
            _remove_clion_opencode_config(config)
        except RuntimeError as exc:
            errors.append(str(exc))

    if errors:
        raise RuntimeError('Shutdown incomplete: ' + '; '.join(errors))


def tunnel():
    manager = ConfigManager()
    values = dict(
        line.strip().split('=', 1)
        for line in (manager.state_dir / 'runtime.env')
        .read_text()
        .splitlines()
        if '=' in line
    )
    config = manager.load_config()
    known_hosts = manager.state_dir / 'known_hosts'
    os.execvp(
        'ssh',
        [
            'ssh',
            '-N',
            '-T',
            '-i',
            values['SSH_KEY'],
            '-p',
            values['SSH_PORT'],
            '-o',
            'BatchMode=yes',
            '-o',
            'ExitOnForwardFailure=yes',
            '-o',
            'ServerAliveInterval=30',
            '-o',
            'ServerAliveCountMax=3',
            '-o',
            'StrictHostKeyChecking=accept-new',
            '-o',
            f'UserKnownHostsFile={known_hosts}',
            '-L',
            f"127.0.0.1:{config.get('LOCAL_TUNNEL_PORT', '18001')}:127.0.0.1:{config.get('REMOTE_VLLM_PORT', '8000')}",
            f"root@{values['SSH_HOST']}",
        ],
    )


def main():
    commands = {'up': up, 'down': down, 'tunnel': tunnel, 'install': install}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit(
            f"Usage: {Path(sys.argv[0]).name} {{{', '.join(commands)}}}"
        )
    try:
        commands[sys.argv[1]]()
    except (OSError, RuntimeError, requests.RequestException) as exc:
        if sys.argv[1] == 'up':
            _record_activation_failure(str(exc))
        raise SystemExit(f'{Path(sys.argv[0]).name}: {exc}') from None


if __name__ == '__main__':
    main()
