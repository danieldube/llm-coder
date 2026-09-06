"""Private, durable runtime state storage."""

import os
from pathlib import Path

POD_STATE_FILE = 'runtime.pod-id'
ACTIVATION_FAILURE_FILE = 'runtime.activation-error'
ACTIVATION_STATUS_FILE = 'runtime.activation-status'


class FileStateStore:
    """Persist pod identity and non-sensitive activation diagnostics."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def read_pod_id(self) -> str | None:
        path = self.directory / POD_STATE_FILE
        if not path.exists():
            return None
        try:
            value = path.read_text().strip()
        except OSError as exc:
            raise RuntimeError(
                f'Cannot read persisted RunPod state at {path}'
            ) from exc
        if not value or '\n' in value or '\r' in value:
            raise RuntimeError(f'Persisted RunPod state at {path} is invalid')
        return value

    def write_pod_id(self, pod_id: str) -> None:
        path = self.directory / POD_STATE_FILE
        temporary = path.with_name(path.name + '.tmp')
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(descriptor, 'w') as output:
                output.write(pod_id + '\n')
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(path)
            path.chmod(0o600)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(
                f'Cannot persist selected RunPod ID at {path}'
            ) from exc

    def forget_pod_id(self) -> None:
        try:
            (self.directory / POD_STATE_FILE).unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError('Cannot clear persisted RunPod state') from exc

    def clear_activation_failure(self) -> None:
        try:
            (self.directory / ACTIVATION_FAILURE_FILE).unlink(missing_ok=True)
        except OSError:
            pass

    def set_activation_status(self, message: str) -> None:
        try:
            path = self.directory / ACTIVATION_STATUS_FILE
            path.write_text(message.strip() + '\n')
            path.chmod(0o600)
        except OSError:
            pass

    def record_activation_failure(self, message: str) -> None:
        try:
            path = self.directory / ACTIVATION_FAILURE_FILE
            path.write_text(message.strip() + '\n')
            path.chmod(0o600)
        except OSError:
            pass
