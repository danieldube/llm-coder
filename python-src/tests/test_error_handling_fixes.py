#!/usr/bin/env python3
"""Regression contracts for bounded locks and recoverable CLI probes."""

# ruff: noqa: PT009

import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_coding.cli import print_activation_failure
from llm_coding.systemd import UnitState, inspect_unit


class TestSystemdStatus(unittest.TestCase):
    def test_activation_failure_ignores_diagnostics_from_before_attempt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'runtime.activation-error'
            path.write_text('old failure\n')
            mtime_ns = path.stat().st_mtime_ns
            self.assertFalse(
                print_activation_failure(Path(temporary), mtime_ns)
            )

            output = StringIO()
            with redirect_stderr(output):
                self.assertTrue(
                    print_activation_failure(Path(temporary), mtime_ns - 1)
                )
            self.assertIn('old failure', output.getvalue())

    def test_documented_states_are_read_from_show_properties(self) -> None:
        for value in ('active', 'activating', 'inactive', 'failed'):
            with self.subTest(value=value):
                controller = MagicMock()
                controller.run.return_value = CompletedProcess(
                    [],
                    0,
                    f'LoadState=loaded\nActiveState={value}\n'
                    'SubState=running\nInvocationID=abc\n',
                    '',
                )
                result = inspect_unit('example.service', controller)
                self.assertEqual(result.state, UnitState(value))
                controller.run.assert_called_once_with(
                    'show',
                    '--property=LoadState',
                    '--property=ActiveState',
                    '--property=SubState',
                    '--property=InvocationID',
                    'example.service',
                    check=False,
                )

    def test_missing_unit_is_not_a_command_failure(self) -> None:
        controller = MagicMock()
        controller.run.return_value = CompletedProcess(
            [], 1, 'LoadState=not-found\nActiveState=inactive\n', ''
        )
        self.assertEqual(
            inspect_unit('missing.service', controller).state,
            UnitState.NOT_FOUND,
        )

    def test_unavailable_user_systemd_is_inspection_failure(self) -> None:
        controller = MagicMock()
        controller.run.return_value = CompletedProcess(
            [], 1, '', 'Failed to connect to bus: No medium found'
        )
        result = inspect_unit('example.service', controller)
        self.assertEqual(result.state, UnitState.INSPECTION_FAILED)
        self.assertIn('connect to bus', result.detail)


if __name__ == '__main__':
    unittest.main()
