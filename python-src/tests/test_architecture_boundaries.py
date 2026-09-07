"""Static contracts for focused module responsibility boundaries."""

# ruff: noqa: PT009

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_coding import cli, core, opencode, runpod, runtime, ssh, systemd

REPOSITORY_ROOT = Path(__file__).parents[2]


def _source(module: object) -> str:
    return Path(module.__file__).read_text()  # type: ignore[attr-defined]


def _definitions(module: object) -> set[str]:
    return {
        node.name
        for node in ast.walk(ast.parse(_source(module)))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


class TestArchitectureBoundaries(unittest.TestCase):
    def test_config_manager_has_one_authoritative_implementation(self) -> None:
        implementations = []
        for path in (REPOSITORY_ROOT / 'python-src').rglob('*.py'):
            tree = ast.parse(path.read_text(), filename=str(path))
            if any(
                isinstance(node, ast.ClassDef) and node.name == 'ConfigManager'
                for node in ast.walk(tree)
            ):
                implementations.append(path.relative_to(REPOSITORY_ROOT))

        self.assertEqual(
            implementations,
            [Path('python-src/llm_coding/config.py')],
        )

    def test_cli_is_presentation_only(self) -> None:
        source = _source(cli)
        self.assertNotIn('fcntl.flock', source)
        self.assertNotIn('StrictHostKeyChecking=', source)
        self.assertNotIn("'gpuTypePriority':", source)
        for service in ('ensure_socket', 'runtime_down', 'runtime_install'):
            self.assertIn(f'{service}(', source)

    def test_operational_implementations_have_focused_owners(self) -> None:
        self.assertIn("'gpuTypePriority':", _source(runpod))
        self.assertIn('StrictHostKeyChecking=', _source(ssh))
        self.assertIn('def render_units(', _source(systemd))
        self.assertIn('def ensure_acp_registration(', _source(opencode))
        self.assertNotIn("'gpuTypePriority':", _source(runtime))
        self.assertNotIn('StrictHostKeyChecking=', _source(runtime))

    def test_core_is_only_a_compatibility_facade(self) -> None:
        self.assertTrue(
            {'RunPodClient', 'create_opencode_config'}.isdisjoint(
                _definitions(core)
            )
        )

    def test_runtime_is_orchestration_only(self) -> None:
        forbidden = {
            'render_units',
            'pod_create_body',
            'command',
            'create_config',
        }
        self.assertTrue(forbidden.isdisjoint(_definitions(runtime)))
        self.assertIn('class RuntimeDependencies', _source(runtime))


if __name__ == '__main__':
    unittest.main()
