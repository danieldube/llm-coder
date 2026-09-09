"""Regression coverage for configuration errors reported by CLI commands."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from llm_coding.cli import llm_up


class LlmUpConfigurationTests(unittest.TestCase):
    """Verify llm-up presents setup failures without a Python traceback."""

    def test_missing_configuration_files_explain_how_to_recover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch('llm_coding.cli.check_dependencies'):
                result = CliRunner().invoke(
                    llm_up, env={'XDG_CONFIG_HOME': directory}
                )

        assert result.exit_code == 1
        assert 'Missing configuration files' in result.output
        assert 'Create config.env and secrets.env' in result.output
        assert 'Traceback' not in result.output

    def test_invalid_configuration_is_a_click_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory) / 'llm-coding'
            config_dir.mkdir()
            (config_dir / 'config.env').write_text(
                'RUNPOD_IMAGE_REPOSITORY=\n'
            )
            (config_dir / 'secrets.env').write_text(
                'RUNPOD_API_KEY=test-key\n'
            )
            with patch('llm_coding.cli.check_dependencies'):
                result = CliRunner().invoke(
                    llm_up, env={'XDG_CONFIG_HOME': directory}
                )

        assert result.exit_code == 1
        assert 'Error: Invalid configuration:' in result.output
        assert 'RUNPOD_IMAGE_REPOSITORY is required' in result.output
        assert 'Update the files in' in result.output
        assert 'Traceback' not in result.output


if __name__ == '__main__':
    unittest.main()
