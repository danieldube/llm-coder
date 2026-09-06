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
