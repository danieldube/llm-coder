#!/usr/bin/env python3
"""Compatibility facade for historically public helpers.

New code should import focused modules directly.
"""

import logging
import shutil
import sys

from .config import ConfigManager, Settings
from .opencode import create_config as create_opencode_config
from .runpod import RunPodAPIError, RunPodClient, RunPodProtocolError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

__all__ = [
    'ConfigManager',
    'RunPodAPIError',
    'RunPodClient',
    'RunPodProtocolError',
    'Settings',
    'check_dependencies',
    'create_opencode_config',
    'fatal',
    'write_shell_assignment',
]


def fatal(message: str) -> None:
    logger.error('ERROR: %s', message)
    sys.exit(1)


def check_dependencies() -> None:
    for command in ('curl', 'jq', 'ssh', 'ssh-keygen', 'systemctl'):
        if not shutil.which(command):
            fatal(f'Missing required command: {command}')


def write_shell_assignment(key: str, value: str) -> str:
    return f'{key}="{value}"\n'
