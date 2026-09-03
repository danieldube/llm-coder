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
- **RunPod controller scripts**: idempotent pod create/start/stop/discovery.
- **SSH tunnel**: encrypted transport; vLLM is bound only to RunPod localhost.
- **vLLM**: OpenAI-compatible model serving and native tool calling.
- **RunPod persistent volume**: model cache and pinned vLLM virtual environment.

## Lifecycle

1. OpenCode starts immediately through ACP.
2. `opencode-runpod` prewarms `127.0.0.1:18000` in the background.
3. The first connection activates `llm-coding-proxy.service`.
4. `runtime-up.sh` creates/resumes the named Pod, discovers its current SSH
   address, starts prebuilt vLLM, and starts the SSH tunnel.
5. `systemd-socket-proxyd` forwards the already-open client connection to the
   tunnel.
6. After the configured idle period, the proxy exits.
7. `ExecStopPost` stops the tunnel and RunPod, retaining `/workspace`.

## Security boundary

OpenCode does not receive the RunPod API key or the dedicated RunPod SSH key as
environment variables. Lifecycle scripts read them instead. This reduces accidental
exposure but is not isolation: a host-native process still runs with the Unix user's filesystem authority. The vLLM port is never exposed publicly; only SSH is
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
