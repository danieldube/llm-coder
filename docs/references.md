# External references

Links and documented interfaces were reviewed on 2026-09-07. Upstream pages
may describe newer releases than this project's pins. This review did not
rebuild the GPU image, rehash release binaries, or run live inference.

## Provider and image interfaces

- RunPod REST v1: [create](https://docs.runpod.io/api-reference/pods/POST/pods)
  and [list](https://docs.runpod.io/api-reference/pods/GET/pods) Pods. Creation
  documents `containerRegistryAuthId`, public TCP ports, and volume settings.
- [RunPod SSH](https://docs.runpod.io/pods/configuration/use-ssh): this project
  requires direct SSH over a public IP and forwarded TCP port.
- [Custom Pod templates](https://docs.runpod.io/pods/templates/create-custom-template):
  background for the base image and startup integration.
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry):
  `GITHUB_TOKEN` for publishing, a classic PAT with `read:packages` for private
  image pulls, and digest references for fixed image selection.

## Agent and IDE interfaces

- OpenCode [providers](https://opencode.ai/docs/providers),
  [permissions](https://opencode.ai/docs/permissions), and
  [configuration precedence](https://opencode.ai/docs/config/).
  `OPENCODE_CONFIG` participates in configuration merging; it does not exclude
  project configuration.
- [OpenCode ACP](https://opencode.ai/docs/acp): the `opencode acp` stdio server.
- [JetBrains ACP](https://www.jetbrains.com/help/ai-assistant/acp.html):
  `agent_servers` and shared `default_mcp_settings` in `~/.jetbrains/acp.json`.
  This project explicitly enables IDEA MCP, unlike JetBrains' documented
  default of `false`.
- [OpenCode v1.18.14](https://github.com/anomalyco/opencode/releases/tag/v1.18.14):
  configured release. Reviewed artifact names and SHA-256 values are maintained
  in `python-src/llm_coding/opencode.py`; link availability does not verify hashes.

## Inference pins

- [vLLM v0.28.0 release](https://github.com/vllm-project/vllm/releases/tag/v0.28.0)
  and [PyPI metadata](https://pypi.org/project/vllm/0.28.0/). The Dockerfile
  downloads the CUDA 12.9 wheel from the GitHub release into the compatible
  RunPod PyTorch environment.
- [vLLM tool calling](https://docs.vllm.ai/en/latest/features/tool_calling/):
  documents the `qwen3_xml` parser for Qwen3-Coder.
- [Qwen3-Coder-30B-A3B-Instruct-FP8](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8)
  and [pinned revision](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8/tree/e8ab3f2db9e388999a004eea5a31c16a8b517bc0)
  containing updated chat-template/tool-parser files.

## Socket proxy

- [systemd-socket-proxyd manual mirror](https://man7.org/linux/man-pages/man8/systemd-socket-proxyd.8.html):
  socket activation and `--exit-idle-time` refer to connection activity.
  Verified against the [upstream manual source](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd-socket-proxyd.xml);
  the freedesktop.org HTML endpoint returned HTTP 403.
