"""Static contracts for the CLI/runtime responsibility boundary."""

# ruff: noqa: PT009

import ast
import sys
import unittest
from pathlib import Path

# Support direct unittest discovery without requiring an editable install.
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_coding import cli, core, runtime


def _source(module: object) -> str:
    path = Path(module.__file__)  # type: ignore[attr-defined]
    return path.read_text()


def _definitions(source: str) -> set[str]:
    return {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class TestArchitectureBoundaries(unittest.TestCase):
    """Prevent operational implementations from drifting back into the CLI."""

    def test_obsolete_cli_service_symbols_are_not_public_imports(self) -> None:
        obsolete = {
            'acquire_lock',
            'release_lock',
            'create_pod_config',
            'wait_for_ssh_connection',
            'ensure_vllm_on_remote',
            'find_pod_by_name',
            'runpod_api',
        }
        self.assertTrue(obsolete.isdisjoint(vars(cli)))

    def test_cli_commands_delegate_to_runtime_services(self) -> None:
        source = _source(cli)
        for service in (
            'ensure_socket',
            'runtime_down',
            'runtime_install',
            'runtime_remove_integration',
        ):
            with self.subTest(service=service):
                self.assertIn(f'{service}(', source)

    def test_operational_implementations_have_one_owner(self) -> None:
        cli_source = _source(cli)
        core_source = _source(core)
        runtime_source = _source(runtime)

        self.assertNotIn('fcntl.flock', cli_source)
        self.assertEqual(runtime_source.count('fcntl.flock'), 1)
        self.assertNotIn('StrictHostKeyChecking=', cli_source)
        self.assertEqual(runtime_source.count('StrictHostKeyChecking='), 1)
        self.assertNotIn("'gpuTypePriority':", cli_source)
        self.assertEqual(runtime_source.count("'gpuTypePriority':"), 1)

        owners = [
            source
            for source in (cli_source, core_source, runtime_source)
            if 'def find_pod_by_name(' in source
        ]
        self.assertEqual(owners, [core_source])

    def test_cli_definitions_are_not_runtime_primitives(self) -> None:
        forbidden = {
            'acquire_lock',
            'release_lock',
            'create_pod_config',
            'wait_for_ssh_connection',
            'ensure_vllm_on_remote',
            'find_pod_by_name',
            'runpod_api',
            '_pod_create_body',
            '_ssh_base',
            '_lifecycle_lock',
        }
        self.assertTrue(forbidden.isdisjoint(_definitions(_source(cli))))


if __name__ == '__main__':
    unittest.main()
