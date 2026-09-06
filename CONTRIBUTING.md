# Project Contribution Guidelines

## Code Style and Standards

All code must comply with PEP8 standards with 79 character line limits.

## Pre-commit Checks

It's not allowed to suppress pre-commit findings - unless some rare edge cases.
Normally the implementation must be changed so no findings show up.

## Required local validation

Create the repository environment and install the development dependencies:

Use Python 3.11 or newer. CI tests the minimum (3.11) and primary development
version (3.13); local static analysis targets the minimum version.

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -e .
.venv/bin/python -m pip install build
```

Run the same validation contracts that CI runs:

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
```

The clean wheel environment is deliberately outside `.venv`: it catches
missing package data and source-layout leaks. It is disposable and must not be
reused as a development environment. When Docker inputs change, also run:

```bash
docker build --tag llm-coding-runtime:local docker
```

All behavior changes must be accompanied by focused unit tests.

## Updating the OpenCode release

OpenCode installation uses the reviewed release allowlist in
`python-src/llm_coding/opencode.py`, rather than an upstream installer script.
For a version update, review the tagged `anomalyco/opencode` GitHub release and
security advisories, download each supported CLI archive directly, and compute
its SHA-256 digest independently with `sha256sum`. Add all platform/architecture
artifact names and digests under the new version in `_RELEASE_ARTIFACTS`, then
update `OPENCODE_VERSION` in both example configuration files. Treat missing
platform artifacts as unsupported rather than copying a digest from another
build. Run the full validation above and `llm-doctor --activate`; the detailed
provenance and trust requirements are documented in `SECURITY.md`.

## Deferred regression backlog

Tests must state an executable contract and must never be placeholders that
fail or skip unconditionally. The audit of the former gap suites retained the
following ideas here until a remediation defines observable behavior:

- **TEST-GAP-001 — concurrent systemd operations:** define serialization and
  expected state transitions for simultaneous install/start/stop requests.
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
