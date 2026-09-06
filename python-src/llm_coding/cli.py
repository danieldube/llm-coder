#!/usr/bin/env python3
"""
CLI commands for llm-coding
"""

import fcntl
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import click
import requests

from .core import (
    ConfigManager,
    RunPodClient,
    check_dependencies,
    create_opencode_config,
    fatal,
)
from .runtime import _asset_text, _startup_timeout_seconds, ensure_socket
from .runtime import down as runtime_down
from .runtime import install as runtime_install

logger = logging.getLogger(__name__)


def run_command(
    cmd: list[str], capture_output: bool = True, check: bool = True
) -> subprocess.CompletedProcess:
    """Run a command and handle errors"""
    try:
        if capture_output:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=check
            )
        else:
            result = subprocess.run(cmd, check=check)
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {' '.join(cmd)}")
        logger.error(f'Return code: {e.returncode}')
        if e.stdout:
            logger.error(f'STDOUT: {e.stdout}')
        if e.stderr:
            logger.error(f'STDERR: {e.stderr}')
        if check:
            raise
        return e


def acquire_lock(lock_file: Path) -> None:
    """Acquire file lock"""
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        # Try to lock the file
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep the file descriptor open to maintain the lock
        acquire_lock.fd = fd
    except (OSError, AttributeError):
        fatal(f'Could not acquire lock on {lock_file}')


def release_lock() -> None:
    """Release file lock"""
    try:
        if hasattr(acquire_lock, 'fd'):
            fcntl.flock(acquire_lock.fd, fcntl.LOCK_UN)
            os.close(acquire_lock.fd)
    except Exception:
        pass


def create_pod_config(config: dict[str, str]) -> dict:
    """Create pod configuration for RunPod"""
    # This mirrors the logic from runtime-up.sh
    pod_config = {
        'name': config.get('RUNPOD_POD_NAME'),
        'imageName': config.get('RUNPOD_IMAGE'),
        'cloudType': config.get('RUNPOD_CLOUD_TYPE', 'SECURE'),
        'computeType': 'GPU',
        'gpuTypeIds': [config.get('RUNPOD_GPU_TYPE')],
        'gpuTypePriority': 'availability',
        'gpuCount': 1,
        'interruptible': False,
        'supportPublicIp': True,
        'containerDiskInGb': int(config.get('RUNPOD_CONTAINER_DISK_GB', 40)),
        'volumeMountPath': config.get(
            'RUNPOD_VOLUME_MOUNT_PATH', '/workspace'
        ),
        'minRAMPerGPU': int(config.get('RUNPOD_MIN_RAM_PER_GPU', 48)),
        'minVCPUPerGPU': int(config.get('RUNPOD_MIN_VCPU_PER_GPU', 8)),
        'ports': ['22/tcp'],
        'env': {},
    }

    # Add SSH public key if available
    ssh_key_path = Path(config.get('RUNPOD_SSH_KEY', ''))
    if ssh_key_path.exists():
        pub_key_path = ssh_key_path.with_suffix('.pub')
        if pub_key_path.exists():
            with open(pub_key_path) as f:
                pub_key = f.read().strip()
                pod_config['env']['SSH_PUBLIC_KEY'] = pub_key

    # Handle volume configuration
    network_volume = config.get('RUNPOD_NETWORK_VOLUME_ID', '')
    if network_volume:
        pod_config['networkVolumeId'] = network_volume
    else:
        pod_config['volumeInGb'] = int(config.get('RUNPOD_VOLUME_GB', 100))

    return pod_config


def wait_for_ssh_connection(host: str, port: int, timeout: int = 120) -> bool:
    """Wait for SSH connection to be available"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # Try to establish SSH connection
            cmd = [
                'ssh',
                '-o',
                'ConnectTimeout=5',
                '-o',
                'BatchMode=yes',
                '-o',
                'StrictHostKeyChecking=no',
                f'-p{port}',
                f'root@{host}',
                'true',
            ]
            result = run_command(cmd, capture_output=True, check=False)
            if result.returncode == 0:
                return True
        except Exception:
            # Log the specific exception for debugging but continue the loop
            logger.debug('SSH connection check failed', exc_info=True)
        time.sleep(2)
    return False


def ensure_vllm_on_remote(
    host: str, port: int, ssh_key: str, config: dict[str, str]
) -> bool:
    """Ensure vLLM is running on remote host"""
    # This replicates the logic from runtime-up.sh that runs ensure-vllm.sh remotely
    try:
        # Prepare the vLLM configuration parameters
        params = [
            config.get('VLLM_VERSION', ''),
            config.get('VLLM_CUDA_VERSION', ''),
            config.get('MODEL_ID', ''),
            config.get('MODEL_REVISION', ''),
            config.get('SERVED_MODEL_NAME', ''),
            config.get('CONTEXT_SIZE', '65536'),
            config.get('VLLM_GPU_MEMORY_UTILIZATION', '0.92'),
            config.get('VLLM_TOOL_CALL_PARSER', 'qwen3_xml'),
            config.get('REMOTE_VLLM_PORT', '8000'),
            config.get('VLLM_START_TIMEOUT_SECONDS', '1800'),
        ]

        script_content = _asset_text('remote/ensure-vllm.sh')

        # Create SSH command to run ensure-vllm.sh remotely
        cmd = [
            'ssh',
            '-i',
            ssh_key,
            '-p',
            str(port),
            '-o',
            'BatchMode=yes',
            '-o',
            'StrictHostKeyChecking=accept-new',
            f'root@{host}',
            'bash -s --',
        ] + params

        # Execute the script remotely
        logger.info(f'Running vLLM setup on remote host {host}')
        result = subprocess.run(
            cmd,
            input=script_content,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.error(f'Remote vLLM setup failed: {result.stderr}')
            return False
        logger.info('Remote vLLM setup completed successfully')
        return True

    except Exception as e:
        logger.error(f'Failed to ensure vLLM on remote: {e}')
        return False


def get_systemd_service_info(service_name: str) -> tuple:
    """Get systemd service state and invocation ID"""
    try:
        # Get active state and invocation ID
        result = run_command(
            [
                'systemctl',
                '--user',
                'show',
                '--property=ActiveState',
                '--property=InvocationID',
                '--value',
                service_name,
            ],
            capture_output=True,
        )

        lines = result.stdout.strip().split('\n')
        active_state = lines[0] if len(lines) > 0 else ''
        invocation_id = lines[1] if len(lines) > 1 else ''

        return active_state, invocation_id
    except Exception:
        logger.debug(
            f'Failed to get systemd service info for {service_name}',
            exc_info=True,
        )
        return '', ''


def print_activation_failure(state_dir: Path) -> bool:
    """Print the specific error recorded by the systemd pre-start command."""
    try:
        message = (state_dir / 'runtime.activation-error').read_text().strip()
    except OSError:
        return False
    if not message:
        return False
    print('', file=sys.stderr)
    print('LLM runtime activation failed:', file=sys.stderr)
    print(f'  {message}', file=sys.stderr)
    return True


def read_activation_status(state_dir: Path) -> str:
    """Read the latest non-sensitive runtime startup stage, if available."""
    try:
        return (state_dir / 'runtime.activation-status').read_text().strip()
    except OSError:
        return ''


def find_pod_by_name(
    runpod_client: RunPodClient, pod_name: str
) -> dict | None:
    """Find a pod by name"""
    try:
        pods = runpod_client.get_pods()
        for pod in pods:
            if pod.get('name') == pod_name:
                return pod
        return None
    except Exception as e:
        logger.error(f'Error finding pod: {e}')
        return None


def runpod_api(
    runpod_client: RunPodClient,
    method: str,
    path: str,
    body: str | None = None,
) -> str:
    """Make a RunPod API call"""
    return runpod_client.api_call(method, path, body)


def print_runtime_ready_summary(
    config: dict[str, str], state_dir: Path, proxy_models: dict
) -> None:
    """Print a concise, evidence-based summary after successful activation."""
    model = config.get('SERVED_MODEL_NAME', 'unknown')
    proxy_port = config.get('LOCAL_PROXY_PORT', '18000')
    tunnel_port = config.get('LOCAL_TUNNEL_PORT', '18001')

    # The proxy response proves the public local endpoint. Probe the direct
    # tunnel too, so the summary does not merely report that a stale proxy was
    # reachable.
    if not any(
        item.get('id') == model for item in proxy_models.get('data', [])
    ):
        fatal(f'Local proxy does not serve expected model {model}')
    try:
        tunnel_models = (
            requests.get(
                f'http://127.0.0.1:{tunnel_port}/v1/models', timeout=5
            )
            .json()
            .get('data', [])
        )
    except (requests.RequestException, ValueError) as exc:
        fatal(
            f'Proxy responded, but the direct SSH tunnel check failed: {exc}'
        )
    if not any(item.get('id') == model for item in tunnel_models):
        fatal(
            f'Proxy responded, but the SSH tunnel does not serve expected model {model}'
        )

    unit_states = {
        service: get_systemd_service_info(service)[0]
        for service in (
            'llm-coding.socket',
            'llm-coding-proxy.service',
            'llm-coding-tunnel.service',
        )
    }
    inactive = [
        service for service, state in unit_states.items() if state != 'active'
    ]
    if inactive:
        fatal(
            'Endpoint responded, but required systemd unit(s) are not active: '
            + ', '.join(inactive)
        )

    pod_summary = 'API status unavailable'
    try:
        pod = RunPodClient(config['RUNPOD_API_KEY']).find_pod_by_name(
            config['RUNPOD_POD_NAME']
        )
        if pod:
            pod_summary = f"{pod.get('name')} ({pod.get('desiredStatus', 'unknown')}, id {pod.get('id', 'unknown')})"
        else:
            pod_summary = f"{config['RUNPOD_POD_NAME']} (not returned by API)"
    except Exception as exc:
        logger.warning(
            'Could not confirm RunPod pod status after activation: %s', exc
        )

    opencode_config = create_opencode_config(config, state_dir)
    acp_state = 'not registered'
    try:
        acp = json.loads((Path.home() / '.jetbrains' / 'acp.json').read_text())
        if config.get('JETBRAINS_AGENT_NAME', 'OpenCode RunPod') in acp.get(
            'agent_servers', {}
        ):
            acp_state = 'registered'
    except (OSError, json.JSONDecodeError):
        pass

    print('LLM runtime ready:')
    print(f'  RunPod pod:     {pod_summary}')
    print(f'  vLLM model:     {model} (healthy)')
    print(f'  SSH tunnel:     127.0.0.1:{tunnel_port} (active)')
    print(f'  Local proxy:    http://127.0.0.1:{proxy_port}/v1 (active)')
    print(f'  OpenCode:       {opencode_config} (configured)')
    print(
        f"  CLion ACP:      {config.get('JETBRAINS_AGENT_NAME', 'OpenCode RunPod')} ({acp_state})"
    )


def _shutdown_summary_items():
    """Snapshot managed resources so shutdown output describes real changes."""
    stopped = []
    removed = []
    for unit, label in (
        ('llm-coding.socket', 'socket listener'),
        ('llm-coding-proxy.service', 'local proxy'),
        ('llm-coding-tunnel.service', 'SSH tunnel'),
    ):
        try:
            result = run_command(
                ['systemctl', '--user', 'is-active', unit],
                capture_output=True,
                check=False,
            )
            if result.stdout.strip() == 'active':
                stopped.append(label)
        except OSError:
            pass

    try:
        manager = ConfigManager()
        config = manager.load_config()
        for path, label in (
            (manager.state_dir / 'runtime.env', 'runtime.env'),
            (manager.state_dir / 'opencode.json', 'opencode.json'),
        ):
            if path.exists():
                removed.append(label)
        acp_file = Path.home() / '.jetbrains' / 'acp.json'
        if acp_file.exists():
            acp = json.loads(acp_file.read_text())
            agent = config.get('JETBRAINS_AGENT_NAME', 'OpenCode RunPod')
            if agent in acp.get('agent_servers', {}):
                removed.append('CLion ACP entry')
        if config.get('RUNPOD_API_KEY') and config.get('RUNPOD_POD_NAME'):
            pod = RunPodClient(config['RUNPOD_API_KEY']).find_pod_by_name(
                config['RUNPOD_POD_NAME']
            )
            if pod and pod.get('desiredStatus') == 'RUNNING':
                stopped.append('RunPod pod')
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        requests.RequestException,
        RuntimeError,
    ):
        # Status reporting must never prevent or obscure teardown itself.
        pass
    return stopped, removed


@click.group()
def cli() -> None:
    """Commands for managing the llm-coding runtime."""


@cli.command()
def llm_install() -> None:
    """Install configuration and user-level systemd integration."""
    try:
        runtime_install()
    except (OSError, RuntimeError, requests.RequestException) as exc:
        raise click.ClickException(str(exc)) from None


@cli.command()
def llm_up():
    """Activate the LLM runtime"""
    check_dependencies()

    # Load configuration
    config_manager = ConfigManager()
    config = config_manager.load_config()

    if (
        not config_manager.config_file.is_file()
        or not config_manager.secrets_file.is_file()
    ):
        fatal(f'Missing configuration files in {config_manager.config_dir}')

    # Validate configuration
    if not config_manager.validate_config(config):
        fatal('Configuration validation failed')

    try:
        ensure_socket(config)
    except Exception as e:
        fatal(f'Could not install or activate the llm-coding socket: {e}')

    try:
        # Get initial invocation ID
        initial_invocation = ''
        try:
            _, initial_invocation = get_systemd_service_info(
                'llm-coding-proxy.service'
            )
        except Exception:
            logger.debug('Failed to get initial invocation ID', exc_info=True)
            initial_invocation = ''

        # `llm-down` from older installations stopped only the tunnel and
        # RunPod.  That can leave socket-proxyd running while its upstream
        # port is gone, in which case a new request is reset immediately
        # instead of activating a fresh runtime.  Recover that stale proxy
        # before opening the socket-activation request.
        initial_state, _ = get_systemd_service_info('llm-coding-proxy.service')
        if initial_state == 'active':
            tunnel_endpoint = f"http://127.0.0.1:{config.get('LOCAL_TUNNEL_PORT', '18001')}/v1/models"
            try:
                tunnel_response = requests.get(tunnel_endpoint, timeout=2)
                tunnel_response.raise_for_status()
            except requests.RequestException:
                print(
                    'Recovering an active proxy with an unavailable SSH tunnel...',
                    file=sys.stderr,
                )
                result = run_command(
                    [
                        'systemctl',
                        '--user',
                        'restart',
                        'llm-coding-proxy.service',
                    ],
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0:
                    details = (
                        result.stderr.strip()
                        if result.stderr
                        else 'unknown systemd error'
                    )
                    if print_activation_failure(config_manager.state_dir):
                        sys.exit(1)
                    fatal(
                        f'Could not restart the stale llm-coding proxy: {details}'
                    )

        print('Activating the LLM runtime...', file=sys.stderr)

        # Calculate timeout exactly as in bash script
        timeout_seconds = _startup_timeout_seconds(config)

        # Monitor the proxy service activation (as in original bash script)
        proxy_invocation = ''
        activation_observed = False
        last_activation_status = ''

        # Connecting to the socket activates the proxy service.  Keep this
        # request running while monitoring that activation, as the shell
        # implementation does; waiting to issue it would never start the
        # service in the first place.
        endpoint = f"http://127.0.0.1:{config.get('LOCAL_PROXY_PORT', '18000')}/v1/models"
        request = subprocess.Popen(
            [
                'curl',
                '--fail',
                '--silent',
                '--show-error',
                '--max-time',
                str(timeout_seconds),
                endpoint,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        while request.poll() is None:
            activation_status = read_activation_status(
                config_manager.state_dir
            )
            if (
                activation_status
                and activation_status != last_activation_status
            ):
                print(f'  {activation_status}...', file=sys.stderr)
                last_activation_status = activation_status

            # Get current service state
            active_state, current_invocation = get_systemd_service_info(
                'llm-coding-proxy.service'
            )

            # Check if activation has been observed
            if (
                not activation_observed
                and (active_state == 'activating' or active_state == 'active')
                and current_invocation
                and current_invocation != initial_invocation
            ):
                proxy_invocation = current_invocation
                activation_observed = True

            # Check for failures or changes (as in original bash script)
            if activation_observed and (
                active_state == 'failed'
                or (
                    current_invocation
                    and current_invocation != proxy_invocation
                )
            ):
                request.terminate()
                _, request_error = request.communicate()

                print('', file=sys.stderr)
                if request_error:
                    print(request_error, file=sys.stderr, end='')
                if not print_activation_failure(config_manager.state_dir):
                    print(
                        'LLM runtime activation failed. See: journalctl --user --unit llm-coding-proxy.service --lines 30 --no-pager',
                        file=sys.stderr,
                    )
                sys.exit(1)

            # Sleep for 1 second (original interval)
            time.sleep(1)

        response, request_error = request.communicate()
        if request.returncode != 0:
            if request_error:
                print(request_error, file=sys.stderr, end='')
            if not print_activation_failure(config_manager.state_dir):
                print(
                    'LLM runtime activation failed. See: journalctl --user --unit llm-coding-proxy.service --lines 30 --no-pager',
                    file=sys.stderr,
                )
            sys.exit(1)

        if not response:
            print('No response from endpoint', file=sys.stderr)
            sys.exit(1)

        try:
            proxy_models = json.loads(response)
        except json.JSONDecodeError:
            print(response, end='')
            return
        print_runtime_ready_summary(
            config, config_manager.state_dir, proxy_models
        )

    except Exception as e:
        print(f'Error during activation: {e}', file=sys.stderr)
        sys.exit(1)


@cli.command()
def llm_down():
    """Stop the LLM runtime and remove its OpenCode/CLion integration."""
    logger.info('Stopping LLM runtime...')
    errors = []
    stopped, removed = _shutdown_summary_items()

    # Disable the listener before stopping the proxy so a concurrent OpenCode
    # request cannot reactivate the service while teardown is in progress.
    for unit, label in (
        ('llm-coding.socket', 'socket listener'),
        ('llm-coding-proxy.service', 'local proxy'),
    ):
        try:
            result = run_command(
                ['systemctl', '--user', 'stop', unit],
                capture_output=True,
                check=False,
            )
            if result.returncode:
                detail = (
                    result.stderr or result.stdout or 'unknown systemd error'
                ).strip()
                errors.append(
                    f"could not stop {label} ({detail.splitlines()[-1] if detail else 'unknown systemd error'})"
                )
        except OSError as exc:
            errors.append(f'could not stop {label} ({exc})')
    try:
        runtime_down()
    except RuntimeError as exc:
        errors.append(str(exc))
    if errors:
        fatal(
            'Shutdown incomplete. '
            + '; '.join(errors)
            + '. Fix the listed issue and run llm-down again.'
        )
    for item in stopped:
        print(f'Stopped: {item}')
    for item in removed:
        print(f'Removed: {item}')
    logger.info('LLM runtime stopped successfully')


@cli.command()
def llm_status():
    """Show LLM runtime status"""
    logger.info('Checking LLM runtime status...')

    # Check systemd services
    services = [
        ('llm-coding.socket', 'Socket'),
        ('llm-coding-proxy.service', 'Proxy'),
        ('llm-coding-tunnel.service', 'Tunnel'),
    ]

    for service, label in services:
        try:
            result = run_command(
                ['systemctl', '--user', 'is-active', service],
                capture_output=True,
            )
            status = result.stdout.strip()
            if not status:
                status = 'inactive'
            print(f'{label:<12} {status}')
        except Exception:
            logger.debug(
                f'Failed to check systemd service {service}', exc_info=True
            )
            print(f'{label:<12} unknown')

    # Check RunPod status
    try:
        config_manager = ConfigManager()
        config = config_manager.load_config()
        runpod_client = RunPodClient(config.get('RUNPOD_API_KEY'))
        pod = find_pod_by_name(runpod_client, config.get('RUNPOD_POD_NAME'))

        if pod:
            status = pod.get('desiredStatus', 'unknown')
            pod_id = pod.get('id', 'unknown')
            gpu = pod.get('gpu', {}).get('displayName') or pod.get(
                'gpu', {}
            ).get('id', 'unknown')
            print(f"{'RunPod':<12} {status}")
            print(f"{'Pod ID':<12} {pod_id}")
            print(f"{'GPU':<12} {gpu}")
        else:
            print(f"{'RunPod':<12} not created")
    except Exception as e:
        logger.error(f'Error checking RunPod status: {e}')
        print(f"{'RunPod':<12} error")

    # Check vLLM status
    try:
        config_manager = ConfigManager()
        config = config_manager.load_config()
        port = config.get('LOCAL_TUNNEL_PORT', '18001')
        endpoint = f'http://127.0.0.1:{port}/v1/models'

        # Use curl to probe the direct tunnel port
        cmd = ['curl', '--fail', '--silent', '--max-time', '2', endpoint]
        result = run_command(cmd, capture_output=True, check=False)

        if result.returncode == 0:
            print(f"{'vLLM':<12} reachable")
        else:
            print(f"{'vLLM':<12} not reachable")
    except Exception as e:
        logger.error(f'Error checking vLLM status: {e}')
        print(f"{'vLLM':<12} error")


@cli.command()
@click.option('--activate', is_flag=True, help='Perform full activation test')
def llm_doctor(activate: bool):
    """Validate the LLM setup"""
    logger.info('Running doctor checks...')
    check_dependencies()

    # Load configuration
    config_manager = ConfigManager()
    config = config_manager.load_config()

    # Validate configuration
    if not config_manager.validate_config(config):
        fatal('Configuration validation failed')

    # Perform checks...
    # 1. Check dependencies
    # 2. Check config files
    # 3. Check RunPod API key
    # 4. Check SSH keys
    # 5. Check OpenCode installation
    # 6. Check systemd socket activation
    # 7. Check JetBrains ACP registration
    # 8. (Optional) Test full activation

    # Basic checks
    logger.info('Basic checks passed')

    # Print basic configuration info
    print(f"vLLM version:        {config.get('VLLM_VERSION', 'unknown')}")
    print(
        f"vLLM CUDA variant:   cu{config.get('VLLM_CUDA_VERSION', 'unknown')}"
    )
    print(f"RunPod image:        {config.get('RUNPOD_IMAGE', 'unknown')}")

    # Check RunPod API key
    api_key = config.get('RUNPOD_API_KEY', '')
    if not api_key or api_key == 'REPLACE_ME':
        fatal('RUNPOD_API_KEY is not configured')
    print('RunPod API key configured')

    # Check SSH keys
    ssh_key_path = Path(
        os.path.expandvars(
            os.path.expanduser(config.get('RUNPOD_SSH_KEY', ''))
        )
    )
    if (
        not ssh_key_path.exists()
        or not (ssh_key_path.with_suffix('.pub')).exists()
    ):
        fatal('Dedicated RunPod SSH key is missing')
    print('RunPod SSH key')

    # Check OpenCode installation
    opencode_bin = Path.home() / '.opencode' / 'bin' / 'opencode'
    # pathlib.Path has no is_executable() method.  Require a regular file and
    # use the OS permission check so this also rejects an executable directory.
    if not opencode_bin.is_file() or not os.access(opencode_bin, os.X_OK):
        fatal('OpenCode is not installed')
    expected_version = config.get('OPENCODE_VERSION')
    if expected_version:
        installed_version = run_command(
            [str(opencode_bin), '--version']
        ).stdout.strip()
        if installed_version != expected_version:
            fatal(
                f'OpenCode version {installed_version}; expected {expected_version}'
            )
    print('OpenCode installed')

    # Check systemd socket activation
    try:
        result = run_command(
            ['systemctl', '--user', 'is-enabled', 'llm-coding.socket'],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            result = run_command(
                ['systemctl', '--user', 'is-active', 'llm-coding.socket'],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                print('systemd socket activation')
            else:
                fatal('llm-coding.socket is not listening')
        else:
            fatal('llm-coding.socket is not enabled')
    except Exception as e:
        fatal(f'systemd socket activation check failed: {e}')

    # Check JetBrains ACP registration
    acp_file = Path.home() / '.jetbrains' / 'acp.json'
    if not acp_file.exists():
        fatal('JetBrains ACP configuration not found')
    try:
        acp = json.loads(acp_file.read_text())
        if config.get('JETBRAINS_AGENT_NAME') not in acp.get(
            'agent_servers', {}
        ):
            fatal('JetBrains ACP agent is not registered')
    except json.JSONDecodeError:
        fatal('JetBrains ACP configuration is invalid JSON')
    print('JetBrains ACP registration')

    if not activate:
        print(
            "Use 'llm-doctor --activate' for an end-to-end RunPod/vLLM/tool-calling test."
        )
        return

    logger.info('Running activation test...')
    try:
        llm_up.callback()
        print('RunPod/vLLM activation')
        endpoint = (
            f"http://127.0.0.1:{config.get('LOCAL_PROXY_PORT', '18000')}/v1"
        )
        models = (
            requests.get(endpoint + '/models', timeout=30)
            .json()
            .get('data', [])
        )
        model = config.get('SERVED_MODEL_NAME', 'unknown')
        if not any(item.get('id') == model for item in models):
            fatal('Expected model is not served')
        print('Expected model ' + model)
        completion = requests.post(
            endpoint + '/chat/completions',
            timeout=60,
            json={
                'model': model,
                'max_tokens': 32,
                'messages': [
                    {'role': 'user', 'content': 'Reply with exactly: OK'}
                ],
            },
        ).json()
        if (
            not completion.get('choices', [{}])[0]
            .get('message', {})
            .get('content')
        ):
            fatal('Chat completion failed')
        print('Chat completion')
        tool_response = requests.post(
            endpoint + '/chat/completions',
            timeout=60,
            json={
                'model': model,
                'max_tokens': 64,
                'messages': [
                    {
                        'role': 'user',
                        'content': 'Call get_temperature for Berlin. Use the tool; do not answer directly.',
                    }
                ],
                'tools': [
                    {
                        'type': 'function',
                        'function': {
                            'name': 'get_temperature',
                            'description': 'Get the temperature for a city',
                            'parameters': {
                                'type': 'object',
                                'properties': {'city': {'type': 'string'}},
                                'required': ['city'],
                            },
                        },
                    }
                ],
            },
        ).json()
        if (
            not tool_response.get('choices', [{}])[0]
            .get('message', {})
            .get('tool_calls')
        ):
            fatal('Tool calling test failed')
        print('Native tool calling')
    except (requests.RequestException, ValueError) as e:
        fatal(f'Activation test failed: {e}')


@cli.command(
    context_settings={'ignore_unknown_options': True, 'allow_extra_args': True}
)
@click.pass_context
def opencode_runpod(ctx):
    """Run OpenCode with RunPod integration"""
    logger.info('Starting OpenCode with RunPod integration...')

    # Load configuration
    config_manager = ConfigManager()
    config = config_manager.load_config()

    # Create OpenCode config
    state_dir = config_manager.state_dir
    opencode_config_path = create_opencode_config(config, state_dir)

    # Set environment variable for OpenCode
    os.environ['OPENCODE_CONFIG'] = str(opencode_config_path)

    # Start OpenCode
    opencode_bin = Path.home() / '.opencode' / 'bin' / 'opencode'
    if not opencode_bin.exists():
        fatal(f'OpenCode not found at {opencode_bin}')

    # Warm the model without blocking ACP/OpenCode startup.  The request is
    # deliberately best-effort, matching the old wrapper.
    endpoint = (
        f"http://127.0.0.1:{config.get('LOCAL_PROXY_PORT', '18000')}/v1/models"
    )
    subprocess.Popen(
        [
            'curl',
            '--fail',
            '--silent',
            '--max-time',
            str(
                int(config.get('RUNPOD_START_TIMEOUT_SECONDS', 1200))
                + int(config.get('VLLM_START_TIMEOUT_SECONDS', 1800))
                + 120
            ),
            endpoint,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Execute OpenCode
    try:
        subprocess.run([str(opencode_bin), *ctx.args], check=True)
    except subprocess.CalledProcessError as e:
        fatal(f'OpenCode execution failed: {e}')


if __name__ == '__main__':
    cli()
