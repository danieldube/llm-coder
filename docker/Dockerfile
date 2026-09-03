# The base image supplies RunPod's SSH startup integration. Do not replace its
# ENTRYPOINT or CMD: runtime.py relies on SSH_PUBLIC_KEY being installed there.
FROM runpod/pytorch:1.1.0-cu1290-torch291-ubuntu2404

ARG VLLM_VERSION=0.28.0
ARG VLLM_CUDA_VERSION=129
ARG TARGETARCH

ENV VLLM_VENV=/opt/llm-coding/vllm \
    UV_CACHE_DIR=/opt/llm-coding/uv-cache

RUN case "${TARGETARCH:-amd64}" in \
        amd64) vllm_arch=x86_64 ;; \
        arm64) vllm_arch=aarch64 ;; \
        *) echo "Unsupported target architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && mkdir -p "${UV_CACHE_DIR}" \
    && uv venv "${VLLM_VENV}" --python python3 --seed \
    && uv pip install --python "${VLLM_VENV}/bin/python" \
        "https://github.com/vllm-project/vllm/releases/download/v${VLLM_VERSION}/vllm-${VLLM_VERSION}%2Bcu${VLLM_CUDA_VERSION}-cp38-abi3-manylinux_2_28_${vllm_arch}.whl" \
        --extra-index-url "https://download.pytorch.org/whl/cu${VLLM_CUDA_VERSION}" \
        --index-strategy unsafe-best-match \
    && "${VLLM_VENV}/bin/python" -c \
        "import torch, vllm; print(torch.__version__, vllm.__version__)" \
    && rm -rf "${UV_CACHE_DIR}"

COPY start-vllm.sh /opt/llm-coding/bin/start-vllm.sh
RUN chmod 0755 /opt/llm-coding/bin/start-vllm.sh
