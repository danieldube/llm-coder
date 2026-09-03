#!/usr/bin/env bash
set -euo pipefail

VLLM_VENV=/opt/llm-coding/vllm

if [[ "$#" -ne 8 ]]; then
    echo "Usage: ${0##*/} MODEL_ID MODEL_REVISION SERVED_MODEL_NAME CONTEXT_SIZE GPU_MEMORY_UTILIZATION TOOL_CALL_PARSER PORT LOG_FILE" >&2
    exit 2
fi

MODEL_ID="$1"
MODEL_REVISION="$2"
SERVED_MODEL_NAME="$3"
CONTEXT_SIZE="$4"
GPU_MEMORY_UTILIZATION="$5"
TOOL_CALL_PARSER="$6"
PORT="$7"
LOG_FILE="$8"

if ! "${VLLM_VENV}/bin/python" - <<'PY'
import torch

if not torch.cuda.is_available():
    raise RuntimeError('No CUDA device is available')
PY
then
    echo 'The prebuilt vLLM runtime cannot access a CUDA device.' >&2
    exit 1
fi

export PATH="${VLLM_VENV}/bin:${PATH}"
exec "${VLLM_VENV}/bin/vllm" serve "${MODEL_ID}" \
    --revision "${MODEL_REVISION}" \
    --served-model-name "${SERVED_MODEL_NAME}" \
    --host 127.0.0.1 \
    --port "${PORT}" \
    --max-model-len "${CONTEXT_SIZE}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --enable-prefix-caching \
    --enable-log-requests \
    --enable-auto-tool-choice \
    --tool-call-parser "${TOOL_CALL_PARSER}" \
    >"${LOG_FILE}" 2>&1
