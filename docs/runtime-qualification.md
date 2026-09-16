# Runtime qualification

CUDA 12.9 is the preferred candidate because it uses the official
`vllm/vllm-openai:v0.28.0-cu129-ubuntu2404` runtime. It is not selected by a
production model profile until the matching GPU and model path has passed this
procedure.

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

No real RunPod GPU qualification has been performed from this checkout.
Accordingly, every production profile remains on CUDA 12.8, including
`qwen3.8-nvfp4`. No CUDA 12.9 image digest, host driver, or inference result
is recorded yet.

The existing `cuda128` alias was restored from `latest` as an emergency
availability measure. It is not evidence of the CUDA 12.8/vLLM 0.28.0 fallback
contract. Keep the rollback workflow until a wheel-built CUDA 12.8 candidate
has been published, statically validated, and GPU-qualified where required.

## Follow-up

`ModelSpec` currently names a validated operational alias (`cuda128` or
`cuda129`), so the controller can use the last promoted image. Moving each
model contract to an immutable GHCR digest would require configuration and Pod
identity changes beyond this release migration. Make that change only after a
qualified candidate digest is available; do not point a profile at a candidate
that has not been promoted.
