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
llm-status
llm-doctor
opencode-runpod
```

## Development

```bash
# Run tests
make test

# Install dependencies
pip install -r requirements.txt
```
