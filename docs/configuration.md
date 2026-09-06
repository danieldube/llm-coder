# Configuration

Configuration is read from `~/.config/llm-coding/config.env`, followed by
`secrets.env`; explicitly set supported environment variables have highest
precedence. Parsing and validation complete before runtime, network, systemd,
SSH-key, ACP, or OpenCode side effects. Errors are reported together and secret
values are never included.

## Schema

Required strings are `RUNPOD_API_KEY`, `RUNPOD_SSH_KEY`, `RUNPOD_POD_NAME`,
`RUNPOD_IMAGE`, `RUNPOD_GPU_TYPE`, `OPENCODE_VERSION`, `VLLM_VERSION`,
`VLLM_CUDA_VERSION`, `MODEL_ID`, `SERVED_MODEL_NAME`, and
`MODEL_DISPLAY_NAME`. `MODEL_REVISION`, `RUNPOD_NETWORK_VOLUME_ID`, and
`RUNPOD_CONTAINER_REGISTRY_AUTH_ID` may be empty. Placeholder API secrets such
as `REPLACE_ME` are rejected. Paths expand `$VARIABLE` and `~` once when parsed.

`RUNPOD_CLOUD_TYPE` is `SECURE` (default) or `COMMUNITY`. Boolean values accept
only `true` or `false`. Ports are integers from 1 through 65535, and the local
proxy and tunnel ports must differ. Positive capacity settings are bounded:
container disk 1–2048 GiB, volume 1–65536 GiB, RAM 1–4096 GiB, and vCPU 1–1024.
GPU memory utilization is greater than zero and at most one. Context and output
limits are 1–10,000,000 tokens, with output not exceeding context. Both startup
timeouts are 1–86400 seconds. `IDLE_SHUTDOWN` is a positive systemd duration
using `us`, `ms`, `s`, `min`, `h`, `d`, or `w`.

The authoritative defaults are demonstrated in
[`config/config.env.example`](../config/config.env.example). Secrets belong in
`secrets.env`, which must remain private.
