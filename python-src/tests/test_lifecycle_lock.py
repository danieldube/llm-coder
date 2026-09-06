"""Deterministic lifecycle-lock and concurrent-shutdown tests."""

# ruff: noqa: PT009, PT027

import errno
import fcntl
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from llm_coding import runtime
from llm_coding.state import lifecycle_lock


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class TestLifecycleLock(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_immediate_acquisition_records_operation_and_unlocks(self) -> None:
        with lifecycle_lock(self.directory, 'startup', 1):
            self.assertEqual(
                (self.directory / 'runtime.lock').read_text(), 'startup\n'
            )
        with lifecycle_lock(self.directory, 'shutdown', 1):
            self.assertEqual(
                (self.directory / 'runtime.lock').read_text(), 'shutdown\n'
            )

    def test_contention_retries_nonblocking_then_acquires(self) -> None:
        clock = FakeTime()
        attempts = 0

        def flock(_file: object, operation: int) -> None:
            nonlocal attempts
            if operation & fcntl.LOCK_NB:
                attempts += 1
                if attempts == 1:
                    raise BlockingIOError(errno.EAGAIN, 'busy')

        with patch('llm_coding.state.fcntl.flock', side_effect=flock):
            with lifecycle_lock(
                self.directory,
                'shutdown',
                1,
                clock.monotonic,
                clock.sleep,
            ):
                pass
        self.assertEqual(attempts, 2)
        self.assertEqual(clock.sleeps, [0.1])

    def test_timeout_identifies_competing_operation(self) -> None:
        clock = FakeTime()
        (self.directory / 'runtime.lock').write_text('startup\n')

        def busy(_file: object, operation: int) -> None:
            if operation & fcntl.LOCK_NB:
                raise BlockingIOError(errno.EAGAIN, 'busy')

        with (
            patch('llm_coding.state.fcntl.flock', side_effect=busy),
            self.assertRaisesRegex(
                RuntimeError,
                'Timed out after 0.2 seconds waiting for startup to finish',
            ),
        ):
            with lifecycle_lock(
                self.directory,
                'shutdown',
                0.2,
                clock.monotonic,
                clock.sleep,
            ):
                pass
        self.assertEqual(clock.sleeps, [0.1, 0.1])

    def test_exception_unlocks_closes_and_records_failure(self) -> None:
        lock = MagicMock()
        lock.read.return_value = ''
        failure = MagicMock()
        with (
            patch('llm_coding.state.os.open', return_value=17),
            patch('llm_coding.state.os.fchmod'),
            patch('llm_coding.state.os.fdopen', return_value=lock),
            patch('llm_coding.state.fcntl.flock') as flock,
            self.assertRaisesRegex(ValueError, 'failed startup'),
        ):
            with lifecycle_lock(
                self.directory, 'startup', 1, on_error=failure
            ):
                raise ValueError('failed startup')
        failure.assert_called_once_with('failed startup')
        self.assertEqual(
            flock.call_args_list,
            [
                call(lock, fcntl.LOCK_EX | fcntl.LOCK_NB),
                call(lock, fcntl.LOCK_UN),
            ],
        )
        lock.close.assert_called_once_with()

    def test_shutdown_times_out_without_mutating_during_startup(self) -> None:
        clock = FakeTime()
        manager = MagicMock()
        manager.state_dir = self.directory
        manager.load_settings.return_value = SimpleNamespace(
            lifecycle_lock_timeout_seconds=0.2,
            runpod_api_key='secret',
            runpod_pod_name='model',
        )
        provider = MagicMock()
        systemd = MagicMock()
        dependencies = runtime.RuntimeDependencies(
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            systemd=systemd,
        )

        with (
            lifecycle_lock(self.directory, 'startup', 1),
            patch.object(runtime, 'ConfigManager', return_value=manager),
            self.assertRaisesRegex(RuntimeError, 'startup to finish'),
        ):
            runtime.down(dependencies, provider)

        systemd.run.assert_not_called()
        provider.stop_pod.assert_not_called()
        self.assertFalse((self.directory / 'runtime.env').exists())


if __name__ == '__main__':
    unittest.main()
