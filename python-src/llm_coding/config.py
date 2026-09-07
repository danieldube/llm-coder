"""Authoritative loading and validation for llm-coding configuration."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised with every configuration problem found in one parse."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = tuple(errors)
        super().__init__('Invalid configuration:\n- ' + '\n- '.join(errors))


@dataclass(frozen=True)
class Settings:
    """Validated, immutable settings used by all operational code."""

    runpod_api_key: str
    runpod_ssh_key: Path
    runpod_pod_name: str
    runpod_image: str
    runpod_gpu_type: str
    runpod_cloud_type: str = 'SECURE'
    runpod_container_registry_auth_id: str = ''
    runpod_container_disk_gb: int = 40
    runpod_volume_gb: int = 100
    runpod_volume_mount_path: Path = Path('/workspace')
    runpod_min_ram_per_gpu: int = 48
    runpod_min_vcpu_per_gpu: int = 8
    runpod_network_volume_id: str = ''
    opencode_version: str = ''
    vllm_version: str = ''
    vllm_cuda_version: str = ''
    model_id: str = ''
    model_revision: str = ''
    served_model_name: str = ''
    model_display_name: str = ''
    context_size: int = 65536
    max_output_tokens: int = 16384
    vllm_gpu_memory_utilization: float = 0.92
    vllm_tool_call_parser: str = 'qwen3_xml'
    remote_vllm_port: int = 8000
    local_proxy_port: int = 18000
    local_tunnel_port: int = 18001
    idle_shutdown: str = '30min'
    runpod_start_timeout_seconds: int = 1200
    vllm_start_timeout_seconds: int = 1800
    lifecycle_lock_timeout_seconds: int = 30
    jetbrains_agent_name: str = 'OpenCode RunPod'
    enable_idea_mcp: bool = True
    enable_custom_mcp: bool = False

    @property
    def startup_timeout_seconds(self) -> int:
        return (
            self.runpod_start_timeout_seconds
            + self.vllm_start_timeout_seconds
            + 120
        )


_REQUIRED = (
    'RUNPOD_API_KEY',
    'RUNPOD_SSH_KEY',
    'RUNPOD_POD_NAME',
    'RUNPOD_IMAGE',
    'RUNPOD_GPU_TYPE',
    'OPENCODE_VERSION',
    'VLLM_VERSION',
    'VLLM_CUDA_VERSION',
    'MODEL_ID',
    'SERVED_MODEL_NAME',
    'MODEL_DISPLAY_NAME',
)
_PLACEHOLDERS = {'REPLACE_ME', 'CHANGEME', 'YOUR_API_KEY', '<API_KEY>'}


def _read_env(path: Path, errors: list[str]) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        errors.append(f'cannot read configuration file {path}')
        return {}
    result: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if '=' not in stripped:
            errors.append(f'{path}:{number}: expected NAME=VALUE')
            continue
        key, value = stripped.split('=', 1)
        key = key.strip()
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            errors.append(f'{path}:{number}: invalid setting name')
            continue
        value = value.strip()
        if len(value) >= 2 and value[:1] == value[-1:] and value[0] in '\'"':
            value = value[1:-1]
        result[key] = value
    return result


def parse_settings(
    config_file: Path,
    secrets_file: Path,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Merge config, secrets, then explicitly set environment values."""
    errors: list[str] = []
    raw = _read_env(config_file, errors)
    raw.update(_read_env(secrets_file, errors))
    source = os.environ if environ is None else environ
    known = set(Settings.__dataclass_fields__)
    env_names = {name.upper() for name in known}
    raw.update(
        {key: value for key, value in source.items() if key in env_names}
    )

    for key in _REQUIRED:
        if not raw.get(key, '').strip():
            errors.append(f'{key} is required and must not be empty')
    secret = raw.get('RUNPOD_API_KEY', '').strip()
    if secret.upper() in _PLACEHOLDERS:
        errors.append('RUNPOD_API_KEY contains a documented placeholder')

    def text(key: str, default: str = '') -> str:
        return raw.get(key, default).strip()

    def integer(key: str, default: int, low: int, high: int) -> int:
        value = text(key, str(default))
        try:
            parsed = int(value)
        except ValueError:
            errors.append(f'{key} must be an integer')
            return default
        if not low <= parsed <= high:
            errors.append(f'{key} must be between {low} and {high}')
        return parsed

    def boolean(key: str, default: bool) -> bool:
        value = text(key, str(default).lower()).lower()
        if value not in {'true', 'false'}:
            errors.append(f'{key} must be true or false')
            return default
        return value == 'true'

    def path(key: str, default: str = '') -> Path:
        return Path(os.path.expandvars(text(key, default))).expanduser()

    cloud = text('RUNPOD_CLOUD_TYPE', 'SECURE').upper()
    if cloud not in {'SECURE', 'COMMUNITY'}:
        errors.append('RUNPOD_CLOUD_TYPE must be SECURE or COMMUNITY')
    utilization_text = text('VLLM_GPU_MEMORY_UTILIZATION', '0.92')
    try:
        utilization = float(utilization_text)
    except ValueError:
        errors.append('VLLM_GPU_MEMORY_UTILIZATION must be a number')
        utilization = 0.92
    if not math.isfinite(utilization):
        errors.append('VLLM_GPU_MEMORY_UTILIZATION must be a finite number')
    elif not 0 < utilization <= 1:
        errors.append(
            'VLLM_GPU_MEMORY_UTILIZATION must be greater than 0 and at most 1'
        )

    context = integer('CONTEXT_SIZE', 65536, 1, 10_000_000)
    output = integer('MAX_OUTPUT_TOKENS', 16384, 1, 10_000_000)
    proxy = integer('LOCAL_PROXY_PORT', 18000, 1, 65535)
    tunnel = integer('LOCAL_TUNNEL_PORT', 18001, 1, 65535)
    remote = integer('REMOTE_VLLM_PORT', 8000, 1, 65535)
    runpod_timeout = integer('RUNPOD_START_TIMEOUT_SECONDS', 1200, 1, 86400)
    vllm_timeout = integer('VLLM_START_TIMEOUT_SECONDS', 1800, 1, 86400)
    lock_timeout = integer('LIFECYCLE_LOCK_TIMEOUT_SECONDS', 30, 1, 300)
    if output > context:
        errors.append('MAX_OUTPUT_TOKENS must not exceed CONTEXT_SIZE')
    if proxy == tunnel:
        errors.append('LOCAL_PROXY_PORT and LOCAL_TUNNEL_PORT must differ')
    idle = text('IDLE_SHUTDOWN', '30min')
    if not re.fullmatch(r'[1-9][0-9]*(?:us|ms|s|min|h|d|w)', idle):
        errors.append('IDLE_SHUTDOWN must be a positive systemd time span')

    settings = Settings(
        runpod_api_key=secret,
        runpod_ssh_key=path('RUNPOD_SSH_KEY'),
        runpod_pod_name=text('RUNPOD_POD_NAME'),
        runpod_image=text('RUNPOD_IMAGE'),
        runpod_gpu_type=text('RUNPOD_GPU_TYPE'),
        runpod_cloud_type=cloud,
        runpod_container_registry_auth_id=text(
            'RUNPOD_CONTAINER_REGISTRY_AUTH_ID'
        ),
        runpod_container_disk_gb=integer(
            'RUNPOD_CONTAINER_DISK_GB', 40, 1, 2048
        ),
        runpod_volume_gb=integer('RUNPOD_VOLUME_GB', 100, 1, 65536),
        runpod_volume_mount_path=path(
            'RUNPOD_VOLUME_MOUNT_PATH', '/workspace'
        ),
        runpod_min_ram_per_gpu=integer('RUNPOD_MIN_RAM_PER_GPU', 48, 1, 4096),
        runpod_min_vcpu_per_gpu=integer('RUNPOD_MIN_VCPU_PER_GPU', 8, 1, 1024),
        runpod_network_volume_id=text('RUNPOD_NETWORK_VOLUME_ID'),
        opencode_version=text('OPENCODE_VERSION'),
        vllm_version=text('VLLM_VERSION'),
        vllm_cuda_version=text('VLLM_CUDA_VERSION'),
        model_id=text('MODEL_ID'),
        model_revision=text('MODEL_REVISION'),
        served_model_name=text('SERVED_MODEL_NAME'),
        model_display_name=text('MODEL_DISPLAY_NAME'),
        context_size=context,
        max_output_tokens=output,
        vllm_gpu_memory_utilization=utilization,
        vllm_tool_call_parser=text('VLLM_TOOL_CALL_PARSER', 'qwen3_xml'),
        remote_vllm_port=remote,
        local_proxy_port=proxy,
        local_tunnel_port=tunnel,
        idle_shutdown=idle,
        runpod_start_timeout_seconds=runpod_timeout,
        vllm_start_timeout_seconds=vllm_timeout,
        lifecycle_lock_timeout_seconds=lock_timeout,
        jetbrains_agent_name=text('JETBRAINS_AGENT_NAME', 'OpenCode RunPod'),
        enable_idea_mcp=boolean('ENABLE_IDEA_MCP', True),
        enable_custom_mcp=boolean('ENABLE_CUSTOM_MCP', False),
    )
    if errors:
        raise ConfigurationError(errors)
    return settings


class ConfigManager:
    """Own XDG paths and load the authoritative validated settings."""

    def __init__(self) -> None:
        self.config_dir = (
            Path(os.environ.get('XDG_CONFIG_HOME', '~/.config')).expanduser()
            / 'llm-coding'
        )
        self.state_dir = (
            Path(
                os.environ.get('XDG_STATE_HOME', '~/.local/state')
            ).expanduser()
            / 'llm-coding'
        )
        self.install_dir = (
            Path(
                os.environ.get('XDG_DATA_HOME', '~/.local/share')
            ).expanduser()
            / 'llm-coding'
        )
        self.config_file = self.config_dir / 'config.env'
        self.secrets_file = self.config_dir / 'secrets.env'

    def ensure_directories(self) -> None:
        """Create private application directories."""
        for directory in (self.config_dir, self.state_dir, self.install_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def load_settings(self) -> Settings:
        """Load configuration without introducing filesystem side effects."""
        return parse_settings(self.config_file, self.secrets_file)

    def load_config(self) -> dict[str, str]:
        """Return the legacy unvalidated merged mapping."""
        errors: list[str] = []
        values = _read_env(self.config_file, errors)
        values.update(_read_env(self.secrets_file, errors))
        if errors:
            raise ConfigurationError(errors)
        return values
