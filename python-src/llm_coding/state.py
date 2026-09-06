"""Private, durable runtime state storage."""

import errno
import fcntl
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

POD_STATE_FILE = 'runtime.pod-id'
ACTIVATION_FAILURE_FILE = 'runtime.activation-error'
ACTIVATION_STATUS_FILE = 'runtime.activation-status'
LIFECYCLE_LOCK_FILE = 'runtime.lock'
LIFECYCLE_LOCK_RETRY_SECONDS = 0.1
_KNOWN_OPERATIONS = frozenset({'install', 'shutdown', 'startup', 'uninstall'})


@contextmanager
def lifecycle_lock(
    directory: Path,
    operation: str,
    timeout_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    on_error: Callable[[str], None] | None = None,
) -> Iterator[None]:
    """Exclusively serialize a lifecycle operation with a bounded wait."""
    if operation not in _KNOWN_OPERATIONS:
        raise ValueError(f'Unknown lifecycle operation: {operation}')
    if timeout_seconds < 0:
        raise ValueError('Lifecycle lock timeout must not be negative')

    path = directory / LIFECYCLE_LOCK_FILE
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        lock = os.fdopen(descriptor, 'r+', encoding='utf-8')
    except BaseException:
        os.close(descriptor)
        raise
    acquired = False
    deadline = monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                if monotonic() >= deadline:
                    lock.seek(0)
                    holder = lock.read().strip()
                    competing = (
                        holder
                        if holder in _KNOWN_OPERATIONS
                        else 'another lifecycle operation'
                    )
                    raise RuntimeError(
                        f'Timed out after {timeout_seconds:g} seconds waiting '
                        f'for {competing} to finish; retry the command after '
                        'that operation completes'
                    ) from None
                sleep(
                    min(
                        LIFECYCLE_LOCK_RETRY_SECONDS,
                        max(0.0, deadline - monotonic()),
                    )
                )
        lock.seek(0)
        lock.truncate()
        lock.write(operation + '\n')
        lock.flush()
        try:
            yield
        except Exception as exc:
            if on_error is not None:
                on_error(str(exc))
            raise
    finally:
        try:
            if acquired:
                fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()


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
