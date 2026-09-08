# Contributing

Follow [AGENTS.md](AGENTS.md) for source ownership and operational invariants.
Use Ruff's 79-character Python lines and single quotes, fully typed functions,
and strict mypy. Fix findings rather than suppressing them unless a narrow
exception is justified. Keep prose concise and use Mermaid for useful diagrams.

## Required local validation

Create the repository environment with Python 3.11 or newer and install the
development dependencies. CI tests the minimum (3.11) and primary development
version (3.13); local static analysis targets the minimum version.

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -e .
```

Run the following checks from the checkout. `jq` is required for static
checks; ShellCheck is used when available. Pre-commit and packaging can need
network access to download dependencies. The unit suite includes a wheel
build/install test but mocks operational RunPod, SSH, and systemd calls.

```bash
.venv/bin/pre-commit run --all-files
.venv/bin/python -m unittest discover -s python-src/tests
bash tests/static-checks.sh
.venv/bin/python -m build
llm_wheel_env="$(mktemp -d /tmp/llm-coding-wheel.XXXXXX)"
.venv/bin/python -m venv "$llm_wheel_env"
"$llm_wheel_env/bin/python" -m pip install dist/*.whl
for command in llm-up llm-down llm-status llm-doctor llm-install \
  llm-runtime opencode-runpod; do
  "$llm_wheel_env/bin/$command" --help >/dev/null
done
```

The clean wheel environment is deliberately outside `.venv`: it catches
missing package data and source-layout leaks. It is disposable and must not be
reused as a development environment. Use a fresh build output directory if
`dist/` contains older wheels: the install glob must select only this build.
CI also verifies that Python 3.10 rejects the wheel via `Requires-Python`.
When `docker/**`, `.dockerignore`, or Docker CI changes, also run:

```bash
docker build --tag llm-coding-runtime:local docker
```

All behavior changes require focused unit tests. Do not run live activation
as routine validation. `python-src/llm_coding/assets/` is the single canonical
location for configuration examples and remote scripts. Packaging tests compare
every file there byte-for-byte with its wheel entry. Report failed or unavailable
checks in the handoff.

## Updating the OpenCode release

OpenCode installation uses the reviewed release allowlist in
`python-src/llm_coding/opencode.py`, rather than an upstream installer script.
For a version update, review the tagged `anomalyco/opencode` GitHub release and
security advisories, download each supported CLI archive directly, and compute
its SHA-256 digest independently with `sha256sum`. Add all platform/architecture
artifact names and digests under the new version in `_RELEASE_ARTIFACTS`, then
update `OPENCODE_VERSION` in both example configuration files. Treat missing
platform artifacts as unsupported rather than copying a digest from another
build. Run the full validation above. In an explicitly selected operational
environment, also run `llm-doctor --activate`. See `SECURITY.md` for provenance
and trust requirements.

## Deferred regression backlog

Tests must state an executable contract and must never be placeholders that
fail or skip unconditionally. The audit of the former gap suites retained the
following ideas here until a remediation defines observable behavior:

- **TEST-GAP-001 — concurrent systemd operations:** the runtime lifecycle lock
  is tested, but CLI unit reconciliation and socket/proxy stops occur outside
  it. Define and test the complete command-level concurrency contract.
- **TEST-GAP-002 — SSH child lifecycle:** define process ownership, termination,
  and reaping requirements before adding a subprocess-leak regression.
- **TEST-GAP-003 — degraded dependencies:** enumerate which missing optional
  dependencies permit operation and the exact diagnostic for each command.
- **TEST-GAP-004 — adaptive timeouts:** specify the signal, bounds, and user
  override semantics if adaptive activation timeouts are introduced.
- **TEST-GAP-005 — error-message consistency:** establish structured error
  categories before asserting presentation text across unrelated commands.

Configuration validation, bounded lock acquisition, rejected lock-handle
cleanup, recoverable SSH/systemd probes, and atomic state-file cleanup have
concrete regression contracts in the test suite and are not duplicated here.

## Known implementation gaps

- Fresh `llm-install` passes a single path to `_asset_text`, which requires
  separate package/name arguments. Missing templates fail before validation;
  the README documents manual template creation.
- Configuration is inherited separately by CLI and systemd; secret environment
  variables are not filtered when launching OpenCode.
- Status reports normal idle standby as degraded, and configuration errors
  return the same exit code as an inactive stack.
