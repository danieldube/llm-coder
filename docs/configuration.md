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
creates directories before validation, but its current template copy call is
invalid; use the bootstrap commands in the README when either configuration
file is missing. Other runtime operations load settings before their lifecycle
side effects.

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
`RUNPOD_POD_NAME`, `RUNPOD_IMAGE_REPOSITORY`, `MODEL`, and
`OPENCODE_VERSION`. API key placeholders `REPLACE_ME`, `CHANGEME`,
`YOUR_API_KEY`, and `<API_KEY>` are rejected case-insensitively. The documented
`RUNPOD_IMAGE_REPOSITORY` placeholder and the `runpod/pytorch` base-image
family are rejected before a Pod is created. Other image references are not
checked against a registry or inspected for the required launcher locally.

`MODEL` selects a reviewed internal model contract. It owns the Hugging Face
ID, served name, context/output limits, vLLM/CUDA versions, all vLLM launch
flags, GPU type/count, and tensor parallelism. Direct settings for those values
are rejected to prevent a model from starting on incompatible hardware.
The available keys are `qwen3-coder-30b-a3b-fp8`,
`qwen3-coder-next-fp8`, and `qwen3.8-nvfp4`. The latter requests one NVIDIA
GeForce RTX 5090, uses a 32K context, and starts vLLM with FP8 KV cache,
eager mode, language-model-only mode, and the `qwen3` reasoning parser.

`RUNPOD_IMAGE_REPOSITORY` supplies only the registry/repository prefix. The
controller appends `:latest`, so a Pod uses the most recently published runtime
image. This tag is mutable: a later image publication can replace it without a
configuration change. The publishing workflow updates it whenever Docker inputs
or the model catalog reach `main`.

| Provider setting | Default | Contract |
| --- | --- | --- |
| `RUNPOD_CLOUD_TYPE` | `SECURE` | `SECURE` or `COMMUNITY` |
| `RUNPOD_IMAGE_REPOSITORY` | Required | Runtime-image repository; the model selects its tag |
| `RUNPOD_CONTAINER_REGISTRY_AUTH_ID` | Empty | RunPod registry credential ID, never a token |
| `RUNPOD_CONTAINER_DISK_GB` | `40` | 1–2048 GiB |
| `RUNPOD_VOLUME_GB` | `100` | 1–65536 GiB; used without a network volume |
| `RUNPOD_VOLUME_MOUNT_PATH` | `/workspace` | Keep this value; remote cache paths are fixed |
| `RUNPOD_MIN_RAM_PER_GPU` | `48` | 1–4096 GiB |
| `RUNPOD_MIN_VCPU_PER_GPU` | `8` | 1–1024 |
| `RUNPOD_NETWORK_VOLUME_ID` | Empty | Existing network volume; replaces `volumeInGb` in create request |

The create request uses the selected model's GPU count/type,
`interruptible=false`, public IP support, and `22/tcp` only.
The selected image must include this project's vLLM launcher and the RunPod
SSH startup integration.

| Inference setting | Contract |
| --- | --- |
| `MODEL` | Required reviewed model key; resolves model and hardware together |
| `REMOTE_VLLM_PORT` | `8000` | 1–65535; remote loopback listener |
| `LOCAL_PROXY_PORT` | `18000` | 1–65535; stable host endpoint |
| `LOCAL_TUNNEL_PORT` | `18001` | 1–65535; must differ from proxy port |

The selected model profile pins the vLLM/CUDA versions and model revision that
its runtime image contains. The launcher always passes that revision. GPU memory
and model support are runtime constraints beyond these numeric ranges.

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

The controller persists a fingerprint of the model and Pod definition. On
`llm-up`, a selected model whose fingerprint differs from the current Pod stops
that Pod and creates a replacement. It never deletes the old Pod or storage.
The transition clears only that Pod's saved SSH endpoint before enrolling the
replacement through the normal fail-closed host-key flow.

Changing `JETBRAINS_AGENT_NAME` creates a new entry without removing the old
name. Remove the old integration before renaming if it should not remain.
ACP registration updates global MCP defaults as well as the named agent;
removal does not restore earlier MCP defaults.

## Removal

`llm-down --remove-integration` removes only generated OpenCode configuration
and the currently configured ACP agent entry after shutdown. It leaves socket
enablement and installed units intact; a later activation can register again.

For complete local removal from a checkout, run `./uninstall.sh`. It stops the
proxy, asks the installed `llm-runtime` to remove the configured ACP entry,
disables the socket, removes generated unit files and every console entry
point, clears the installation directory, and reloads the user manager. It
does not source configuration files.

The script deliberately preserves configuration, state, SSH keys, OpenCode,
and RunPod storage. Remove the Python package separately if it was installed
into a virtual environment:

```bash
./uninstall.sh
.venv/bin/python -m pip uninstall llm-coding
```

Keep the package installed until shutdown has finished, because unit stop hooks
invoke `llm-runtime`. Configuration, state, SSH keys, the OpenCode binary, and
remote storage require separate removal decisions. Verify Pod state in RunPod
if shutdown reports errors.
