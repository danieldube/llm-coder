"""OpenCode installation, configuration, and JetBrains ACP integration."""

import importlib.resources
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .interfaces import CommandRunner


def _value(
    config: Settings | dict[str, str], name: str, default: Any = ''
) -> Any:
    if isinstance(config, dict):
        return config.get(name.upper(), default)
    return getattr(config, name)


def create_config(config: Settings | dict[str, str], state_dir: Path) -> Path:
    """Render the generated OpenCode configuration."""
    try:
        value = json.loads(
            importlib.resources.read_text(
                'llm_coding.assets.config',
                'opencode.base.json',
                encoding='utf-8',
            )
        )
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(
            "Required packaged resource 'config/opencode.base.json' is "
            'missing; reinstall llm-coding'
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Packaged resource 'config/opencode.base.json' is invalid; "
            'reinstall llm-coding'
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(
            "Packaged resource 'config/opencode.base.json' must contain "
            'a JSON object; reinstall llm-coding'
        )
    output: dict[str, Any] = value
    output['model'] = f'runpod/{_value(config, "served_model_name")}'
    provider = output['provider']['runpod']
    provider['options']['baseURL'] = (
        f'http://127.0.0.1:{_value(config, "local_proxy_port")}/v1'
    )
    provider['models'] = {
        _value(config, 'served_model_name'): {
            'name': _value(config, 'model_display_name'),
            'limit': {
                'context': int(_value(config, 'context_size')),
                'output': int(_value(config, 'max_output_tokens')),
            },
        }
    }
    path = state_dir / 'opencode.json'
    path.write_text(json.dumps(output, indent=2))
    return path


def ensure_acp_registration(config: Settings | dict[str, str]) -> None:
    """Register the ACP server while preserving unrelated agents."""
    path = Path.home() / '.jetbrains' / 'acp.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        acp = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f'Cannot read JetBrains ACP configuration at {path}'
        ) from exc
    if not isinstance(acp, dict):
        raise RuntimeError(
            f'JetBrains ACP configuration at {path} must be a JSON object'
        )
    defaults = acp.setdefault('default_mcp_settings', {})
    servers = acp.setdefault('agent_servers', {})
    if not isinstance(defaults, dict) or not isinstance(servers, dict):
        raise RuntimeError(
            f'JetBrains ACP configuration at {path} has invalid sections'
        )
    defaults['use_idea_mcp'] = bool(_value(config, 'enable_idea_mcp', True))
    defaults['use_custom_mcp'] = bool(
        _value(config, 'enable_custom_mcp', False)
    )
    servers[_value(config, 'jetbrains_agent_name', 'OpenCode RunPod')] = {
        'command': str(Path(sys.argv[0]).resolve().parent / 'opencode-runpod'),
        'args': ['acp'],
    }
    try:
        path.write_text(json.dumps(acp, indent=2) + '\n')
        path.chmod(0o600)
    except OSError as exc:
        raise RuntimeError(
            f'Cannot update JetBrains ACP configuration at {path}'
        ) from exc


def remove_acp_registration(config: Settings | dict[str, str]) -> None:
    """Remove only this installation's ACP registration."""
    path = Path.home() / '.jetbrains' / 'acp.json'
    if not path.exists():
        return
    try:
        acp = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f'Cannot remove the CLion OpenCode entry: repair {path} and rerun integration removal'  # noqa: E501
        ) from exc
    if not isinstance(acp, dict) or not isinstance(
        acp.get('agent_servers', {}), dict
    ):
        raise RuntimeError(
            f'Cannot remove the CLion OpenCode entry: {path} has invalid content'  # noqa: E501
        )
    if (
        acp['agent_servers'].pop(
            _value(config, 'jetbrains_agent_name', 'OpenCode RunPod'), None
        )
        is None
    ):
        return
    temporary = path.with_name(path.name + '.llm-coding.tmp')
    try:
        temporary.write_text(json.dumps(acp, indent=2) + '\n')
        temporary.chmod(0o600)
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f'Cannot update {path}; check that it is writable and rerun integration removal'  # noqa: E501
        ) from exc


def ensure_installed(config: Settings, run: CommandRunner) -> None:
    """Install the configured OpenCode version when required."""
    binary = Path.home() / '.opencode/bin/opencode'
    if (
        binary.exists()
        and run(
            [str(binary), '--version'], check=False, capture_output=True
        ).stdout.strip()
        == config.opencode_version
    ):
        return
    installer = requests.get('https://opencode.ai/install', timeout=60).text
    run(
        [
            'bash',
            '-s',
            '--',
            '--version',
            config.opencode_version,
            '--no-modify-path',
        ],
        input=installer,
    )


def launch(config: Settings, state_dir: Path, args: list[str]) -> None:
    """Generate configuration, prewarm, and execute OpenCode."""
    os.environ['OPENCODE_CONFIG'] = str(create_config(config, state_dir))
    binary = Path.home() / '.opencode/bin/opencode'
    if not binary.exists():
        raise RuntimeError(f'OpenCode not found at {binary}')
    subprocess.Popen(
        [
            'curl',
            '--fail',
            '--silent',
            '--max-time',
            str(config.startup_timeout_seconds),
            f'http://127.0.0.1:{_value(config, "local_proxy_port")}/v1/models',
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run([str(binary), *args], check=True)
