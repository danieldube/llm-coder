"""Private, durable runtime state storage."""

import errno
import fcntl
import json
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

POD_STATE_FILE = 'runtime.pod-id'
POD_SPEC_FILE = 'runtime.pod-spec.json'
ACTIVATION_FAILURE_FILE = 'runtime.activation-error'
ACTIVATION_STATUS_FILE = 'runtime.activation-status'
LIFECYCLE_LOCK_FILE = 'runtime.lock'
LIFECYCLE_LOCK_RETRY_SECONDS = 0.1
_KNOWN_OPERATIONS = frozenset({'install', 'shutdown', 'startup', 'uninstall'})


def _fsync_directory(directory: Path) -> None:
    """Synchronize directory metadata when the platform supports doing so."""
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError as exc:
        if exc.errno in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in {
                errno.EINVAL,
                errno.ENOTSUP,
                errno.EOPNOTSUPP,
            }:
                raise
    finally:
        os.close(descriptor)


def atomic_write_private(destination: Path, content: str) -> None:
    """Atomically replace a file with durable, owner-only UTF-8 content."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f'.{destination.name}.',
        suffix='.tmp',
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        data = content.encode('utf-8')
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written == 0:
                raise OSError(errno.EIO, 'write returned no data')
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


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
        try:
            atomic_write_private(path, pod_id + '\n')
        except OSError as exc:
            raise RuntimeError(
                f'Cannot persist selected RunPod ID at {path}'
            ) from exc

    def forget_pod_id(self) -> None:
        try:
            (self.directory / POD_STATE_FILE).unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError('Cannot clear persisted RunPod state') from exc

    def read_pod_spec(self) -> str | None:
        path = self.directory / POD_SPEC_FILE
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f'Persisted RunPod specification at {path} is invalid'
            ) from exc
        fingerprint = (
            value.get('fingerprint') if isinstance(value, dict) else None
        )
        if not isinstance(fingerprint, str) or not fingerprint:
            raise RuntimeError(
                f'Persisted RunPod specification at {path} is invalid'
            )
        return fingerprint

    def write_pod_spec(self, fingerprint: str) -> None:
        if not fingerprint:
            raise RuntimeError('Cannot persist an empty RunPod specification')
        try:
            atomic_write_private(
                self.directory / POD_SPEC_FILE,
                json.dumps({'fingerprint': fingerprint}, sort_keys=True)
                + '\n',
            )
        except OSError as exc:
            raise RuntimeError(
                'Cannot persist selected RunPod specification'
            ) from exc

    def forget_pod_spec(self) -> None:
        try:
            (self.directory / POD_SPEC_FILE).unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(
                'Cannot clear persisted RunPod specification'
            ) from exc

    def clear_activation_failure(self) -> None:
        try:
            (self.directory / ACTIVATION_FAILURE_FILE).unlink(missing_ok=True)
        except OSError:
            pass

    def set_activation_status(self, message: str) -> None:
        try:
            path = self.directory / ACTIVATION_STATUS_FILE
            atomic_write_private(path, message.strip() + '\n')
        except OSError:
            pass

    def record_activation_failure(self, message: str) -> None:
        try:
            path = self.directory / ACTIVATION_FAILURE_FILE
            atomic_write_private(path, message.strip() + '\n')
        except OSError:
            pass
