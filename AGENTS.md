# AGENTS.md

## Project overview

`llm-coding` is a Python implementation of a local coding-agent runtime. It
manages a RunPod-hosted vLLM service, an SSH tunnel, and user-level systemd
socket activation. The command-line entry points include `llm-up`, `llm-down`,
`llm-status`, `llm-doctor`, `llm-install`, and `opencode-runpod`.

Treat changes to lifecycle, networking, systemd, and remote-runtime code as
operationally sensitive: preserve idempotency, avoid exposing credentials, and
keep the vLLM endpoint bound to localhost unless the change explicitly requires
otherwise. Read [SECURITY.md](SECURITY.md) and [docs/architecture.md](docs/architecture.md)
before changing those areas.

## Repository layout

- `python-src/llm_coding/`: Python package and CLI implementation.
- `python-src/tests/`: `unittest` test suite.
- `remote/`: scripts executed on the RunPod machine; `ensure-vllm.sh` provisions
  and starts vLLM.
- `systemd/`: templates and units for the user socket, proxy, and tunnel.
- `config/`: checked-in example configuration and OpenCode configuration.
- `tests/static-checks.sh`: syntax and configuration checks for shell/JSON
  assets.
- `docs/`: architecture and reference documentation.

## Development workflow

Use Python 3.8+ for compatibility; tooling is configured for Python 3.13.
Install the project and development dependencies in an isolated environment:

```bash
python3 -m pip install -e .
python3 -m pip install -r requirements.txt
```

Run the relevant tests from the Python source directory:

```bash
cd python-src
python3 -m unittest discover -s tests
```

Run the full required validation before handing off a change:

```bash
pre-commit run --all-files
bash tests/static-checks.sh
```

CI runs `pre-commit run --all-files`. Do not suppress lint, formatting, or type
checking findings without a narrowly justified exception; fix the underlying
code instead. If a dependency lacks type information, add its stub package to
the mypy hook's `additional_dependencies` in `.pre-commit-config.yaml`. For
example, resolve `Library stubs not installed for "requests"` by adding
`types-requests` to that list, then rerun `pre-commit run --all-files`.

## Implementation standards

- Follow the repository's Ruff configuration: 79-character lines, single
  quotes, and the configured lint rules.
- Keep public and internal Python functions fully typed. Mypy is strict; use
  `dict[str, Any]` for heterogeneous JSON/config mappings when needed.
- Prefer `pathlib.Path` for filesystem operations and `subprocess.run()` with
  argument lists rather than shell strings.
- Handle failures explicitly and include useful, non-sensitive diagnostics.
  Never log API keys, SSH private-key contents, or environment-file values.
- Preserve existing CLI behaviour and configuration compatibility unless a
  deliberate breaking change is requested.
- Keep shell scripts POSIX-aware where practical, quote expansions, and retain
  `set -euo pipefail` in Bash scripts. Ensure modified scripts pass `bash -n`;
  `shellcheck` is used automatically by the static checks when available.

## Tests and documentation

- Add or update focused unit tests for behaviour changes. Mock subprocess,
  network, filesystem, and systemd interactions; tests must not create real
  pods, tunnels, or user services.
- Update example configuration, systemd templates, and architecture/security
  documentation whenever an operational interface or security boundary changes.
- Do not add secrets to the repository. Use the example environment files as
  templates and keep real credential files private and mode-restricted.

## Change hygiene

- Keep diffs scoped to the requested work; do not reformat or alter unrelated
  files.
- Inspect existing changes before editing and preserve user-authored work.
- State which validation commands were run and any checks that could not run in
  the final handoff.
