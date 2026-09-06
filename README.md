# llm-coding-python

Python implementation of the llm-coding project that provides the same functionality as the original bash-based version.

## Features

- Full compatibility with the original bash scripts
- Same configuration and behavior
- Python-based implementation for better maintainability
- Unit testing support
- Uses uv for dependency management

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

# The first `llm-up` creates and enables the required user systemd socket.
# To provision it explicitly instead, run:
.venv/bin/llm-install

# Or using uv
uv venv --python 3.11
uv pip install --python .venv/bin/python -e .
```

## Usage

```bash
# Run the commands (same as original bash scripts)
llm-up
llm-down
# Stop and also remove this project's durable IDE integration (prompts):
llm-down --remove-integration
llm-status
llm-doctor
opencode-runpod
```

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

# Install dependencies
.venv/bin/python -m pip install -r requirements.txt
```
