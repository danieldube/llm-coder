#!/usr/bin/env python3
"""Compatibility facade for historically public helpers.

New code should import focused modules directly.
"""

import logging
import shutil
import sys
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigManager, ConfigurationError, Settings, parse_settings
from .models import MODEL_SPECS
from .opencode import create_config
from .runpod import RunPodAPIError, RunPodClient, RunPodProtocolError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

__all__ = [
    'ConfigManager',
    'LegacySettingsAdapter',
    'RunPodAPIError',
    'RunPodClient',
    'RunPodProtocolError',
    'Settings',
    'check_dependencies',
    'create_opencode_config',
    'fatal',
    'write_shell_assignment',
]


@dataclass(frozen=True)
class LegacySettingsAdapter:
    """Validate a legacy environment-style mapping as typed settings."""

    values: Mapping[str, str]

    def to_settings(self) -> Settings:
        errors = [
            'legacy configuration keys and values must be strings'
            for key, value in self.values.items()
            if not isinstance(key, str) or not isinstance(value, str)
        ]
        if errors:
            raise ConfigurationError(errors)
        normalized = dict(self.values)
        selected = normalized.get('MODEL')
        if selected is None:
            matching = [
                spec.key
                for spec in MODEL_SPECS.values()
                if normalized.get('MODEL_ID') == spec.model_id
            ]
            if len(matching) == 1:
                selected = matching[0]
                normalized['MODEL'] = selected
        if selected in MODEL_SPECS:
            for key in (
                'RUNPOD_IMAGE',
                'RUNPOD_GPU_TYPE',
                'MODEL_ID',
                'MODEL_REVISION',
                'SERVED_MODEL_NAME',
                'MODEL_DISPLAY_NAME',
                'CONTEXT_SIZE',
                'MAX_OUTPUT_TOKENS',
                'VLLM_GPU_MEMORY_UTILIZATION',
                'VLLM_TOOL_CALL_PARSER',
                'VLLM_VERSION',
                'VLLM_CUDA_VERSION',
            ):
                normalized.pop(key, None)
        empty_file = Path('/dev/null')
        return parse_settings(
            empty_file,
            empty_file,
            environ=normalized,
        )


def create_opencode_config(
    config: Settings | Mapping[str, str], state_dir: Path
) -> Path:
    """Render OpenCode config, adapting the deprecated mapping input."""
    if isinstance(config, Settings):
        settings = config
    else:
        warnings.warn(
            'mapping configuration is deprecated; pass Settings instead',
            DeprecationWarning,
            stacklevel=2,
        )
        settings = LegacySettingsAdapter(config).to_settings()
    return create_config(settings, state_dir)


def fatal(message: str) -> None:
    logger.error('ERROR: %s', message)
    sys.exit(1)


def check_dependencies() -> None:
    for command in ('curl', 'jq', 'ssh', 'ssh-keygen', 'systemctl'):
        if not shutil.which(command):
            fatal(f'Missing required command: {command}')


def write_shell_assignment(key: str, value: str) -> str:
    return f'{key}="{value}"\n'
