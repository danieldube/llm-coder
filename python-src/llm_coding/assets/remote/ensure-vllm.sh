#!/usr/bin/env bash
set -euo pipefail

VLLM_VERSION="$1"
VLLM_CUDA_VERSION="$2"
MODEL_ID="$3"
MODEL_REVISION="$4"
SERVED_MODEL_NAME="$5"
CONTEXT_SIZE="$6"
GPU_MEMORY_UTILIZATION="$7"
TENSOR_PARALLEL_SIZE="$8"
KV_CACHE_DTYPE="$9"
ENFORCE_EAGER="${10}"
LANGUAGE_MODEL_ONLY="${11}"
MAX_NUM_SEQS="${12}"
REASONING_PARSER="${13}"
TOOL_CALL_PARSER="${14}"
PORT="${15}"
START_TIMEOUT="${16}"

RUNTIME_ROOT=/workspace/llm-coding
HF_HOME="${RUNTIME_ROOT}/huggingface"
LAUNCHER=/opt/llm-coding/bin/start-vllm.sh
PID_FILE="${RUNTIME_ROOT}/vllm.pid"
LOG_FILE="${RUNTIME_ROOT}/vllm.log"
SIGNATURE_FILE="${RUNTIME_ROOT}/runtime.signature"

fail() {
    printf 'LLM_CODING_REMOTE_FAILURE=%s\n' "$1" >&2
    exit 1
}

mkdir -p "${RUNTIME_ROOT}" "${HF_HOME}"
export HF_HOME

if [[ ! -x "${LAUNCHER}" ]]; then
    fail missing_launcher
fi

if ! /usr/local/bin/python - <<'PY'
import torch

if not torch.cuda.is_available():
    raise RuntimeError('No CUDA device is available')
PY
then
    fail no_cuda
fi

signature="$(printf '%s\n' \
    "${VLLM_VERSION}" \
    "${VLLM_CUDA_VERSION}" \
    "${MODEL_ID}" \
    "${MODEL_REVISION}" \
    "${SERVED_MODEL_NAME}" \
    "${CONTEXT_SIZE}" \
    "${GPU_MEMORY_UTILIZATION}" \
    "${TENSOR_PARALLEL_SIZE}" \
    "${KV_CACHE_DTYPE}" \
    "${ENFORCE_EAGER}" \
    "${LANGUAGE_MODEL_ONLY}" \
    "${MAX_NUM_SEQS}" \
    "${REASONING_PARSER}" \
    "${TOOL_CALL_PARSER}" \
    'log-requests' \
    | sha256sum | awk '{print $1}')"

is_healthy() {
    curl --fail --silent --max-time 3 "http://127.0.0.1:${PORT}/v1/models" \
        | grep -Fq "\"${SERVED_MODEL_NAME}\""
}

if is_healthy && [[ -f "${SIGNATURE_FILE}" ]] \
    && [[ "$(cat "${SIGNATURE_FILE}")" == "${signature}" ]]; then
    echo 'vLLM is already healthy with the requested configuration.'
    exit 0
fi

if [[ -f "${PID_FILE}" ]]; then
    old_pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [[ "${old_pid}" =~ ^[0-9]+$ ]] && kill -0 "${old_pid}" 2>/dev/null; then
        cmdline="$(tr '\0' ' ' < "/proc/${old_pid}/cmdline" 2>/dev/null || true)"
        if [[ "${cmdline}" == *vllm* ]]; then
            echo "Stopping previous vLLM process ${old_pid}..."
            kill "${old_pid}" || true
            for _ in $(seq 1 20); do
                kill -0 "${old_pid}" 2>/dev/null || break
                sleep 1
            done
            kill -9 "${old_pid}" 2>/dev/null || true
        fi
    fi
fi

rm -f "${SIGNATURE_FILE}"

echo "Starting prebuilt vLLM ${VLLM_VERSION} with ${MODEL_ID}@${MODEL_REVISION}..."
nohup "${LAUNCHER}" \
    "${MODEL_ID}" \
    "${MODEL_REVISION}" \
    "${SERVED_MODEL_NAME}" \
    "${CONTEXT_SIZE}" \
    "${GPU_MEMORY_UTILIZATION}" \
    "${TENSOR_PARALLEL_SIZE}" \
    "${KV_CACHE_DTYPE}" \
    "${ENFORCE_EAGER}" \
    "${LANGUAGE_MODEL_ONLY}" \
    "${MAX_NUM_SEQS}" \
    "${REASONING_PARSER}" \
    "${TOOL_CALL_PARSER}" \
    "${PORT}" \
    "${LOG_FILE}" \
    > /dev/null 2>&1 < /dev/null &

echo "$!" > "${PID_FILE}"

deadline=$((SECONDS + START_TIMEOUT))
while (( SECONDS < deadline )); do
    if is_healthy; then
        printf '%s\n' "${signature}" > "${SIGNATURE_FILE}"
        echo 'vLLM is ready.'
        exit 0
    fi

    pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
        fail vllm_exited
    fi

    sleep 5
done

fail vllm_timeout
