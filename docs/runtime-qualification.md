# Runtime qualification

All profiles reference the published CUDA 12.9 image digest. Its software and
SSH startup have passed CI checks, but RunPod GPU inference has not been
qualified. Do not treat those checks as proof of model compatibility.

## Required RunPod qualification

Use the normal controller path: create or resume the Pod, connect through SSH,
and let the controller invoke `start-vllm`. Do not start an alternative vLLM
command for the test.

For an RTX 5090, test `Inferact/Qwen3.8-27B-NVFP4` with the existing
`qwen3.8-nvfp4` profile. Record the candidate image digest, `nvidia-smi`
output, driver version, GPU name, compute capability, PyTorch version, CUDA
version, and vLLM version. Before controller startup, confirm that no vLLM
process exists. Then confirm:

1. SSH public-key authentication works and the container remains alive.
2. `validate-runtime` reports the expected software stack.
3. vLLM starts only after the controller request and listens on `127.0.0.1`.
4. The tunneled health endpoint succeeds.
5. The target model loads, non-streaming inference succeeds, streaming
   inference succeeds, and a representative prompt succeeds.
6. Logs contain no PTX, unsupported architecture, Triton, CUDA driver/runtime,
   illegal-instruction, missing-cubin/kernel-image, or NVFP4 kernel error.
7. vLLM stops cleanly and the Pod can be restarted and used again.

Qualify L40S with `qwen3-coder-30b-a3b-fp8` and H200 with
`qwen3-coder-next-fp8` separately. A result on one GPU/model path does not
qualify another.

## Recorded results

No real RunPod GPU qualification has been performed from this checkout. The
published image digest is
`sha256:e5899e0f548aaf2a5c9bab129fe4ded0855c39517214fd99e6e3ff839ab378db`.
No host driver, model-loading, streaming, tool-calling, or restart result is
recorded. Qualify each profile before relying on it for production work.
