# systemd units

`llm-install` installs these as user units under `~/.config/systemd/user/`.

- `llm-coding.socket` listens on the stable local OpenAI-compatible endpoint.
- `llm-coding-proxy.service` is socket-activated, ensures RunPod/vLLM are ready,
  then executes `systemd-socket-proxyd`.
- `llm-coding-tunnel.service` owns the SSH local-forward from the host to vLLM.

Do not enable the proxy or tunnel directly. Only `llm-coding.socket` is enabled.
