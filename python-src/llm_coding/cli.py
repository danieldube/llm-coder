#!/usr/bin/env python3
"""
CLI commands for llm-coding
"""

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import click
import requests

from .config import Settings
from .core import (
    ConfigManager,
    check_dependencies,
    create_opencode_config,
    fatal,
)
from .opencode import launch as launch_opencode
from .runtime import _startup_timeout_seconds, ensure_socket
from .runtime import down as runtime_down
from .runtime import install as runtime_install
from .runtime import remove_integration as runtime_remove_integration
from .status import ProviderState, inspect_provider, inspect_runtime
from .systemd import inspect_unit

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
        logger.error(f'Command failed: {" ".join(cmd)}')
        logger.error(f'Return code: {e.returncode}')
        if e.stdout:
            logger.error(f'STDOUT: {e.stdout}')
        if e.stderr:
            logger.error(f'STDERR: {e.stderr}')
        if check:
            raise
        return e


def get_systemd_service_info(service_name: str) -> tuple[str, str]:
    """Get systemd service state and invocation ID"""
    status = inspect_unit(service_name)
    return status.state.value, status.invocation_id


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


def print_runtime_ready_summary(
    config: Settings, state_dir: Path, proxy_models: dict
) -> None:
    """Print a concise, evidence-based summary after successful activation."""
    model = config.served_model_name
    proxy_port = config.local_proxy_port
    tunnel_port = config.local_tunnel_port

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
    pod = inspect_provider(config, state_dir)
    if pod.state is ProviderState.AVAILABLE:
        pod_summary = f'{pod.lifecycle} (id {pod.pod_id})'
    elif pod.state in {
        ProviderState.API_UNAVAILABLE,
        ProviderState.PROTOCOL_FAILURE,
    }:
        logger.warning(
            'Could not confirm RunPod pod status after activation: %s',
            pod.detail,
        )
    else:
        pod_summary = pod.state.value

    opencode_config = create_opencode_config(config, state_dir)
    acp_state = 'not registered'
    try:
        acp = json.loads((Path.home() / '.jetbrains' / 'acp.json').read_text())
        if config.jetbrains_agent_name in acp.get('agent_servers', {}):
            acp_state = 'registered'
    except (OSError, json.JSONDecodeError):
        pass

    print('LLM runtime ready:')
    print(f'  RunPod pod:     {pod_summary}')
    print(f'  vLLM model:     {model} (healthy)')
    print(f'  SSH tunnel:     127.0.0.1:{tunnel_port} (active)')
    print(f'  Local proxy:    http://127.0.0.1:{proxy_port}/v1 (active)')
    print(f'  OpenCode:       {opencode_config} (configured)')
    print(f'  CLion ACP:      {config.jetbrains_agent_name} ({acp_state})')


def _shutdown_summary_items(include_integration: bool = False):
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
        config = manager.load_settings()
        for path, label in (
            (manager.state_dir / 'runtime.env', 'runtime.env'),
        ):
            if path.exists():
                removed.append(label)
        if include_integration:
            if (manager.state_dir / 'opencode.json').exists():
                removed.append('OpenCode configuration')
            acp_file = Path.home() / '.jetbrains' / 'acp.json'
            if acp_file.exists():
                acp = json.loads(acp_file.read_text())
                agent = config.jetbrains_agent_name
                if agent in acp.get('agent_servers', {}):
                    removed.append('CLion ACP entry')
        if config.runpod_api_key:
            pod = inspect_provider(config, manager.state_dir)
            if (
                pod.state is ProviderState.AVAILABLE
                and pod.lifecycle == 'RUNNING'
            ):
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
    config = config_manager.load_settings()

    if (
        not config_manager.config_file.is_file()
        or not config_manager.secrets_file.is_file()
    ):
        fatal(f'Missing configuration files in {config_manager.config_dir}')

    # Validate configuration

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
            tunnel_endpoint = (
                f'http://127.0.0.1:{config.local_tunnel_port}/v1/models'
            )
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
        endpoint = f'http://127.0.0.1:{config.local_proxy_port}/v1/models'
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
@click.option(
    '--remove-integration',
    is_flag=True,
    help=(
        "Also remove this installation's durable OpenCode configuration and "
        'JetBrains ACP registration after confirmation.'
    ),
)
def llm_down(remove_integration: bool = False) -> None:
    """Stop the transient runtime; retain configuration for later reuse.

    By default this explicitly stops the socket, proxy, SSH tunnel, and RunPod
    while preserving installation data and IDE integration.  Use
    --remove-integration to additionally unregister this project.
    """
    if remove_integration and not click.confirm(
        'Remove this llm-coding OpenCode/JetBrains integration?',
        default=False,
    ):
        raise click.Abort
    logger.info('Stopping LLM runtime...')
    errors = []
    stopped, removed = _shutdown_summary_items(remove_integration)

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
                    f'could not stop {label} ({detail.splitlines()[-1] if detail else "unknown systemd error"})'
                )
        except OSError as exc:
            errors.append(f'could not stop {label} ({exc})')
    try:
        runtime_down()
    except RuntimeError as exc:
        errors.append(str(exc))
    if remove_integration:
        try:
            runtime_remove_integration()
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
def llm_status() -> None:
    """Show LLM runtime status"""
    logger.info('Checking LLM runtime status...')
    try:
        manager = ConfigManager()
        result = inspect_runtime(manager.load_settings(), manager.state_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(
            f'Status inspection failed: {exc}'
        ) from None

    for label, unit in zip(
        ('Socket', 'Proxy', 'Tunnel'), result.units, strict=True
    ):
        suffix = f' ({unit.sub_state})' if unit.sub_state else ''
        if unit.detail:
            suffix = f' ({unit.detail})'
        print(f'{label:<12} {unit.state.value}{suffix}')
    provider = result.provider
    if provider.state is ProviderState.AVAILABLE:
        print(f'{"RunPod":<12} {provider.lifecycle}')
        print(f'{"Pod ID":<12} {provider.pod_id}')
        if provider.gpu:
            print(f'{"GPU":<12} {provider.gpu}')
    else:
        detail = f' ({provider.detail})' if provider.detail else ''
        print(f'{"RunPod":<12} {provider.state.value}{detail}')
    print(f'{"vLLM":<12} {result.endpoint.value}')
    print(f'{"Overall":<12} {result.overall.value}')
    if result.exit_code:
        raise click.exceptions.Exit(result.exit_code)


@cli.command()
@click.option('--activate', is_flag=True, help='Perform full activation test')
def llm_doctor(activate: bool):
    """Validate the LLM setup"""
    logger.info('Running doctor checks...')
    check_dependencies()

    # Load configuration
    config_manager = ConfigManager()
    config = config_manager.load_settings()

    # Validate configuration

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
    print(f'vLLM version:        {config.vllm_version}')
    print(f'vLLM CUDA variant:   cu{config.vllm_cuda_version}')
    print(f'RunPod image:        {config.runpod_image}')

    # Check RunPod API key
    api_key = config.runpod_api_key
    if not api_key or api_key == 'REPLACE_ME':
        fatal('RUNPOD_API_KEY is not configured')
    print('RunPod API key configured')

    # Check SSH keys
    ssh_key_path = Path(
        os.path.expandvars(os.path.expanduser(str(config.runpod_ssh_key)))
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
    expected_version = config.opencode_version
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
        if config.jetbrains_agent_name not in acp.get('agent_servers', {}):
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
        endpoint = f'http://127.0.0.1:{config.local_proxy_port}/v1'
        models = (
            requests.get(endpoint + '/models', timeout=30)
            .json()
            .get('data', [])
        )
        model = config.served_model_name
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
def opencode_runpod(ctx: click.Context) -> None:
    """Run OpenCode with RunPod integration."""
    manager = ConfigManager()
    try:
        launch_opencode(
            manager.load_settings(), manager.state_dir, list(ctx.args)
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        fatal(f'OpenCode execution failed: {exc}')


if __name__ == '__main__':
    cli()
