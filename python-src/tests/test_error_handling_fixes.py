#!/usr/bin/env python3
"""Regression contracts for bounded locks and recoverable CLI probes."""

# ruff: noqa: PT009

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_coding.cli import get_systemd_service_info


class TestFixedBareExceptClauses(unittest.TestCase):
    def test_get_systemd_service_info_handles_exceptions(self) -> None:
        """Unavailable systemd state has one stable empty-value contract."""
        with patch('llm_coding.cli.run_command') as mock_run:
            mock_run.side_effect = Exception('Simulated subprocess error')
            result = get_systemd_service_info('nonexistent.service')
            self.assertEqual(result, ('', ''))


if __name__ == '__main__':
    unittest.main()
