# llm-coding

Reproducible host-native OpenCode + CLion ACP + self-hosted RunPod/vLLM setup.
No Docker, Terraform, Kubernetes, reverse proxy, or permanent custom daemon is
required on the workstation.

## What it does

```text
CLion -> OpenCode -> localhost:18000 -> systemd socket activation
      -> SSH tunnel -> RunPod/vLLM -> Qwen3-Coder
```

The RunPod GPU is created/resumed lazily when the LLM endpoint is first used.
After the configured idle time, the local proxy exits and the Pod is stopped.
The persistent `/workspace` volume keeps the vLLM environment and Hugging Face
model cache.

See [docs/architecture.md](docs/architecture.md) for the design.

## Default pinned stack

- OpenCode `1.18.14`
- vLLM `0.28.0`
- vLLM CUDA variant `cu129`
- RunPod image `runpod/pytorch:1.1.0-cu1290-torch291-ubuntu2404`
- `Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8`
- model revision `e8ab3f2db9e388999a004eea5a31c16a8b517bc0`
- Qwen3-Coder vLLM tool parser `qwen3_xml`
- L40S 48 GB default GPU
- 65,536-token configured context

All values can be changed in `~/.config/llm-coding/config.env`.

## CUDA compatibility

The RunPod CUDA runtime and installed vLLM binary must be compatible. The
default pinned unit is:

| Component | Version |
| --- | --- |
| RunPod image CUDA | 12.9.0 |
| vLLM CUDA variant | cu129 |
| `VLLM_CUDA_VERSION` | `129` |

The vLLM wheel variant is selected explicitly with:

```bash
VLLM_CUDA_VERSION=129
```

Its installation uses the matching PyTorch CUDA index and resolves shared
Python dependencies against the compatible versions published across that
index and PyPI.

Its persistent environment includes that variant, for example
`/workspace/llm-coding/vllm-0.28.0-cu129`. Changing it creates an isolated
environment. Do not rely on automatic CUDA selection or fix a mismatch by
manually copying another `libcudart` into the image.

Changing `RUNPOD_IMAGE` affects newly created Pods only. An existing Pod is
reused by name, so update its image through the RunPod API (while it is
stopped) or replace the Pod before changing the CUDA variant. Use a RunPod
network volume when retaining the model cache across Pod replacement is
important.

## Prerequisites

Ubuntu with a user systemd session, CLion/JetBrains AI Assistant with ACP
support, and a RunPod account/API key.

Install the small host dependency set:

```bash
sudo apt update
sudo apt install -y curl jq openssh-client util-linux
```

`systemd-socket-proxyd` is provided by systemd on supported Ubuntu releases.

## Install

```bash
./install.sh
```

The installer:

1. installs a snapshot under `~/.local/share/llm-coding/`;
2. creates `~/.config/llm-coding/config.env` and `secrets.env` if absent;
3. generates a dedicated RunPod SSH key if needed;
4. installs the pinned OpenCode version through the official installer;
5. installs/enables the systemd user socket;
6. merges an `OpenCode RunPod` ACP entry into `~/.jetbrains/acp.json` while
   backing up an existing file;
7. creates convenience commands in `~/.local/bin/`.

Then set your API key:

```bash
$EDITOR ~/.config/llm-coding/secrets.env
```

```bash
RUNPOD_API_KEY=your-key-here
```

Review the non-secret configuration:

```bash
$EDITOR ~/.config/llm-coding/config.env
```

## Validate

Static/local validation without starting a GPU:

```bash
llm-doctor
```

Full first-time initialization and end-to-end test:

```bash
llm-doctor --activate
```

The first activation is intentionally expensive: it creates the Pod if needed,
creates the persistent vLLM environment, and downloads the model into
`/workspace/llm-coding/huggingface`.

## CLion

The installer registers this agent in `~/.jetbrains/acp.json`:

```text
OpenCode RunPod
```

In CLion open AI Chat and select **OpenCode RunPod**. JetBrains launches:

```bash
~/.local/share/llm-coding/bin/opencode-runpod acp
```

OpenCode starts immediately. The wrapper prewarms RunPod asynchronously; if the
first model request arrives earlier, the socket connection waits while systemd
finishes provisioning.

## OpenCode provider and ACP

Use the repository-owned wrapper, not a separate hard-coded OpenCode provider
configuration:

```bash
opencode-runpod
```

The wrapper renders a non-secret OpenCode config in
`~/.local/state/llm-coding/opencode.json` and sets `OPENCODE_CONFIG` only for
the OpenCode process it starts. It configures `runpod/qwen3-coder` against the
stable local endpoint `http://127.0.0.1:18000/v1`.

Port `18000` is intentional: it is the socket-activated endpoint OpenCode
uses. Port `18001` is the direct SSH-tunnel endpoint for diagnostics; CLion
does not connect to either port itself.

Verify the OpenCode provider without modifying a project:

```bash
opencode-runpod models runpod
opencode-runpod run 'Inspect this repository and describe its top-level structure. Do not modify files.'
```

The default vLLM endpoint has no HTTP authentication, so OpenCode does not
need a provider credential for this local provider. If authentication is added
to vLLM later, configure the `runpod` provider credential through OpenCode.

The installer registers `OpenCode RunPod` in `~/.jetbrains/acp.json`, pointing
CLion at `~/.local/share/llm-coding/bin/opencode-runpod acp`. ACP uses the
subprocess standard input/output protocol; it is not an HTTP connection from
CLion to the SSH tunnel. Restart CLion after changing its ACP configuration.

## Manual commands

```bash
llm-up              # force activation and show /v1/models
llm-status          # status without accidentally activating the GPU
llm-down            # stop proxy/tunnel/Pod now
llm-doctor           # local configuration checks
llm-doctor --activate # end-to-end completion and tool-call checks
```

`llm-status` deliberately probes port 18001, not port 18000. Port 18000 is
socket-activated and merely checking it would start the GPU.

## Logs

Host proxy/lifecycle:

```bash
journalctl --user -u llm-coding-proxy.service -f
```

SSH tunnel:

```bash
journalctl --user -u llm-coding-tunnel.service -f
```

OpenCode version:

```bash
~/.opencode/bin/opencode --version
```

## Configuration notes

### Pod persistence

By default the project creates a Pod with `RUNPOD_VOLUME_GB` mounted at
`/workspace`. The Pod is stopped, not deleted, on idle shutdown.

For stronger persistence across Pod replacement, create a RunPod network volume
and set:

```bash
RUNPOD_NETWORK_VOLUME_ID=<volume-id>
```

The controller then attaches that volume instead of creating a Pod-local volume.

### Changing the local proxy port

`LOCAL_PROXY_PORT` is embedded in the systemd socket unit at installation time.
After changing it, rerun:

```bash
./install.sh
```

Other runtime/model values are read dynamically.

### Changing model/runtime settings

`remote/ensure-vllm.sh` records a configuration signature in `/workspace`.
Changing the model revision, vLLM version or CUDA variant, context, GPU
utilization, or tool parser causes vLLM to be restarted with the new
configuration on next use.

### GPU availability

The default is:

```bash
RUNPOD_GPU_TYPE="NVIDIA L40S"
RUNPOD_CLOUD_TYPE=SECURE
```

If RunPod cannot rent that GPU, edit `config.env` to a currently available GPU
with sufficient VRAM. A model change may also require a different GPU.

## Security

OpenCode is intentionally host-native so it can use your existing repository,
compiler, CMake, Git, tests, linters, and other developer tools directly.
Consequently, it executes with your Unix user's authority; OpenCode permissions
are not an OS sandbox.

The supplied policy:

- denies external-directory access;
- denies reads of `.env`/`.env.*` (except `.env.example`);
- asks before arbitrary shell commands;
- allows common read-only Git/search commands;
- denies `git push`, `sudo`, and `ssh` through OpenCode.

Build/test commands remain `ask` by default because project-controlled build
files can execute arbitrary code.

The RunPod API key is stored separately in
`~/.config/llm-coding/secrets.env` and is not exported to OpenCode. The dedicated
SSH key path is also not injected into OpenCode. Because OpenCode is host-native,
it still has the Unix user's underlying filesystem authority if a shell command
is approved; these are exposure reductions, not an OS security boundary. vLLM
listens on `127.0.0.1` inside the Pod and is reachable only through SSH.

Custom JetBrains MCP forwarding is disabled by default; the integrated IDE MCP
is enabled. Project-local OpenCode configuration/instructions are another reason
to use the host-native agent only with repositories you trust.

Do not rely on this setup as a sandbox for untrusted repositories.

## RunPod lifecycle behavior

The controller identifies the Pod by the configured unique name. It fails if
multiple Pods with that name exist rather than making an ambiguous choice.

- missing: create;
- `EXITED`: start/resume;
- `RUNNING`: reuse;
- `TERMINATED`: fail with an actionable error.

SSH IP/port mappings are rediscovered on every activation because RunPod may
change them after resume.

## Development

Run static checks:

```bash
make check
```

If `shellcheck` is installed, it is included automatically.

## Uninstall

```bash
./uninstall.sh
```

The uninstall intentionally preserves secrets/config, runtime state, the
RunPod pod/volume, the dedicated SSH key, and OpenCode itself. This avoids data
loss. Remove them manually if required.
