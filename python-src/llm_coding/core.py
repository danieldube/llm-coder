#!/usr/bin/env python3
"""
Core functionality for llm-coding Python implementation
"""

import json
import logging
import os
import shutil
import sys
from pathlib import Path

import requests

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages configuration loading and validation"""

    def __init__(self):
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

        # Initialize directories
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.install_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self) -> dict[str, str]:
        """Load configuration from env files"""
        config = {}

        # Load config.env
        if self.config_file.exists():
            with open(self.config_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '=' in line:
                            key, value = line.split('=', 1)
                            config[key.strip()] = value.strip().strip('"\'')

        # Load secrets.env
        if self.secrets_file.exists():
            with open(self.secrets_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '=' in line:
                            key, value = line.split('=', 1)
                            config[key.strip()] = value.strip().strip('"\'')

        return config

    def validate_config(self, config: dict[str, str]) -> bool:
        """Validate configuration"""
        required_keys = ['RUNPOD_API_KEY', 'RUNPOD_SSH_KEY', 'RUNPOD_POD_NAME']
        for key in required_keys:
            if key not in config or not config[key]:
                logger.error(f'Missing required configuration: {key}')
                return False
        return True


class RunPodClient:
    """Handles RunPod API interactions"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = 'https://rest.runpod.io/v1'

    def _make_request(
        self, method: str, path: str, data: dict | None = None
    ) -> dict:
        """Make API request to RunPod"""
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

        url = f'{self.base_url}{path}'
        response = requests.request(
            method, url, headers=headers, json=data, timeout=120
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            # RunPod often includes the useful reason for a failed start (for
            # example, capacity or an invalid pod state) only in the response
            # body.  `raise_for_status()` otherwise reduces it to an opaque
            # HTTP status, which makes a systemd activation failure impossible
            # to diagnose from the journal.
            detail = response.text.strip()
            if detail:
                raise RuntimeError(
                    f'RunPod API {method} {path} failed with HTTP '
                    f'{response.status_code}: {detail}'
                ) from exc
            raise RuntimeError(
                f'RunPod API {method} {path} failed with HTTP {response.status_code}'
            ) from exc
        return response.json()

    def get_pods(self) -> list[dict]:
        """Get list of pods"""
        payload = self._make_request('GET', '/pods')
        return (
            payload.get('data', []) if isinstance(payload, dict) else payload
        )

    def find_pod_by_name(self, name: str) -> dict | None:
        """Find pod by name"""
        pods = self.get_pods()
        matching_pods = [pod for pod in pods if pod.get('name') == name]
        if len(matching_pods) > 1:
            raise ValueError(f"More than one RunPod named '{name}' exists.")
        return matching_pods[0] if matching_pods else None

    def create_pod(self, pod_config: dict) -> str:
        """Create a new pod"""
        response = self._make_request('POST', '/pods', pod_config)
        return response['id']

    def start_pod(self, pod_id: str) -> None:
        """Start an existing pod"""
        self._make_request('POST', f'/pods/{pod_id}/start')

    def get_pod(self, pod_id: str) -> dict:
        """Get pod details"""
        return self._make_request('GET', f'/pods/{pod_id}')


def fatal(message: str) -> None:
    """Exit with error message"""
    logger.error(f'ERROR: {message}')
    sys.exit(1)


def check_dependencies() -> None:
    """Check for required system dependencies"""
    required_commands = ['curl', 'jq', 'ssh', 'ssh-keygen', 'systemctl']
    for cmd in required_commands:
        if not shutil.which(cmd):
            fatal(f'Missing required command: {cmd}')


def write_shell_assignment(key: str, value: str) -> str:
    """Write shell assignment for environment file"""
    return f'{key}="{value}"\n'


def create_opencode_config(config: dict[str, str], state_dir: Path) -> Path:
    """Render OpenCode configuration"""
    # Read base config
    base_config_path = Path(__file__).parent / 'config' / 'opencode.base.json'
    if not base_config_path.exists():
        # Fallback to using default base config
        base_config = {
            '$schema': 'https://opencode.ai/config.json',
            'enabled_providers': ['runpod'],
            'model': 'runpod/PLACEHOLDER',
            'provider': {
                'runpod': {
                    'npm': '@ai-sdk/openai-compatible',
                    'name': 'Self-hosted RunPod',
                    'options': {'baseURL': 'http://127.0.0.1:18000/v1'},
                    'models': {},
                }
            },
            'permission': {
                'read': {
                    '*': 'allow',
                    '*.env': 'deny',
                    '*.env.*': 'deny',
                    '*.env.example': 'allow',
                },
                'edit': 'allow',
                'glob': 'allow',
                'grep': 'allow',
                'external_directory': 'deny',
                'webfetch': 'ask',
                'websearch': 'ask',
                'task': 'ask',
                'skill': 'ask',
                'bash': {
                    '*': 'ask',
                    'git status*': 'allow',
                    'git diff*': 'allow',
                    'git log*': 'allow',
                    'git show*': 'allow',
                    'git grep*': 'allow',
                    'rg *': 'allow',
                    'grep *': 'allow',
                    'git commit*': 'ask',
                    'git push*': 'deny',
                    'sudo *': 'deny',
                    'ssh *': 'deny',
                },
            },
        }
    else:
        with open(base_config_path) as f:
            base_config = json.load(f)

    # Modify base config with runtime values
    modified_config = base_config.copy()
    modified_config['model'] = (
        f"runpod/{config.get('SERVED_MODEL_NAME', 'PLACEHOLDER')}"
    )

    # Update provider options
    modified_config['provider']['runpod']['options']['baseURL'] = (
        f"http://127.0.0.1:{config.get('LOCAL_PROXY_PORT', 18000)}/v1"
    )

    # Create model configuration
    model_name = config.get('SERVED_MODEL_NAME', 'PLACEHOLDER')
    display_name = config.get('MODEL_DISPLAY_NAME', 'Unknown Model')
    context_size = int(config.get('CONTEXT_SIZE', 65536))
    max_output_tokens = int(config.get('MAX_OUTPUT_TOKENS', 16384))

    modified_config['provider']['runpod']['models'] = {
        model_name: {
            'name': display_name,
            'limit': {'context': context_size, 'output': max_output_tokens},
        }
    }

    # Save to state directory
    opencode_config_path = state_dir / 'opencode.json'
    with open(opencode_config_path, 'w') as f:
        json.dump(modified_config, f, indent=2)

    return opencode_config_path
