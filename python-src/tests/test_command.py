"""Tests for the shared subprocess abstraction."""

# ruff: noqa: PT009

import logging
import subprocess
import sys
import unittest
from unittest.mock import patch

from llm_coding.command import run_command


class CommandRunnerTests(unittest.TestCase):
    def test_returns_text_output_when_captured(self) -> None:
        result = run_command(
            [sys.executable, '-c', "print('text output')"],
            capture_output=True,
        )

        self.assertEqual(result.stdout, 'text output\n')
        self.assertEqual(result.stderr, '')

    def test_checked_failure_raises(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            run_command(
                [sys.executable, '-c', 'raise SystemExit(7)'],
                capture_output=True,
            )

        self.assertEqual(raised.exception.returncode, 7)

    def test_unchecked_failure_is_returned(self) -> None:
        result = run_command(
            [sys.executable, '-c', 'raise SystemExit(8)'],
            check=False,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 8)

    def test_forwards_text_input(self) -> None:
        result = run_command(
            [sys.executable, '-c', 'import sys; print(sys.stdin.read())'],
            input='forwarded input',
            capture_output=True,
        )

        self.assertEqual(result.stdout, 'forwarded input\n')

    def test_capture_behavior_is_forwarded(self) -> None:
        completed = subprocess.CompletedProcess[str]([], 0, '', '')
        with patch(
            'llm_coding.command.subprocess.run', return_value=completed
        ) as run:
            result = run_command(['program'], capture_output=False)

        self.assertIs(result, completed)
        run.assert_called_once_with(
            ['program'],
            check=True,
            capture_output=False,
            input=None,
            text=True,
        )

    def test_diagnostics_are_opt_in_and_redacted(self) -> None:
        command = [
            sys.executable,
            '-c',
            "import sys; print('secret-output'); print('secret-error', file=sys.stderr); raise SystemExit(9)",  # noqa: E501
            'secret-argument',
        ]
        with self.assertNoLogs('llm_coding.command'):
            with self.assertRaises(subprocess.CalledProcessError):
                run_command(command, capture_output=True)

        with self.assertLogs('llm_coding.command', logging.ERROR) as captured:
            with self.assertRaises(subprocess.CalledProcessError):
                run_command(command, capture_output=True, diagnostics=True)

        diagnostic = '\n'.join(captured.output)
        self.assertIn('exit code 9', diagnostic)
        self.assertNotIn('secret-argument', diagnostic)
        self.assertNotIn('secret-output', diagnostic)
        self.assertNotIn('secret-error', diagnostic)
