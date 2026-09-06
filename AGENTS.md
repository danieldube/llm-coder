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

Python 3.11 is the minimum supported version; Python 3.13 is the primary
development version. Ruff and mypy target Python 3.11 so checks enforce the
full supported language and standard-library contract.
All AI agents must use the repository's `.venv` for Python commands and must
never install packages into or run checks with the system Python. Activate it
before working, or invoke its interpreter directly:

```bash
source .venv/bin/activate
python -m pip install -e .
python -m pip install -r requirements.txt
# Alternatively: .venv/bin/python -m unittest discover -s python-src/tests
```

Run the full required validation before handing off a change. These are the
same independently visible checks run by CI:

```bash
.venv/bin/pre-commit run --all-files
.venv/bin/python -m unittest discover -s python-src/tests
bash tests/static-checks.sh
.venv/bin/python -m build
.venv/bin/python -m venv /tmp/llm-coding-wheel
/tmp/llm-coding-wheel/bin/python -m pip install dist/*.whl
for command in llm-up llm-down llm-status llm-doctor llm-install \
  llm-runtime opencode-runpod; do
  /tmp/llm-coding-wheel/bin/"$command" --help >/dev/null
done
docker build --tag llm-coding-runtime:local docker
```

The packaging commands require `build` in `.venv`. The final Docker command is
required when `docker/**`, `.dockerignore`, or the Docker CI definition changes.
Do not suppress lint, formatting, or type checking findings without a narrowly
justified exception; fix the underlying code instead. If a dependency lacks
type information, add its stub package to the mypy hook's
`additional_dependencies` in `.pre-commit-config.yaml`.

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
