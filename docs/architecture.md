# Architecture

```text
CLion
  | ACP / stdio
  v
OpenCode (host-native)
  |
  | OpenAI-compatible HTTP
  v
127.0.0.1:18000
  |
  | systemd socket activation
  v
systemd-socket-proxyd
  |
  v
127.0.0.1:18001
  |
  | SSH local forwarding
  v
RunPod localhost:8000
  |
  v
vLLM -> Qwen3-Coder
```

## Responsibility split

- **OpenCode**: local coding agent, source tree, local compiler/build/test tools.
- **systemd socket/proxy**: stable local endpoint and lazy activation.
- **GHCR runtime image**: pinned vLLM/CUDA runtime and launcher, published by
  the release workflow.
- **Python RunPod controller**: idempotent pod create/start/stop/discovery.
- **SSH tunnel**: encrypted transport; vLLM is bound only to RunPod localhost.
- **vLLM**: OpenAI-compatible model serving and native tool calling.
- **RunPod persistent volume**: model cache and pinned vLLM virtual environment.

## Lifecycle

1. OpenCode starts immediately through ACP.
2. `opencode-runpod` prewarms `127.0.0.1:18000` in the background.
3. The first connection activates `llm-coding-proxy.service`.
4. The runtime reconciles the mode-0600 Pod ID stored in the user state
   directory with RunPod. A name lookup is used only to adopt an existing Pod
   when no live persisted identity exists; ambiguous matches stop activation.
   Startup, shutdown, installation, and integration removal share a
   nonblocking lifecycle lock. Contending commands retry for the configured
   bounded interval and report which lifecycle operation is in progress;
   shutdown never edits endpoint state or stops a Pod unless it holds the lock.
5. The runtime creates or resumes the selected Pod, discovers its current SSH
   address, and binds that endpoint to the persisted Pod ID. It preserves the
   host key while the endpoint is stable; a provider-confirmed address rotation
   removes only the obsolete entry before enrolling the replacement. It then
   starts prebuilt vLLM and the SSH tunnel.
6. `systemd-socket-proxyd` forwards the already-open client connection to the
   tunnel.
7. After the configured idle period, the proxy exits.
8. `ExecStopPost` stops the tunnel and persisted RunPod, retaining both the
   Pod identity and `/workspace` so the next activation reuses it. This idle
   stop also retains the generated OpenCode configuration, JetBrains ACP
   registration, and installation configuration.

`llm-down` is an explicit stop: it first disables the socket listener and
stops the proxy, then performs the same transient shutdown used by
`ExecStopPost`. It preserves all durable integration and Pod identity state.
`llm-down --remove-integration` additionally prompts before deleting this
installation's generated OpenCode configuration and its named JetBrains ACP
entry. It does not delete unrelated ACP agents, local installation
configuration, credentials, the reusable Pod identity, or remote storage.

## Security boundary

OpenCode does not receive the RunPod API key or the dedicated RunPod SSH key as
environment variables. The packaged `llm-runtime` lifecycle command reads them
from mode-restricted configuration instead. This reduces accidental exposure
but is not isolation: a host-native process still runs with the Unix user's
filesystem authority. The vLLM port is never exposed publicly; only SSH is
exposed by RunPod.

OpenCode itself is not an OS sandbox. Its shell/file permission policy is a
human-approval mechanism. Use this setup only with repositories you trust, or
add an OS-level sandbox/container/VM when handling untrusted repositories.

## Runtime image releases

`docker/` is built only when its Docker inputs change on `main` or through an
explicit GitHub Actions dispatch, and is published to GitHub Container Registry
(GHCR). The resulting image must be referenced by its immutable `sha-<commit>`
tag in `RUNPOD_IMAGE`; never use `latest`. For a private GHCR image, configure
a RunPod registry credential with a dedicated read-only `read:packages` token
and set its opaque credential ID in `RUNPOD_CONTAINER_REGISTRY_AUTH_ID`. The
token is stored by RunPod and is never written to local configuration or passed
into the Pod.

## Python module boundaries

The local controller keeps side effects behind focused modules:

- `config.py` owns immutable `Settings`, validation, and XDG paths.
- `runpod.py` owns typed provider transport and response contracts.
- `systemd.py` owns unit rendering and user `systemctl` calls.
- `ssh.py` owns transient-host authentication and tunnel commands.
- `opencode.py` owns installation, generated configuration, and ACP state.
- `state.py` atomically persists private pod identity and activation status.
- `runtime.py` composes those services into lifecycle operations, while
  `cli.py` presents Click commands and output.

Runtime orchestration accepts explicit command, monotonic-clock, sleep, systemd,
and provider dependencies. Tests can therefore supply deterministic fakes
without replacing module globals or contacting RunPod, SSH, or systemd.

## Activation diagnostics

The lifecycle command writes a non-sensitive activation failure to private
state before systemd marks the proxy failed. `llm-up` reads that diagnostic and
reports it with the current activation stage. Known RunPod capacity failures are
translated into retry and availability guidance; other provider errors retain
their provider detail. OpenCode's background prewarm surfaces failed `llm-up`
output on standard error without delaying OpenCode startup.

## Status contract

`llm-status` reads `LoadState`, `ActiveState`, and `SubState` from user systemd
and looks up the persisted RunPod ID through the typed provider client. It does
not select a pod or silently substitute a same-named pod. When no identity is
persisted, a name query is used only to diagnose absent or ambiguous legacy
pods. Output distinguishes inactive, activating, failed, missing, and
uninspectable units; an absent selection, a deleted selected pod, ambiguous
legacy names, provider API/protocol failures, and the provider lifecycle value.

The command's exit codes are stable for automation: `0` means every component
is healthy, `1` means the stack is wholly inactive, `2` means inspection
succeeded but components are inconsistent or degraded, and `3` means at least
one systemd, provider, or endpoint inspection could not be completed.
