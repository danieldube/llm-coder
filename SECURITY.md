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
- Private GHCR images are pulled by RunPod with a dedicated, read-only package
  token stored as a RunPod registry credential; the local configuration holds
  only its opaque ID.
- OpenCode denies external-directory tools, `.env` reads, `git push`, `sudo`,
  and `ssh`; arbitrary shell/build commands require approval.
- JetBrains custom MCP forwarding is disabled by default.

These controls reduce accidental exposure. They do not prevent an approved
shell command from accessing anything the Unix user can access.

## Dynamic SSH endpoints and host keys

RunPod may assign a different public host or forwarded SSH port when a pod is
restarted. An address is therefore not, by itself, the durable remote identity.
The controller binds the last external SSH endpoint to the persisted provider
pod ID in private state and keeps accepted keys in this installation's
mode-0600 `known_hosts` file.

On first use, a key may be enrolled only after the RunPod API reports the
endpoint for the expected persisted pod. Later connections to the same address
must present the enrolled key; a mismatch fails closed and the controller does
not delete the key or retry. When API metadata reports a changed endpoint, the
controller requires the response to carry the same pod ID, records an audit
status message, removes only the obsolete address from `known_hosts`, and lets
OpenSSH enroll the replacement on first contact. A different pod identity,
malformed local state, or failure to remove the precise old entry aborts the
rotation.

This policy protects continuity at stable endpoints and prevents an endpoint
change from silently authorizing a different RunPod pod. The initial key at a
newly assigned endpoint still relies on the authenticated RunPod control-plane
metadata plus the security of the first SSH connection; RunPod does not provide
an independent host-key fingerprint through this integration.

## Shutdown and integration removal

Idle shutdown and ordinary `llm-down` stop the SSH tunnel and selected RunPod
but intentionally retain the localhost socket integration, OpenCode
configuration, JetBrains ACP registration, installation files, and reusable
Pod identity. The explicit command also stops the socket and proxy for the
current session. To remove the durable IDE integration, use
`llm-down --remove-integration` and confirm the prompt. Removal targets only
the configured llm-coding ACP agent name and preserves unrelated ACP entries;
it does not delete credentials, Pod identity, or remote storage.

### Runtime dependencies

The remote inference runtime is a pinned unit: the RunPod base image, CUDA
runtime, PyTorch CUDA build, vLLM CUDA build, and model revision must be
changed together or explicitly validated for compatibility. Do not repair CUDA
mismatches by copying individual CUDA shared libraries into the image.

The runtime-image publishing workflow uses GitHub Actions' short-lived
`GITHUB_TOKEN`. Do not add a personal GitHub token to repository or Actions
secrets for publishing. A separate classic PAT with only `read:packages` is
appropriate for RunPod to pull a private GHCR image.

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
