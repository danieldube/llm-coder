# Agent instructions

## Scope and invariants

`llm-coding` is a Linux/Python controller for RunPod-hosted vLLM, SSH forwarding,
user-systemd socket activation, and host-native OpenCode/JetBrains ACP.
Python 3.11 is the minimum; 3.13 is the primary development version.

Before editing, inspect `git status` and preserve existing user changes.
Read [SECURITY.md](SECURITY.md) and [architecture](docs/architecture.md) before
changing lifecycle, networking, credentials, systemd, or remote execution.

- Keep the proxy, tunnel, and vLLM listeners on `127.0.0.1`; expose only SSH
  in the Pod request unless explicitly asked to change the security boundary.
- Preserve Pod identity, persistent storage, idempotent lifecycle operations,
  bounded lock acquisition, and fail-closed SSH host-key verification.
- Keep secrets out of logs, examples, generated OpenCode configuration, and
  subprocess environments. Do not inspect or print real credential files.
- Mock RunPod, SSH, systemd, and agent execution in tests. Do not create Pods,
  launch live inference, modify user services, or run uninstall commands as
  routine validation. `llm-doctor --activate` is an operational integration test.
- Keep changes scoped to the request. Record unrelated defects instead of
  silently repairing them. See documented limitations in configuration/setup.

## Source map

| Task | Authoritative source |
| --- | --- |
| Settings, validation, XDG paths | `python-src/llm_coding/config.py` |
| CLI behavior and entry points | `cli.py`, `pyproject.toml` |
| Lifecycle orchestration | `runtime.py` |
| RunPod payloads and response contracts | `runpod.py` |
| User units and systemctl | `systemd.py`; no static unit templates in `systemd/` |
| SSH trust and forwarding | `ssh.py` |
| Locks, Pod identity, diagnostics | `state.py` |
| Status and exit codes | `status.py` |
| OpenCode release allowlist, config, ACP | `opencode.py` |
| Dependency injection protocols | `interfaces.py` |
| Remote build and launcher | `docker/Dockerfile`, `docker/start-vllm.sh` |
| Remote health/restart orchestration | `remote/ensure-vllm.sh` and packaged copy |
| Validation | `python-src/tests/`, `tests/static-checks.sh`, `.github/workflows/ci.yml` |

Python filenames without a directory above are under `python-src/llm_coding/`.
Import focused modules in new code; `core.py` is a compatibility facade.
`python-src/main.py` is a legacy scaffold. `uninstall.sh` is the supported
checkout cleanup script; it must not source configuration or diverge from the
console entry points in `pyproject.toml`.

Installed resources come from `python-src/llm_coding/assets/` through
`importlib.resources`. Keep these pairs byte-identical when editing:

- `config/*` and `python-src/llm_coding/assets/config/*` (matching assets).
- `remote/ensure-vllm.sh` and its `assets/remote/` copy.

The wheel test checks resource inclusion, not equality of duplicate assets.
Keep generated metadata, `build/`, `dist/`, and `.venv/` out of commits.

## Implementation rules

- Use the repository `.venv` for all development Python commands. Never install
  packages into or run checks with system Python. The disposable wheel-test
  environment described in CONTRIBUTING.md is the validation exception.
- Follow Ruff: 79-character Python lines, single quotes, Python 3.11 syntax.
  Type functions fully; pre-commit runs strict mypy. Use `dict[str, Any]` only
  for heterogeneous mappings that require it.
- Prefer `pathlib.Path` and subprocess argument lists. Retain Bash
  `set -euo pipefail`, quote expansions, and preserve remote-shell argument
  safety. Local argument lists alone do not quote arguments for remote SSH.
- Use explicit runtime dependencies for commands, clocks, sleep, and providers.
  Mock direct HTTP readiness calls separately. Preserve module boundaries.
- Fix lint/type findings at their source. Any suppression needs a narrow
  justification. Add missing third-party stubs to the mypy hook's
  `additional_dependencies` in `.pre-commit-config.yaml`.
- Add focused tests for behavior changes; avoid placeholders and unconditional
  skips. Update docs/examples when interfaces, defaults, or boundaries change.
- Treat Docker, CUDA/vLLM, model revision/parser, and OpenCode artifact pins as
  compatibility contracts. Follow SECURITY.md for OpenCode release updates;
  changing version settings alone does not update the remote image.

## Validation and handoff

Setup and exact commands are maintained in
[CONTRIBUTING.md](CONTRIBUTING.md#required-local-validation). Use that sequence:

1. `.venv/bin/pre-commit run --all-files`.
2. `.venv/bin/python -m unittest discover -s python-src/tests`.
3. `bash tests/static-checks.sh`.
4. `.venv/bin/python -m build`, then install the wheel into a fresh temporary
   environment and run all seven installed commands with `--help`.
5. Build the Docker image when Docker inputs or Docker CI change.

Run the full required validation before handing off a change, including docs
changes. Packaging checks may download build/runtime dependencies; they do not
contact RunPod. Static checks require `jq` and run ShellCheck when available.
Do not weaken checks to accommodate an unavailable network or tool; report the
blocker. CI additionally tests Python 3.11/3.13 and rejects installation on 3.10.

Review the final diff for unrelated changes, stale documentation, secrets, and
asset drift. Keep documentation technical and concise; use Mermaid when it
clarifies flows. Use top-to-bottom Mermaid layouts and keep diagrams narrow;
split complex flows into separate diagrams. Verify rendering after edits.
State what changed, checks run, and any checks that could not run. Do not
claim live operational validation from mocked tests.
