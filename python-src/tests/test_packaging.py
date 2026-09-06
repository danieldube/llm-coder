"""End-to-end checks for the distributable wheel."""

import os
import subprocess
import sys
import tempfile
import unittest
import venv
import zipfile
from pathlib import Path


class WheelPackagingTests(unittest.TestCase):
    """Verify entry points and resources outside the source checkout."""

    def test_wheel_installs_with_commands_and_assets(self) -> None:
        repository = Path(__file__).parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheel_dir = root / 'wheels'
            subprocess.run(
                [
                    sys.executable,
                    '-m',
                    'pip',
                    'wheel',
                    '--no-deps',
                    '--wheel-dir',
                    str(wheel_dir),
                    str(repository),
                ],
                check=True,
                cwd=root,
            )
            wheel = next(wheel_dir.glob('llm_coding-*.whl'))
            required_assets = {
                'llm_coding/assets/config/config.env.example',
                'llm_coding/assets/config/secrets.env.example',
                'llm_coding/assets/config/opencode.base.json',
                'llm_coding/assets/remote/ensure-vllm.sh',
            }
            with zipfile.ZipFile(wheel) as archive:
                assert required_assets.issubset(archive.namelist())

            environment = root / 'environment'
            venv.EnvBuilder(with_pip=True).create(environment)
            scripts = environment / ('Scripts' if os.name == 'nt' else 'bin')
            python = scripts / ('python.exe' if os.name == 'nt' else 'python')
            subprocess.run(
                [str(python), '-m', 'pip', 'install', str(wheel)],
                check=True,
                cwd=root,
            )
            for command in (
                'llm-up',
                'llm-down',
                'llm-status',
                'llm-doctor',
                'llm-install',
                'llm-runtime',
                'opencode-runpod',
            ):
                subprocess.run(
                    [str(scripts / command), '--help'],
                    check=True,
                    cwd=root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )

            probe = (
                "import importlib.resources as r; "
                "assert r.read_text('llm_coding.assets.remote', "
                "'ensure-vllm.sh'); "
                "assert r.read_text('llm_coding.assets.config', "
                "'opencode.base.json')"
            )
            subprocess.run([str(python), '-c', probe], check=True, cwd=root)


if __name__ == '__main__':
    unittest.main()
