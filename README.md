# llm-coding

Local coding-agent runtime backed by a RunPod-hosted vLLM service.

## Features

- Python console scripts manage installation, activation, status, and shutdown.
- A stable localhost endpoint uses user-systemd socket activation.
- Pod identity and SSH endpoint state are persisted privately between runs.
- vLLM stays bound to the Pod's loopback interface and is reached by SSH.

## Installation

Python 3.11 or newer is required. Python 3.11 is the production minimum used
by the runtime image and CI, while Python 3.13 is the primary development
version. Packaging metadata rejects installation on older interpreters.

The local commands require Linux with a user systemd session, OpenSSH, and
`curl`. Docker is required only to build the RunPod vLLM runtime image. On an
Ubuntu 24.04 workstation, install the system prerequisites with:

```bash
sudo apt-get install python3 python3-venv openssh-client curl
python3 --version  # must report 3.11 or newer
```

Create a virtual environment with a supported interpreter before installing:

```bash
# Install in development mode
python3 -m venv .venv
.venv/bin/python -m pip install -e .

# Install the generated user-systemd units and local integration.
.venv/bin/llm-install

# Or using uv
uv venv --python 3.11
uv pip install --python .venv/bin/python -e .
```

## Usage

The installed command names are:

| Command | Purpose |
| --- | --- |
| `llm-install` | Generate and enable the user-systemd socket and integration. |
| `llm-up` | Install if needed and activate the runtime. |
| `llm-down` | Stop the socket, proxy, tunnel, and selected Pod for this session. |
| `llm-status` | Inspect systemd, the persisted Pod ID, and provider state. |
| `llm-doctor` | Validate configuration and dependencies (`--activate` also starts the runtime). |
| `llm-runtime` | Internal systemd lifecycle entry point. |
| `opencode-runpod` | Start OpenCode after prewarming the local endpoint. |

Ordinary and idle shutdowns are transient: they retain the generated
integration, installation configuration, remote storage, and reusable Pod ID.
Use `llm-down --remove-integration` to remove the generated OpenCode file and
this installation's named JetBrains ACP entry as well. Credentials, Pod state,
remote storage, and unrelated ACP entries are never removed by that option.

## Startup failures

`llm-up` reports the failed activation stage and a saved runtime diagnostic.
When RunPod has no capacity for the configured GPU and cloud type, retry later;
if it persists, check availability in RunPod before changing configuration.
For other failures, use the command printed by `llm-up` to inspect the user
service journal. `opencode-runpod` reports a failed background prewarm on
standard error while OpenCode starts immediately.

## Runtime image

RunPod starts the prebuilt vLLM image published to GHCR rather than installing
vLLM during Pod activation. The **Publish runtime image** workflow runs only
when its Docker inputs change on `main`, or when manually dispatched. Set
`RUNPOD_IMAGE` to its emitted immutable `sha-<commit>` tag. For a private image,
create a RunPod registry credential using a dedicated GitHub token with only
`read:packages`, then set its credential ID as
`RUNPOD_CONTAINER_REGISTRY_AUTH_ID`. Never put the GitHub token in either
configuration file.

## Development

```bash
# Run tests
make test

# Install development-only tools (runtime dependencies come from pyproject.toml)
.venv/bin/python -m pip install -r requirements.txt
```
