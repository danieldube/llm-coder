#!/usr/bin/env python3
"""
Core functionality for llm-coding Python implementation
"""

import importlib.resources
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import requests

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

JSONResult = dict[str, Any] | list[Any]


class RunPodProtocolError(RuntimeError):
    """Raised when RunPod returns JSON that violates an endpoint contract."""


class RunPodAPIError(RuntimeError):
    """Raised when RunPod returns an unsuccessful HTTP response."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class ConfigManager:
    """Manages configuration loading and validation"""

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
                    stripped_line = line.strip()
                    if stripped_line and not stripped_line.startswith('#'):
                        if '=' in stripped_line:
                            key, value = stripped_line.split('=', 1)
                            config[key.strip()] = value.strip().strip('"\'')

        # Load secrets.env
        if self.secrets_file.exists():
            with open(self.secrets_file) as f:
                for line in f:
                    stripped_line = line.strip()
                    if stripped_line and not stripped_line.startswith('#'):
                        if '=' in stripped_line:
                            key, value = stripped_line.split('=', 1)
                            config[key.strip()] = value.strip().strip('"\'')

        return config

    def validate_config(self, config: dict[str, str]) -> bool:
        """Validate configuration"""
        required_keys = ['RUNPOD_API_KEY', 'RUNPOD_SSH_KEY', 'RUNPOD_POD_NAME']
        for key in required_keys:
            if key not in config or not config[key]:
                logger.error('Missing required configuration: %s', key)
                return False
        return True


class RunPodClient:
    """Handles RunPod API interactions"""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.base_url = 'https://rest.runpod.io/v1'

    def _make_request(
        self, method: str, path: str, data: dict[str, Any] | None = None
    ) -> JSONResult:
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
                raise RunPodAPIError(
                    f'RunPod API {method} {path} failed with HTTP '
                    f'{response.status_code}: {detail}',
                    response.status_code,
                ) from exc
            raise RunPodAPIError(
                f'RunPod API {method} {path} failed with HTTP '
                f'{response.status_code}',
                response.status_code,
            ) from exc
        try:
            result = response.json()
        except ValueError as exc:
            raise RunPodProtocolError(
                f'RunPod API {method} {path} returned invalid JSON'
            ) from exc
        if not isinstance(result, (dict, list)):
            raise RunPodProtocolError(
                f'RunPod API {method} {path} returned '
                f'{type(result).__name__}; expected an object or array'
            )
        return result

    @staticmethod
    def _validate_pod(value: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise RunPodProtocolError(
                f'RunPod API {endpoint} returned a pod that is not an object'
            )
        for field in ('id', 'name', 'desiredStatus'):
            if not isinstance(value.get(field), str) or not value[field]:
                raise RunPodProtocolError(
                    f'RunPod API {endpoint} returned a pod without a valid '
                    f'{field}'
                )
        return value

    def get_pods(self) -> list[dict[str, Any]]:
        """Get list of pods"""
        payload = self._make_request('GET', '/pods')
        if isinstance(payload, list):
            pods = payload
        elif set(payload) == {'data'} and isinstance(payload['data'], list):
            # Compatibility with the legacy API envelope.  Do not accept
            # arbitrary objects as an empty pod collection.
            pods = payload['data']
        else:
            raise RunPodProtocolError(
                'RunPod API GET /pods expected an array (or legacy '
                '{"data": [...]} envelope)'
            )
        return [self._validate_pod(pod, 'GET /pods') for pod in pods]

    def find_pod_by_name(self, name: str) -> dict[str, Any] | None:
        """Find pod by name"""
        pods = self.get_pods()
        matching_pods = [pod for pod in pods if pod.get('name') == name]
        if len(matching_pods) > 1:
            raise ValueError(f"More than one RunPod named '{name}' exists.")
        return matching_pods[0] if matching_pods else None

    def create_pod(self, pod_config: dict[str, Any]) -> str:
        """Create a new pod"""
        response = self._make_request('POST', '/pods', pod_config)
        if not isinstance(response, dict):
            raise RunPodProtocolError(
                'RunPod API POST /pods expected an object'
            )
        id_value = response.get('id')
        if not isinstance(id_value, str) or not id_value:
            raise RunPodProtocolError(
                'RunPod API POST /pods returned no valid pod id'
            )
        return id_value

    def start_pod(self, pod_id: str) -> None:
        """Start an existing pod"""
        self._validate_action_response('start', pod_id)

    def stop_pod(self, pod_id: str) -> None:
        """Stop a running pod."""
        self._validate_action_response('stop', pod_id)

    def _validate_action_response(self, action: str, pod_id: str) -> None:
        endpoint = f'/pods/{pod_id}/{action}'
        response = self._make_request('POST', endpoint)
        pod = self._validate_pod(response, f'POST {endpoint}')
        if pod['id'] != pod_id:
            raise RunPodProtocolError(
                f'RunPod API POST {endpoint} returned a different pod id'
            )

    def get_pod(self, pod_id: str) -> dict[str, Any]:
        """Get pod details"""
        endpoint = f'/pods/{pod_id}'
        pod = self._validate_pod(
            self._make_request('GET', endpoint), f'GET {endpoint}'
        )
        if pod['id'] != pod_id:
            raise RunPodProtocolError(
                f'RunPod API GET {endpoint} returned a different pod id'
            )
        return pod


def fatal(message: str) -> None:
    """Exit with error message"""
    logger.error('ERROR: %s', message)
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
    base_config: dict[str, Any]
    try:
        base_config = json.loads(
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
    if not isinstance(base_config, dict):
        raise RuntimeError(
            "Packaged resource 'config/opencode.base.json' must contain "
            'a JSON object; reinstall llm-coding'
        )

    # Modify base config with runtime values
    modified_config = base_config.copy()
    modified_config['model'] = (
        f"runpod/{config.get('SERVED_MODEL_NAME', 'PLACEHOLDER')}"
    )

    # Update provider options
    modified_config['provider']['runpod']['options']['baseURL'] = (
        f"http://127.0.0.1:{config.get('LOCAL_PROXY_PORT', '18000')}/v1"
    )

    # Create model configuration
    model_name = config.get('SERVED_MODEL_NAME', 'PLACEHOLDER')
    display_name = config.get('MODEL_DISPLAY_NAME', 'Unknown Model')
    context_size = int(config.get('CONTEXT_SIZE', '65536'))
    max_output_tokens = int(config.get('MAX_OUTPUT_TOKENS', '16384'))

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
