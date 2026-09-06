# llm-coding

Local coding-agent runtime backed by RunPod and vLLM. OpenCode runs on the
workstation; a user-systemd socket activates remote inference through SSH.

## Installation

Requires Linux, a user systemd session, Python 3.11+, OpenSSH, `curl`, `jq`,
and `systemd-socket-proxyd` with `--exit-idle-time` support (systemd 246+).
Python 3.13 is the primary development version.
Docker is needed only to build the remote runtime image.

From the checkout, on Ubuntu 24.04:

```bash
sudo apt-get install python3 python3-venv openssh-client curl jq systemd
python3 -m venv .venv
.venv/bin/python -m pip install -e .
source .venv/bin/activate
```

Prepare configuration before running `llm-install`. The current installer
cannot copy missing templates because its packaged-resource call is invalid.
These commands avoid that bootstrap defect and preserve existing files:

```bash
llm_config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/llm-coding"
install -d -m 0700 "$llm_config_dir"
for name in config secrets; do
  if [ ! -e "$llm_config_dir/$name.env" ]; then
    install -m 0600 "config/$name.env.example" "$llm_config_dir/$name.env"
  fi
done
```

Edit `config.env` and `secrets.env` in that directory:

- Set `RUNPOD_API_KEY` in `secrets.env`; do not export it to the agent.
- Replace `RUNPOD_IMAGE` with a published image reference. The example is a
  placeholder. See [runtime image releases](docs/architecture.md#runtime-image-releases).
- For a private image, set `RUNPOD_CONTAINER_REGISTRY_AUTH_ID` to a RunPod
  registry credential ID. Keep the registry token in RunPod.
- Review GPU, model, storage, and timeout settings in
  [configuration](docs/configuration.md). Keep the volume mounted at `/workspace`.

```bash
llm-install
llm-doctor
llm-up
# Optional: also verify chat completion and native tool calling.
llm-doctor --activate
```

Installation creates a dedicated SSH key if absent, installs the pinned
OpenCode executable, registers the JetBrains ACP agent, and enables the socket.
Activation creates or resumes a Pod. In JetBrains AI Assistant, select
`OpenCode RunPod`; for terminal use, run `opencode-runpod` in a trusted checkout.
Keep `.venv` at its installed path: generated units and ACP registration use
absolute executable paths. Read [SECURITY.md](SECURITY.md) before use.

## Commands

| Command | Purpose |
| --- | --- |
| `llm-install` | Install OpenCode, SSH key, ACP registration, and user-systemd units. |
| `llm-up` | Ensure the socket is installed/listening and activate the runtime. |
| `llm-down` | Stop the socket, proxy, tunnel, and selected Pod for this session. |
| `llm-status` | Inspect units, persisted Pod identity, provider state, and tunnel HTTP. |
| `llm-doctor` | Check local setup; `--activate` tests inference and tool calling. |
| `llm-runtime` | Internal lifecycle entry point used by systemd. |
| `opencode-runpod` | Launch OpenCode with generated configuration and background prewarm. |

`llm-up` requires existing configuration, SSH keys, and OpenCode installation;
it does not perform the complete `llm-install` workflow. OpenCode starts while
prewarm runs and receives failed prewarm diagnostics on standard error.

Idle shutdown stops the tunnel and Pod after there are no proxy connections
for `IDLE_SHUTDOWN`. It leaves the socket listening. Explicit `llm-down` also
stops the socket and proxy, but leaves socket enablement intact. Both retain
configuration, Pod identity, and persistent storage. `llm-up` can reactivate
an explicitly stopped installation.

`llm-down --remove-integration` prompts before shutdown and additionally
removes generated OpenCode configuration and the configured ACP agent entry.
It preserves credentials, units, other agents, and storage. It is not a full
uninstall; see [removal](docs/configuration.md#removal).

## Diagnostics

```bash
llm-status
journalctl --user -u llm-coding-proxy.service -u llm-coding-tunnel.service \
  --lines 100 --no-pager
```

`llm-status` probes the direct tunnel, so it does not wake the socket. Exit
codes are `0` healthy, `1` inactive, `2` degraded, and `3` inspection failed;
configuration errors also return `1`. A listening socket with a stopped Pod
is reported as degraded, including after normal idle shutdown.

`llm-doctor` checks local dependencies, configuration, key files, OpenCode
version, socket enablement/activity, and ACP registration. Without `--activate`
it does not authenticate against RunPod or test inference.

For GPU capacity failures, retry later. Changing the GPU or image setting does
not modify an existing selected Pod. See [configuration changes](docs/configuration.md#applying-changes).
Remote startup logs are at `/workspace/llm-coding/vllm.log`; request logging
is enabled, so review logs for sensitive content before sharing.

## Development and design

- [Architecture](docs/architecture.md): data flow, lifecycle, state, and modules.
- [Configuration](docs/configuration.md): schema, paths, changes, and removal.
- [Contributing](CONTRIBUTING.md): environment setup and required validation.
- [Agent instructions](AGENTS.md): implementation constraints and source map.
- [External references](docs/references.md): upstream interfaces and release pins.

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s python-src/tests
```
