# Security notes

This stack deliberately runs OpenCode directly as the logged-in Unix user so it
can use the existing source tree and development tools. It is therefore **not a
sandbox**.

## Trust assumptions

- Repositories opened with the agent are trusted.
- The local Unix account and workstation are trusted.
- RunPod is trusted to process source snippets sent to the self-hosted model.
- The dedicated SSH tunnel protects transport to the Pod; it does not hide data
  from the RunPod infrastructure operator.

## Default reductions in exposure

- vLLM binds to `127.0.0.1` inside the Pod; only SSH is publicly exposed.
- The RunPod API key is stored in a separate mode-0600 file and is not exported
  to OpenCode.
- A dedicated SSH key is used for this stack rather than the developer's main
  SSH identity.
- OpenCode denies external-directory tools, `.env` reads, `git push`, `sudo`,
  and `ssh`; arbitrary shell/build commands require approval.
- JetBrains custom MCP forwarding is disabled by default.

These controls reduce accidental exposure. They do not prevent an approved
shell command from accessing anything the Unix user can access.

### Runtime dependencies

The remote inference runtime is a pinned unit: the RunPod base image, CUDA
runtime, PyTorch CUDA build, vLLM CUDA build, and model revision must be
changed together or explicitly validated for compatibility. Do not repair CUDA
mismatches by copying individual CUDA shared libraries into the image.

## Untrusted repositories

Do not use this host-native configuration for an untrusted repository. A
repository can contain build logic, instructions, OpenCode configuration,
plugins, or content designed to influence an agent. Use a VM/container or other
OS-level sandbox when repository trust is not established.

## Updating OpenCode

OpenCode has previously shipped security fixes affecting local command
execution. Keep `OPENCODE_VERSION` pinned, review upstream security advisories,
then deliberately update the pin and run `make check` plus
`llm-doctor --activate`.
