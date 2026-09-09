#!/usr/bin/env bash
set -euo pipefail

VLLM_PYTHON=/usr/local/bin/python

if [[ "$#" -ne 14 ]]; then
    echo "Usage: ${0##*/} MODEL_ID MODEL_REVISION SERVED_MODEL_NAME CONTEXT_SIZE GPU_MEMORY_UTILIZATION TENSOR_PARALLEL_SIZE KV_CACHE_DTYPE ENFORCE_EAGER LANGUAGE_MODEL_ONLY MAX_NUM_SEQS REASONING_PARSER TOOL_CALL_PARSER PORT LOG_FILE" >&2
    exit 2
fi

MODEL_ID="$1"
MODEL_REVISION="$2"
SERVED_MODEL_NAME="$3"
CONTEXT_SIZE="$4"
GPU_MEMORY_UTILIZATION="$5"
TENSOR_PARALLEL_SIZE="$6"
KV_CACHE_DTYPE="$7"
ENFORCE_EAGER="$8"
LANGUAGE_MODEL_ONLY="$9"
MAX_NUM_SEQS="${10}"
REASONING_PARSER="${11}"
TOOL_CALL_PARSER="${12}"
PORT="${13}"
LOG_FILE="${14}"

if ! "${VLLM_PYTHON}" - <<'PY'
import torch

if not torch.cuda.is_available():
    raise RuntimeError('No CUDA device is available')
PY
then
    echo 'The prebuilt vLLM runtime cannot access a CUDA device.' >&2
    exit 1
fi

args=(
    serve "${MODEL_ID}"
    --revision "${MODEL_REVISION}"
    --served-model-name "${SERVED_MODEL_NAME}"
    --host 127.0.0.1
    --port "${PORT}"
    --max-model-len "${CONTEXT_SIZE}"
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}"
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}"
    --kv-cache-dtype "${KV_CACHE_DTYPE}"
    --max-num-seqs "${MAX_NUM_SEQS}"
    --enable-prefix-caching
    --enable-log-requests
    --enable-auto-tool-choice
    --tool-call-parser "${TOOL_CALL_PARSER}"
)
if [[ "${ENFORCE_EAGER}" == true ]]; then
    args+=(--enforce-eager)
fi
if [[ "${LANGUAGE_MODEL_ONLY}" == true ]]; then
    args+=(--language-model-only)
fi
if [[ -n "${REASONING_PARSER}" ]]; then
    args+=(--reasoning-parser "${REASONING_PARSER}")
fi
exec "${VLLM_PYTHON}" -m vllm "${args[@]}" >"${LOG_FILE}" 2>&1
