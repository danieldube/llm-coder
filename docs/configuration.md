# Configuration

## Loading and syntax

`config.py` reads `config.env`, then `secrets.env`, then supported variables
from the current process environment. Later values win, including empty ones.
Unknown settings are ignored.

Files use `NAME=VALUE`, with uppercase names. Blank lines and full-line `#`
comments are ignored; matching outer quotes are stripped. Files are not shell
scripts: `export`, command substitution, interpolation between settings, and
inline comments are not supported. Only path settings expand environment
variables and `~`; other values remain literal. Boolean and cloud-type values
are case-insensitive.

Validation aggregates missing values, invalid types, and range errors without
printing secret values. It does not check image availability, CUDA
compatibility, file permissions, or credentials against RunPod. `llm-install`
creates directories and attempts missing template copies before validation;
other runtime operations load settings before their lifecycle side effects.

Use configuration files for durable settings. Generated units do not embed
shell overrides or forward XDG variables: services read their user-systemd
manager environment. A shell-only override can therefore disagree with the
runtime. Keep XDG paths consistent between the shell and user services.

## Paths

| Location | Content |
| --- | --- |
| `${XDG_CONFIG_HOME:-~/.config}/llm-coding/` | `config.env`, `secrets.env` |
| `${XDG_STATE_HOME:-~/.local/state}/llm-coding/` | Runtime state and generated `opencode.json` |
| `${XDG_DATA_HOME:-~/.local/share}/llm-coding/` | Reserved installation directory; executable assets come from the Python package |
| `${XDG_CONFIG_HOME:-~/.config}/systemd/user/` | Three generated units |
| `~/.opencode/bin/opencode` | Downloaded OpenCode binary; not XDG-relative |
| `~/.jetbrains/acp.json` | Shared ACP registration; not XDG-relative |
| `/workspace/llm-coding/` on the Pod | Model cache, logs, PID, runtime signature |

Keep both environment files mode 0600 and application directories private.
The loader does not enforce permissions on existing files. See the
[setup procedure](../README.md#installation) for initial template creation.

## Settings

Required nonempty strings: `RUNPOD_API_KEY`, `RUNPOD_SSH_KEY`,
`RUNPOD_POD_NAME`, `RUNPOD_IMAGE`, `RUNPOD_GPU_TYPE`, `OPENCODE_VERSION`,
`VLLM_VERSION`, `VLLM_CUDA_VERSION`, `MODEL_ID`, `SERVED_MODEL_NAME`, and
`MODEL_DISPLAY_NAME`. API key placeholders `REPLACE_ME`, `CHANGEME`,
`YOUR_API_KEY`, and `<API_KEY>` are rejected case-insensitively. Other
placeholder strings, including the example image, are not rejected.

The [example](../config/config.env.example) supplies release/model pins;
required settings have no usable fallback when omitted from configuration.
The tables below list parser defaults for optional settings.

| Provider setting | Default | Contract |
| --- | --- | --- |
| `RUNPOD_CLOUD_TYPE` | `SECURE` | `SECURE` or `COMMUNITY` |
| `RUNPOD_CONTAINER_REGISTRY_AUTH_ID` | Empty | RunPod registry credential ID, never a token |
| `RUNPOD_CONTAINER_DISK_GB` | `40` | 1–2048 GiB |
| `RUNPOD_VOLUME_GB` | `100` | 1–65536 GiB; used without a network volume |
| `RUNPOD_VOLUME_MOUNT_PATH` | `/workspace` | Keep this value; remote cache paths are fixed |
| `RUNPOD_MIN_RAM_PER_GPU` | `48` | 1–4096 GiB |
| `RUNPOD_MIN_VCPU_PER_GPU` | `8` | 1–1024 |
| `RUNPOD_NETWORK_VOLUME_ID` | Empty | Existing network volume; replaces `volumeInGb` in create request |

The create request always uses one GPU, `interruptible=false`, public IP
support, and `22/tcp`. `RUNPOD_GPU_TYPE` is sent as a RunPod GPU type ID.
The selected image must include this project's vLLM launcher and the RunPod
SSH startup integration.

| Inference setting | Default | Contract |
| --- | --- | --- |
| `MODEL_REVISION` | Empty | Example pins a model commit; pin a valid revision for reproducibility |
| `CONTEXT_SIZE` | `65536` | 1–10,000,000 tokens; vLLM context and OpenCode context limit |
| `MAX_OUTPUT_TOKENS` | `16384` | 1–10,000,000, at most context; OpenCode model metadata only |
| `VLLM_GPU_MEMORY_UTILIZATION` | `0.92` | Greater than 0, at most 1 |
| `VLLM_TOOL_CALL_PARSER` | `qwen3_xml` | Passed directly to vLLM; parser availability is not validated |
| `REMOTE_VLLM_PORT` | `8000` | 1–65535; remote loopback listener |
| `LOCAL_PROXY_PORT` | `18000` | 1–65535; stable host endpoint |
| `LOCAL_TUNNEL_PORT` | `18001` | 1–65535; must differ from proxy port |

`VLLM_VERSION` and `VLLM_CUDA_VERSION` describe the prebuilt image. Changing
them does not install or verify a different vLLM/CUDA build. The launcher
always passes `--revision`, including when `MODEL_REVISION` is empty; empty
revision behavior is not validated locally. GPU memory and model support are
runtime constraints beyond these numeric ranges.

| Lifecycle/integration setting | Default | Contract |
| --- | --- | --- |
| `IDLE_SHUTDOWN` | `30min` | Positive integer followed by `us`, `ms`, `s`, `min`, `h`, `d`, or `w` |
| `RUNPOD_START_TIMEOUT_SECONDS` | `1200` | 1–86400; endpoint discovery and SSH readiness |
| `VLLM_START_TIMEOUT_SECONDS` | `1800` | 1–86400; remote health polling |
| `LIFECYCLE_LOCK_TIMEOUT_SECONDS` | `30` | 1–300; full installation always uses 30 seconds |
| `JETBRAINS_AGENT_NAME` | `OpenCode RunPod` | ACP entry name |
| `ENABLE_IDEA_MCP` | `true` | Shared JetBrains `use_idea_mcp` default |
| `ENABLE_CUSTOM_MCP` | `false` | Shared JetBrains `use_custom_mcp` default |

`OPENCODE_VERSION` must be a reviewed release in `opencode.py` for a new
download. An existing binary reporting that version is reused without hashing.
The generated provider is `runpod`, uses `@ai-sdk/openai-compatible`, and serves
`SERVED_MODEL_NAME` at `http://127.0.0.1:LOCAL_PROXY_PORT/v1`.

## Applying changes

Stop the runtime with `llm-down` before editing configuration. Then run
`llm-install` to update units, OpenCode, and ACP, followed by `llm-up`.
Unit files are rewritten but already-active listeners are not restarted by
installation alone. OpenCode configuration is regenerated on launch and
successful activation. Existing configuration files are never migrated or
replaced automatically with new examples.

Image, GPU, storage, cloud, and SSH public-key settings affect newly created
Pods only. Existing Pods are resumed without patching their definition;
renaming `RUNPOD_POD_NAME` does not override a live persisted Pod ID.
There is no Pod replacement command. If replacement is necessary, stop the
installation, confirm the old Pod's disposition and storage retention in
RunPod, and reconcile both Pod identity and SSH endpoint state deliberately.
A stale `runtime.ssh-endpoint.json` from another Pod prevents enrollment even
if `runtime.pod-id` was cleared automatically after a 404. Do not bypass
host-key verification or clear trust state simply to silence an error.

Changing `JETBRAINS_AGENT_NAME` creates a new entry without removing the old
name. Remove the old integration before renaming if it should not remain.
ACP registration updates global MCP defaults as well as the named agent;
removal does not restore earlier MCP defaults.

## Removal

`llm-down --remove-integration` removes only generated OpenCode configuration
and the currently configured ACP agent entry after shutdown. It leaves socket
enablement and installed units intact; a later activation can register again.

For complete local removal, first complete that command successfully, then
disable the socket, remove the three generated unit files listed in
[systemd documentation](../systemd/README.md), and reload the user manager:

```bash
systemctl --user disable --now llm-coding.socket
llm_units_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
rm -f "$llm_units_dir/llm-coding.socket" \
  "$llm_units_dir/llm-coding-proxy.service" \
  "$llm_units_dir/llm-coding-tunnel.service"
systemctl --user daemon-reload
.venv/bin/python -m pip uninstall llm-coding
```

Keep the package installed until shutdown has finished, because unit stop hooks
invoke `llm-runtime`. Configuration, state, SSH keys, the OpenCode binary, and
remote storage require separate removal decisions. Verify Pod state in RunPod
if shutdown reports errors. `uninstall.sh` is a legacy shell-installation
cleanup script and is not a complete uninstaller for the Python package.
