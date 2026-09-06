# systemd units

`python-src/llm_coding/systemd.py` is the sole authoritative unit source.
`llm-install` renders paths and configured ports/timeouts at installation time
and writes the resulting user units under
`${XDG_CONFIG_HOME:-~/.config}/systemd/user/`. There are deliberately no static
unit copies in this directory to drift from the renderer.

- `llm-coding.socket` listens on the stable local OpenAI-compatible endpoint.
- `llm-coding-proxy.service` is socket-activated, ensures RunPod/vLLM are ready,
  then executes `systemd-socket-proxyd`.
- `llm-coding-tunnel.service` owns the SSH local-forward from the host to vLLM.

Do not enable the proxy or tunnel directly. Only `llm-coding.socket` is enabled.
The proxy invokes the packaged `llm-runtime up` and `llm-runtime down`
subcommands. Its stop hook performs transient shutdown: the tunnel and selected
Pod stop, while the persisted Pod ID and durable IDE integration remain.

`llm-down` stops the socket without disabling it. Idle exit leaves the socket
listening. `llm-down --remove-integration` also leaves unit files and socket
enablement intact; see [complete removal](../docs/configuration.md#removal).

Generated units contain absolute paths to the installed `llm-runtime`. Keep
that Python environment available until services are stopped. After changing
ports or executable paths, stop the runtime and rerun `llm-install`; writing
unit files and reloading systemd does not replace an already-active listener.
Services inherit the user manager environment, not shell-only configuration
or XDG overrides.

The proxy uses `Type=notify`, a startup timeout equal to both configured startup
budgets plus 120 seconds, and a three-minute stop timeout. Its `ExecStopPost`
also runs after startup failure. The tunnel uses `Restart=on-failure` and
`RestartSec=5`. `IDLE_SHUTDOWN` counts time without active connections.
