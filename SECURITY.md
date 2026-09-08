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
- Store the RunPod API key in a separate mode-0600 file. File-loaded settings
  are not exported to OpenCode, but the wrapper inherits the parent environment
  without filtering: an exported API key will reach OpenCode. The loader does
  not enforce permissions on existing configuration files.
- A dedicated SSH key is used for this stack rather than the developer's main
  SSH identity.
- Private GHCR images are pulled by RunPod with a dedicated, read-only package
  token stored as a RunPod registry credential; the local configuration holds
  only its opaque ID.
- OpenCode denies external-directory tools, `.env` reads, `git push`, `sudo`,
  and `ssh` in the generated permission policy; most shell/build commands
  require approval. Read-oriented commands such as `rg`, `grep`, and selected
  `git` operations are allowed. OpenCode can merge other configuration sources,
  so the generated file is not an exclusive policy boundary. See the upstream
  [configuration rules](https://opencode.ai/docs/config/).
- JetBrains custom MCP forwarding is disabled by default.

The localhost HTTP endpoint has no API authentication; other local processes
can use it and trigger activation. The vLLM launcher enables request logging
to `/workspace/llm-coding/vllm.log`, which may retain prompts and source code.
Treat remote logs and copied journal diagnostics as sensitive. Runtime error
reporting can include provider response bodies and remote startup log excerpts.

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

When a selected model's persisted Pod specification differs from the current
model contract, the controller stops the old Pod, creates a replacement, and
removes only the old Pod's recorded endpoint before enrolling the new one. It
does not delete either Pod or any volume. This identity transition is allowed
only by the model compatibility check while the lifecycle lock is held; an
unrelated saved endpoint still fails closed.

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

## Runtime dependencies

Treat the RunPod base image, CUDA runtime, PyTorch/vLLM CUDA builds, model
revision, and tool parser as one compatibility set when making changes. The
base image uses a tag and transitive packages are resolved during image builds;
SHA-derived image tags are not immutable. Use a published digest to select
fixed image bytes. The controller does not validate the installed versions
against local settings. Do not repair CUDA mismatches by copying individual
CUDA shared libraries into the image.

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

New OpenCode downloads come exclusively from the versioned upstream
GitHub release at `anomalyco/opencode`; the project does not execute the remote
installer. `python-src/llm_coding/opencode.py` is the authoritative allowlist
of release versions, platform/architecture artifact names, and SHA-256 hashes.
The downloader follows redirects with status checks, connect/read timeouts,
and download/executable size limits, then verifies the allowlisted digest
before safely extracting and atomically replacing the existing executable. An existing executable reporting
the requested version is reused without digest verification; these checks
protect new downloads, not an already-compromised local installation.

To update OpenCode, review upstream security advisories and the tagged release,
download every supported CLI archive directly from that release, and calculate
each SHA-256 digest independently (for example, with `sha256sum`). Add the new
version and every supported platform/architecture pair to
`_RELEASE_ARTIFACTS`, update both checked-in `OPENCODE_VERSION` examples, and
review the resulting artifact-name and digest diff. Never derive a trusted
digest from the artifact being verified at install time. Run the full checks
in `CONTRIBUTING.md`, install into a clean environment, and verify with
`llm-doctor --activate` before release.
