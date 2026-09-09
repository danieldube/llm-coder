# The base image supplies RunPod's SSH startup integration. Do not replace its
# ENTRYPOINT or CMD: runtime.py relies on SSH_PUBLIC_KEY being installed there.
# Its PyTorch 2.13.0 build matches the vLLM 0.28.0 wheel requirement, so vLLM
# can use the base environment without downloading a second PyTorch runtime.
FROM runpod/pytorch:1.2.0-rc.162-cu1290-torch2130-ubuntu2404@sha256:8fb86016fe9b1a16dbe767318ccda9348b2fd161ba538dcc753180e831b93585

ARG VLLM_VERSION=0.28.0
ARG VLLM_CUDA_VERSION=129
ARG TARGETARCH

ENV VLLM_PYTHON=/usr/local/bin/python

# Install only vLLM and its missing dependencies into the compatible base
# environment. The explicit Torch check prevents a resolver change from
# silently reintroducing a second CUDA/PyTorch installation.
RUN case "${TARGETARCH:-amd64}" in \
        amd64) vllm_arch=x86_64 ;; \
        arm64) vllm_arch=aarch64 ;; \
        *) echo "Unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && "${VLLM_PYTHON}" -c \
        "import torch; assert torch.__version__.split('+', 1)[0] == '2.13.0'" \
    && uv pip install --system --break-system-packages \
        "https://github.com/vllm-project/vllm/releases/download/v${VLLM_VERSION}/vllm-${VLLM_VERSION}%2Bcu${VLLM_CUDA_VERSION}-cp38-abi3-manylinux_2_28_${vllm_arch}.whl" \
        --extra-index-url "https://download.pytorch.org/whl/cu${VLLM_CUDA_VERSION}" \
        --index-strategy unsafe-best-match \
    && "${VLLM_PYTHON}" -c \
        "import torch, vllm; assert torch.__version__.split('+', 1)[0] == '2.13.0'; assert vllm.__version__ == '${VLLM_VERSION}'"

COPY start-vllm.sh /opt/llm-coding/bin/start-vllm.sh
RUN chmod 0755 /opt/llm-coding/bin/start-vllm.sh
