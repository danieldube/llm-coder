#!/usr/bin/env python3
"""Regression contracts for bounded locks and recoverable CLI probes."""

# ruff: noqa: PT009

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_coding.cli import get_systemd_service_info, wait_for_ssh_connection
from llm_coding.runtime import _try_acquire_lock


class TestFixedBareExceptClauses(unittest.TestCase):
    def test_ssh_probe_returns_false_after_recoverable_command_error(
        self,
    ) -> None:
        """A failed probe is retried until its caller-supplied deadline."""
        with (
            patch(
                'llm_coding.cli.run_command',
                side_effect=RuntimeError('ssh is unavailable'),
            ) as run_command,
            patch('llm_coding.cli.time.time', side_effect=[10.0, 10.0, 11.0]),
            patch('llm_coding.cli.time.sleep') as sleep,
        ):
            result = wait_for_ssh_connection('localhost', 22, timeout=1)

        self.assertFalse(result)
        run_command.assert_called_once()
        sleep.assert_called_once_with(2)

    def test_get_systemd_service_info_handles_exceptions(self) -> None:
        """Unavailable systemd state has one stable empty-value contract."""
        with patch('llm_coding.cli.run_command') as mock_run:
            mock_run.side_effect = Exception('Simulated subprocess error')
            result = get_systemd_service_info('nonexistent.service')
            self.assertEqual(result, ('', ''))

    def test_try_acquire_lock_with_timeout(self) -> None:
        """A contended lock times out and closes every rejected handle."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / 'test.lock'
            result = _try_acquire_lock(lock_path, timeout_seconds=1)
            self.assertIsNotNone(result)
            assert result is not None
            result.close()  # Release the lock

        rejected = (MagicMock(), MagicMock())
        with (
            patch('llm_coding.runtime.open', side_effect=rejected),
            patch(
                'llm_coding.runtime.fcntl.flock',
                side_effect=BlockingIOError,
            ),
            patch(
                'llm_coding.runtime.time.time',
                side_effect=[10.0, 10.0, 10.5, 11.0],
            ),
            patch('llm_coding.runtime.time.sleep') as sleep,
        ):
            self.assertIsNone(
                _try_acquire_lock(Path('runtime.lock'), timeout_seconds=1)
            )

        self.assertEqual(sleep.call_args_list, [call(0.1), call(0.1)])
        for handle in rejected:
            handle.close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
