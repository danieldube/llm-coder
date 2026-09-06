"""OpenCode installation, configuration, and JetBrains ACP integration."""

import hashlib
import importlib.resources
import json
import os
import platform
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import requests

from .config import Settings
from .interfaces import CommandRunner

_RELEASE_BASE_URL = 'https://github.com/anomalyco/opencode/releases/download'
_DOWNLOAD_LIMIT = 100 * 1024 * 1024
_DOWNLOAD_TIMEOUT = (10, 120)


@dataclass(frozen=True)
class _ReleaseArtifact:
    filename: str
    sha256: str


# This is intentionally an allowlist: a configured version cannot silently
# select an artifact that has not been reviewed and hashed by this project.
_RELEASE_ARTIFACTS: dict[str, dict[tuple[str, str], _ReleaseArtifact]] = {
    '1.18.14': {
        ('Linux', 'x86_64'): _ReleaseArtifact(
            'opencode-linux-x64.tar.gz',
            'f23980ba2aebfbfa53948e55e213d3f2a53740fd7326553828e89ad27e970572',
        ),
        ('Linux', 'aarch64'): _ReleaseArtifact(
            'opencode-linux-arm64.tar.gz',
            '27ede7aa2080002459d8c970a40016bbef49cd13bb467302777da67467f1602d',
        ),
        ('Darwin', 'x86_64'): _ReleaseArtifact(
            'opencode-darwin-x64.zip',
            '78b2e99a9094ce7a4fb38416990d2b9b23e5f99a9992a37b04fb861f24c48925',
        ),
        ('Darwin', 'arm64'): _ReleaseArtifact(
            'opencode-darwin-arm64.zip',
            'ad8125bb649086eb9210a87bbd27ac453a526e2432aebd4d3c9853e2d42e3291',
        ),
    }
}


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


def _safe_archive_path(name: str) -> bool:
    path = PurePosixPath(name.replace('\\', '/'))
    return not path.is_absolute() and '..' not in path.parts


def _copy_limited(source: Any, destination: Any) -> None:
    total = 0
    while chunk := source.read(1024 * 1024):
        total += len(chunk)
        if total > _DOWNLOAD_LIMIT:
            raise RuntimeError('OpenCode executable exceeds the size limit')
        destination.write(chunk)


def _extract_executable(archive: Path, output: Path) -> None:
    try:
        if archive.name.endswith('.zip'):
            with zipfile.ZipFile(archive) as bundle:
                zip_entries = bundle.infolist()
                if any(
                    not _safe_archive_path(item.filename)
                    for item in zip_entries
                ):
                    raise RuntimeError(
                        'OpenCode archive contains an unsafe path'
                    )
                zip_candidates = [
                    item
                    for item in zip_entries
                    if not item.is_dir()
                    and PurePosixPath(item.filename).name == 'opencode'
                ]
                if len(zip_candidates) != 1:
                    raise RuntimeError(
                        'OpenCode archive does not contain one executable'
                    )
                with (
                    bundle.open(zip_candidates[0]) as source,
                    output.open('wb') as destination,
                ):
                    _copy_limited(source, destination)
        else:
            with tarfile.open(archive, mode='r:gz') as bundle:
                tar_entries = bundle.getmembers()
                if any(
                    not _safe_archive_path(item.name)
                    or item.issym()
                    or item.islnk()
                    for item in tar_entries
                ):
                    raise RuntimeError(
                        'OpenCode archive contains an unsafe path'
                    )
                tar_candidates = [
                    item
                    for item in tar_entries
                    if item.isfile()
                    and PurePosixPath(item.name).name == 'opencode'
                ]
                if len(tar_candidates) != 1:
                    raise RuntimeError(
                        'OpenCode archive does not contain one executable'
                    )
                tar_source = bundle.extractfile(tar_candidates[0])
                if tar_source is None:
                    raise RuntimeError('Cannot read OpenCode executable')
                with tar_source, output.open('wb') as destination:
                    _copy_limited(tar_source, destination)
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, OSError) as exc:
        raise RuntimeError('Downloaded OpenCode archive is malformed') from exc


def ensure_installed(config: Settings, run: CommandRunner) -> None:
    """Securely install the configured OpenCode release when required."""
    binary = Path.home() / '.opencode/bin/opencode'
    if (
        binary.exists()
        and run(
            [str(binary), '--version'], check=False, capture_output=True
        ).stdout.strip()
        == config.opencode_version
    ):
        return
    releases = _RELEASE_ARTIFACTS.get(config.opencode_version)
    if releases is None:
        raise RuntimeError(
            f'OpenCode version {config.opencode_version!r} is not configured'
        )
    key = (platform.system(), platform.machine())
    artifact = releases.get(key)
    if artifact is None:
        raise RuntimeError(
            f'OpenCode does not support platform {key[0]!r} '
            f'architecture {key[1]!r}'
        )
    binary.parent.mkdir(parents=True, exist_ok=True)
    archive_path: Path | None = None
    staged_path: Path | None = None
    try:
        archive_fd, archive_name = tempfile.mkstemp(
            prefix='.opencode-download-',
            suffix='-' + artifact.filename,
            dir=binary.parent,
        )
        archive_path = Path(archive_name)
        digest = hashlib.sha256()
        total = 0
        url = (
            f'{_RELEASE_BASE_URL}/v{config.opencode_version}/'
            f'{artifact.filename}'
        )
        try:
            with (
                os.fdopen(archive_fd, 'wb') as destination,
                requests.get(
                    url,
                    stream=True,
                    allow_redirects=True,
                    timeout=_DOWNLOAD_TIMEOUT,
                ) as response,
            ):
                response.raise_for_status()
                length = response.headers.get('Content-Length')
                if length is not None and int(length) > _DOWNLOAD_LIMIT:
                    raise RuntimeError(
                        'OpenCode download exceeds the size limit'
                    )
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _DOWNLOAD_LIMIT:
                        raise RuntimeError(
                            'OpenCode download exceeds the size limit'
                        )
                    digest.update(chunk)
                    destination.write(chunk)
                if length is not None and total != int(length):
                    raise RuntimeError('OpenCode download was truncated')
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError('Unable to download OpenCode release') from exc
        if digest.hexdigest() != artifact.sha256:
            raise RuntimeError('OpenCode release checksum does not match')

        staged_fd, staged_name = tempfile.mkstemp(
            prefix='.opencode-install-', dir=binary.parent
        )
        os.close(staged_fd)
        staged_path = Path(staged_name)
        _extract_executable(archive_path, staged_path)
        staged_path.chmod(
            stat.S_IRUSR
            | stat.S_IWUSR
            | stat.S_IXUSR
            | stat.S_IRGRP
            | stat.S_IXGRP
            | stat.S_IROTH
            | stat.S_IXOTH
        )
        result = run(
            [str(staged_path), '--version'], check=False, capture_output=True
        )
        if (
            result.returncode
            or result.stdout.strip() != config.opencode_version
        ):
            raise RuntimeError(
                'OpenCode executable reports an unexpected version'
            )
        staged_path.replace(binary)
        staged_path = None
    finally:
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)


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
