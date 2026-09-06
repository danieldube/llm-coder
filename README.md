# llm-coding-python

Python implementation of the llm-coding project that provides the same functionality as the original bash-based version.

## Features

- Full compatibility with the original bash scripts
- Same configuration and behavior
- Python-based implementation for better maintainability
- Unit testing support
- Uses uv for dependency management

## Installation

```bash
# Install in development mode
pip install -e .

# The first `llm-up` creates and enables the required user systemd socket.
# To provision it explicitly instead, run:
llm-install

# Or using uv
uv pip install -e .
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
pip install -r requirements.txt
```
