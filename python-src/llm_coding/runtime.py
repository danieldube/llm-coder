"""Lifecycle orchestration for the on-demand RunPod endpoint."""

import importlib.resources
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests

from .config import ConfigManager, Settings
from .interfaces import (
    Clock,
    CommandRunner,
    RuntimeState,
    Sleeper,
    SystemdController,
)
from .opencode import (
    ensure_acp_registration,
    ensure_installed,
    remove_acp_registration,
)
from .runpod import (
    RunPodAPIError,
    RunPodClient,
    RunPodEndpoint,
    pod_create_body,
)
from .ssh import command as ssh_command
from .ssh import exec_tunnel, is_host_key_mismatch, prepare_endpoint
from .state import (
    ACTIVATION_FAILURE_FILE,
    ACTIVATION_STATUS_FILE,
    FileStateStore,
    lifecycle_lock,
)
from .systemd import Systemd, run_command
from .systemd import ensure_socket as systemd_ensure_socket
from .systemd import install as systemd_install
from .vllm import VLLMProtocolError, VLLMStatusError, response_model_ids


class PodProvider(Protocol):
    def find_pod_by_name(self, name: str) -> dict[str, Any] | None: ...
    def get_pod(self, pod_id: str) -> dict[str, Any]: ...
    def get_pod_endpoint(self, pod_id: str) -> RunPodEndpoint | None: ...
    def create_pod(self, pod_config: dict[str, Any]) -> str: ...
    def start_pod(self, pod_id: str) -> None: ...
    def stop_pod(self, pod_id: str) -> None: ...


@dataclass(frozen=True)
class RuntimeDependencies:
    """Explicit side-effect dependencies for lifecycle orchestration."""

    run: CommandRunner = run_command
    monotonic: Clock = time.monotonic
    sleep: Sleeper = time.sleep
    systemd: SystemdController | None = None

    def systemd_controller(self) -> SystemdController:
        return self.systemd or Systemd(self.run)


def _asset_text(*parts: str) -> str:
    relative = '/'.join(parts)
    if len(parts) != 2:
        raise RuntimeError(f'Invalid packaged resource path: {relative!r}')
    try:
        return importlib.resources.read_text(
            f'llm_coding.assets.{parts[0]}', parts[1], encoding='utf-8'
        )
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(
            f'Required packaged resource {relative!r} is missing; reinstall llm-coding'  # noqa: E501
        ) from exc


def _startup_timeout_seconds(config: Settings) -> int:
    return config.startup_timeout_seconds


def _lock_timeout_seconds(config: Settings) -> int:
    """Allow lightweight compatibility settings used by external callers."""
    return getattr(config, 'lifecycle_lock_timeout_seconds', 30)


def install_systemd(config: Settings | None = None) -> None:
    systemd_install(config or ConfigManager().load_settings())


def ensure_socket(config: Settings) -> None:
    systemd_ensure_socket(config)


def install(dependencies: RuntimeDependencies | None = None) -> None:
    deps = dependencies or RuntimeDependencies()
    manager = ConfigManager()
    manager.ensure_directories()
    with lifecycle_lock(
        manager.state_dir, 'install', 30, deps.monotonic, deps.sleep
    ):
        for destination, template in (
            (manager.config_file, 'config/config.env.example'),
            (manager.secrets_file, 'config/secrets.env.example'),
        ):
            if not destination.exists():
                destination.write_text(_asset_text(template))
                destination.chmod(0o600)
        config = manager.load_settings()
        key = config.runpod_ssh_key
        if not key.exists():
            key.parent.mkdir(parents=True, exist_ok=True)
            deps.run(
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
        ensure_installed(config, deps.run)
        ensure_acp_registration(config)
        systemd_install(config, deps.systemd_controller())


def _start_pod_or_raise(
    client: PodProvider, pod_id: str, config: Settings
) -> None:
    try:
        client.start_pod(pod_id)
    except RuntimeError as exc:
        if not _is_capacity_error(exc):
            raise
        storage = (
            'This pod uses a detachable network volume, so it can be recreated without losing the volume contents.'  # noqa: E501
            if (
                config.get('RUNPOD_NETWORK_VOLUME_ID', '')
                if isinstance(config, dict)
                else config.runpod_network_volume_id
            )
            else 'This pod uses pod-local storage (RUNPOD_NETWORK_VOLUME_ID is empty); deleting it can discard the cached model and files in /workspace.'  # noqa: E501
        )
        raise RuntimeError(
            f'RunPod cannot resume pod {pod_id}: its assigned host has no free GPU. '  # noqa: E501
            'Retry llm-up later. If capacity does not return, select an available '  # noqa: E501
            'GPU in RunPod and deliberately replace the persisted pod. '
            + storage
        ) from None


def _is_capacity_error(exc: RuntimeError) -> bool:
    """Return whether a provider failure specifically reports GPU capacity."""
    message = str(exc).lower()
    return (
        'no instances currently available' in message
        or 'not enough free gpus on the host machine' in message
    )


def _create_pod_or_raise(
    client: PodProvider, config: Settings, public_key: str
) -> str:
    """Create a pod and add useful context to known capacity failures."""
    try:
        return client.create_pod(pod_create_body(config, public_key))
    except RunPodAPIError as exc:
        if not _is_capacity_error(exc):
            raise
        raise RuntimeError(
            'RunPod could not create a pod: no instances are currently '
            'available for the configured request. Retry llm-up later. '
            'If this '
            'persists, check availability for RUNPOD_GPU_TYPE and '
            'RUNPOD_CLOUD_TYPE in RunPod before changing configuration.'
        ) from None


def _select_pod(
    state: RuntimeState, client: PodProvider, name: str
) -> dict[str, Any] | None:
    if hasattr(state, 'state_dir'):
        state = FileStateStore(Path(state.state_dir))
    persisted_id = state.read_pod_id()
    if persisted_id:
        try:
            return client.get_pod(persisted_id)
        except RunPodAPIError as exc:
            if exc.status_code != 404:
                raise
            state.forget_pod_id()
    pod = client.find_pod_by_name(name)
    if pod is not None:
        state.write_pod_id(str(pod['id']))
    return pod


def _read_pod_id(manager: ConfigManager) -> str | None:
    return FileStateStore(manager.state_dir).read_pod_id()


def _write_pod_id(manager: ConfigManager, pod_id: str) -> None:
    FileStateStore(manager.state_dir).write_pod_id(pod_id)


def _forget_pod_id(manager: ConfigManager) -> None:
    FileStateStore(manager.state_dir).forget_pod_id()


def up(
    dependencies: RuntimeDependencies | None = None,
    provider: PodProvider | None = None,
) -> None:
    deps = dependencies or RuntimeDependencies()
    manager = ConfigManager()
    config = manager.load_settings()
    state = FileStateStore(manager.state_dir)
    with lifecycle_lock(
        manager.state_dir,
        'startup',
        _lock_timeout_seconds(config),
        deps.monotonic,
        deps.sleep,
        state.record_activation_failure,
    ):
        ensure_acp_registration(config)
        public_key = config.runpod_ssh_key.with_suffix(
            config.runpod_ssh_key.suffix + '.pub'
        )
        if not public_key.is_file():
            raise RuntimeError(f'SSH public key not found: {public_key}')
        state.set_activation_status('Checking the RunPod pod')
        client = provider or RunPodClient(config.runpod_api_key)
        pod = _select_pod(state, client, config.runpod_pod_name)
        if pod is None:
            state.set_activation_status('Creating a RunPod pod')
            pod_id = _create_pod_or_raise(
                client, config, public_key.read_text().strip()
            )
            state.write_pod_id(pod_id)
        else:
            pod_id = str(pod['id'])
            if pod.get('desiredStatus') == 'TERMINATED':
                raise RuntimeError(
                    f'RunPod {pod_id} is TERMINATED; delete it or change RUNPOD_POD_NAME'  # noqa: E501
                )
            if pod.get('desiredStatus') == 'EXITED':
                state.set_activation_status('Starting the stopped RunPod pod')
                _start_pod_or_raise(client, pod_id, config)
        deadline = deps.monotonic() + config.runpod_start_timeout_seconds
        host: str | None = None
        port: int | None = None
        state.set_activation_status('Waiting for RunPod to expose SSH')
        while deps.monotonic() < deadline:
            pod_endpoint = client.get_pod_endpoint(pod_id)
            if pod_endpoint is not None:
                host, port = pod_endpoint.host, pod_endpoint.port
                break
            deps.sleep(5)
        if not host or not port:
            raise RuntimeError('RunPod did not expose SSH before timeout')
        prepare_endpoint(
            manager.state_dir,
            pod_id,
            host,
            port,
            deps.run,
            state.set_activation_status,
        )
        ssh = ssh_command(config, manager.state_dir, host, port)
        state.set_activation_status('Waiting for the RunPod SSH service')
        while deps.monotonic() < deadline:
            probe = deps.run([*ssh, 'true'], check=False, capture_output=True)
            if probe.returncode == 0:
                break
            if is_host_key_mismatch(probe.stderr):
                raise RuntimeError(
                    'SSH host key mismatch at unchanged endpoint '
                    f'[{host}]:{port}; refusing automatic recovery'
                )
            pod_endpoint = client.get_pod_endpoint(pod_id)
            if pod_endpoint is not None and (
                pod_endpoint.host,
                pod_endpoint.port,
            ) != (host, port):
                new_host, new_port = pod_endpoint.host, pod_endpoint.port
                prepare_endpoint(
                    manager.state_dir,
                    pod_id,
                    new_host,
                    new_port,
                    deps.run,
                    state.set_activation_status,
                )
                host, port = new_host, new_port
                ssh = ssh_command(config, manager.state_dir, host, port)
            deps.sleep(5)
        else:
            raise RuntimeError('SSH did not become available before timeout')
        state.set_activation_status(
            'Preparing vLLM on RunPod (first start can take several minutes)'
        )
        deps.run(
            [
                *ssh,
                'bash',
                '-s',
                '--',
                config.vllm_version,
                config.vllm_cuda_version,
                config.model_id,
                config.model_revision,
                config.served_model_name,
                str(config.context_size),
                str(config.vllm_gpu_memory_utilization),
                config.vllm_tool_call_parser,
                str(config.remote_vllm_port),
                str(config.vllm_start_timeout_seconds),
            ],
            input=_asset_text('remote', 'ensure-vllm.sh'),
        )
        (manager.state_dir / 'runtime.env').write_text(
            f'SSH_HOST={host}\nSSH_PORT={port}\nSSH_KEY={config.runpod_ssh_key}\n'
        )
        state.set_activation_status('Starting the local SSH tunnel')
        deps.systemd_controller().run('restart', 'llm-coding-tunnel.service')
        deadline = deps.monotonic() + 60
        endpoint = f'http://127.0.0.1:{config.local_tunnel_port}/v1/models'
        state.set_activation_status('Verifying vLLM through the SSH tunnel')
        response_error: VLLMProtocolError | VLLMStatusError | None = None
        while deps.monotonic() < deadline:
            try:
                response = requests.get(endpoint, timeout=2)
                model_ids = response_model_ids(response)
                response_error = None
                if config.served_model_name in model_ids:
                    state.set_activation_status('Runtime ready')
                    state.clear_activation_failure()
                    return
            except (VLLMProtocolError, VLLMStatusError) as exc:
                # vLLM can briefly return an incomplete response while it is
                # becoming ready. Retry, but preserve the last response error
                # across later transport failures for the timeout diagnostic.
                response_error = exc
            except requests.RequestException:
                pass
            deps.sleep(2)
        if response_error is not None:
            error_kind = (
                'unsuccessful'
                if isinstance(response_error, VLLMStatusError)
                else 'malformed'
            )
            raise RuntimeError(
                f'SSH tunnel returned {error_kind} vLLM responses: '
                f'{response_error}'
            ) from None
        raise RuntimeError('SSH tunnel did not become healthy')


def down(
    dependencies: RuntimeDependencies | None = None,
    provider: PodProvider | None = None,
) -> None:
    deps = dependencies or RuntimeDependencies()
    manager = ConfigManager()
    config = manager.load_settings()
    state = FileStateStore(manager.state_dir)
    errors: list[str] = []
    with lifecycle_lock(
        manager.state_dir,
        'shutdown',
        _lock_timeout_seconds(config),
        deps.monotonic,
        deps.sleep,
    ):
        try:
            result = deps.systemd_controller().run(
                'stop', 'llm-coding-tunnel.service', check=False
            )
            if result.returncode:
                detail = (
                    (result.stderr or result.stdout or 'unknown systemd error')
                    .strip()
                    .splitlines()[-1]
                )
                errors.append(f'could not stop the SSH tunnel ({detail})')
        except OSError as exc:
            errors.append(f'could not stop the SSH tunnel ({exc})')
        try:
            client = provider or RunPodClient(config.runpod_api_key)
            pod = _select_pod(state, client, config.runpod_pod_name)
            if pod and pod.get('desiredStatus') == 'RUNNING':
                client.stop_pod(str(pod['id']))
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            errors.append(
                f'could not stop RunPod pod {config.runpod_pod_name} ({exc})'
            )
        for name in (
            'runtime.env',
            ACTIVATION_FAILURE_FILE,
            ACTIVATION_STATUS_FILE,
        ):
            try:
                (manager.state_dir / name).unlink(missing_ok=True)
            except OSError as exc:
                errors.append(
                    f'could not remove {manager.state_dir / name} ({exc})'
                )
    if errors:
        raise RuntimeError('Shutdown incomplete: ' + '; '.join(errors))


def remove_integration(
    dependencies: RuntimeDependencies | None = None,
) -> None:
    deps = dependencies or RuntimeDependencies()
    manager = ConfigManager()
    config = manager.load_settings()
    errors: list[str] = []
    with lifecycle_lock(
        manager.state_dir,
        'uninstall',
        _lock_timeout_seconds(config),
        deps.monotonic,
        deps.sleep,
    ):
        try:
            (manager.state_dir / 'opencode.json').unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f'could not remove OpenCode configuration ({exc})')
        try:
            remove_acp_registration(config)
        except RuntimeError as exc:
            errors.append(str(exc))
    if errors:
        raise RuntimeError(
            'Integration removal incomplete: ' + '; '.join(errors)
        )


def tunnel(dependencies: RuntimeDependencies | None = None) -> None:
    manager = ConfigManager()
    values = dict(
        line.split('=', 1)
        for line in (manager.state_dir / 'runtime.env')
        .read_text()
        .splitlines()
        if '=' in line
    )
    exec_tunnel(
        manager.load_settings(),
        manager.state_dir,
        values['SSH_HOST'],
        int(values['SSH_PORT']),
    )


def main() -> None:
    commands: dict[str, Callable[[], None]] = {
        'up': up,
        'down': down,
        'tunnel': tunnel,
        'install': install,
        'remove-integration': remove_integration,
    }
    if len(sys.argv) == 2 and sys.argv[1] in {'-h', '--help'}:
        print(f'Usage: {Path(sys.argv[0]).name} {{{", ".join(commands)}}}')
        return
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit(
            f'Usage: {Path(sys.argv[0]).name} {{{", ".join(commands)}}}'
        )
    try:
        commands[sys.argv[1]]()
    except (OSError, RuntimeError, requests.RequestException) as exc:
        raise SystemExit(f'{Path(sys.argv[0]).name}: {exc}') from None


if __name__ == '__main__':
    main()
