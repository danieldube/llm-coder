#!/usr/bin/env bash
set -euo pipefail

VLLM_VERSION="$1"
VLLM_CUDA_VERSION="$2"
MODEL_ID="$3"
MODEL_REVISION="$4"
SERVED_MODEL_NAME="$5"
CONTEXT_SIZE="$6"
GPU_MEMORY_UTILIZATION="$7"
TOOL_CALL_PARSER="$8"
PORT="$9"
START_TIMEOUT="${10}"

RUNTIME_ROOT=/workspace/llm-coding
LOCAL_RUNTIME_ROOT=/opt/llm-coding
VLLM_VENV="${LOCAL_RUNTIME_ROOT}/vllm-${VLLM_VERSION}-cu${VLLM_CUDA_VERSION}"
HF_HOME="${RUNTIME_ROOT}/huggingface"
UV_CACHE_DIR="${LOCAL_RUNTIME_ROOT}/uv-cache"
PID_FILE="${RUNTIME_ROOT}/vllm.pid"
LOG_FILE="${RUNTIME_ROOT}/vllm.log"
SIGNATURE_FILE="${RUNTIME_ROOT}/runtime.signature"
VALIDATION_TIMEOUT_SECONDS=300

mkdir -p "${RUNTIME_ROOT}" "${LOCAL_RUNTIME_ROOT}" "${HF_HOME}" "${UV_CACHE_DIR}"
export HF_HOME UV_CACHE_DIR

command -v curl >/dev/null 2>&1 || { echo "curl is missing in the RunPod image" >&2; exit 1; }
command -v sha256sum >/dev/null 2>&1 || { echo "sha256sum is missing in the RunPod image" >&2; exit 1; }
command -v timeout >/dev/null 2>&1 || { echo "timeout is missing in the RunPod image" >&2; exit 1; }

if command -v uv >/dev/null 2>&1; then
    UV="$(command -v uv)"
else
    echo "The selected RunPod image does not contain uv." >&2
    echo "Use a RunPod PyTorch image that includes uv or adjust remote/ensure-vllm.sh." >&2
    exit 1
fi

signature="$(printf '%s\n' \
    "${VLLM_VERSION}" \
    "${VLLM_CUDA_VERSION}" \
    "${MODEL_ID}" \
    "${MODEL_REVISION}" \
    "${SERVED_MODEL_NAME}" \
    "${CONTEXT_SIZE}" \
    "${GPU_MEMORY_UTILIZATION}" \
    "${TOOL_CALL_PARSER}" \
    "log-requests" \
    | sha256sum | awk '{print $1}')"

is_healthy() {
    curl --fail --silent --max-time 3 "http://127.0.0.1:${PORT}/v1/models" \
        | grep -Fq "\"${SERVED_MODEL_NAME}\""
}

if is_healthy && [[ -f "${SIGNATURE_FILE}" ]] && [[ "$(cat "${SIGNATURE_FILE}")" == "${signature}" ]]; then
    echo "vLLM is already healthy with the requested configuration."
    exit 0
fi

architecture="$(uname -m)"

case "${architecture}" in
    x86_64|aarch64)
        ;;
    *)
        echo "Unsupported architecture: ${architecture}" >&2
        exit 1
        ;;
esac

VLLM_WHEEL_URL="https://github.com/vllm-project/vllm/releases/download/v${VLLM_VERSION}/vllm-${VLLM_VERSION}%2Bcu${VLLM_CUDA_VERSION}-cp38-abi3-manylinux_2_28_${architecture}.whl"

if [[ ! -x "${VLLM_VENV}/bin/vllm" ]]; then
    echo "Checking vLLM release artifact:"
    echo "  ${VLLM_WHEEL_URL}"
    if ! curl --fail --silent --show-error --location --head "${VLLM_WHEEL_URL}" >/dev/null; then
        echo >&2
        echo "vLLM wheel does not exist:" >&2
        echo "  ${VLLM_WHEEL_URL}" >&2
        echo >&2
        echo "vLLM version: ${VLLM_VERSION}" >&2
        echo "CUDA variant: cu${VLLM_CUDA_VERSION}" >&2
        echo "Architecture: ${architecture}" >&2
        exit 1
    fi

    echo "Creating local vLLM ${VLLM_VERSION} CUDA ${VLLM_CUDA_VERSION} environment at ${VLLM_VENV}..."
    if [[ ! -x "${VLLM_VENV}/bin/python" ]]; then
        PYTHON_BIN="$(command -v python3)"
        [[ -n "${PYTHON_BIN}" ]] || { echo "python3 is missing in the RunPod image" >&2; exit 1; }
        "${UV}" venv "${VLLM_VENV}" --python "${PYTHON_BIN}" --seed
    else
        echo "Reusing existing vLLM environment at ${VLLM_VENV}."
    fi

    echo "Installing vLLM ${VLLM_VERSION} CUDA ${VLLM_CUDA_VERSION} variant..."

    "${UV}" pip install \
        --python "${VLLM_VENV}/bin/python" \
        "${VLLM_WHEEL_URL}" \
        --extra-index-url "https://download.pytorch.org/whl/cu${VLLM_CUDA_VERSION}" \
        --index-strategy unsafe-best-match
fi

case "${VLLM_CUDA_VERSION}" in
    129) EXPECTED_CUDA_VERSION="12.9" ;;
    130) EXPECTED_CUDA_VERSION="13.0" ;;
    *)
        echo "vLLM ${VLLM_VERSION} does not have a supported configured CUDA variant: cu${VLLM_CUDA_VERSION}" >&2
        exit 1
        ;;
esac

echo "Validating vLLM runtime..."
if ! timeout "${VALIDATION_TIMEOUT_SECONDS}" "${VLLM_VENV}/bin/python" - <<'PY'
import sys

try:
    import torch
    import vllm
except Exception as exc:
    print(f"Failed to import the vLLM runtime: {exc}", file=sys.stderr)
    raise

print(f"PyTorch version: {torch.__version__}")
print(f"PyTorch CUDA version: {torch.version.cuda}")
print(f"vLLM version: {vllm.__version__}")

if not torch.cuda.is_available():
    raise RuntimeError("PyTorch was imported successfully but no CUDA device is available")

print(f"CUDA device: {torch.cuda.get_device_name(0)}")
PY
then
    echo "vLLM runtime validation did not complete within ${VALIDATION_TIMEOUT_SECONDS}s." >&2
    echo "This usually means importing torch or initializing CUDA is stuck." >&2
    exit 1
fi

if ! actual_cuda="$(
    timeout "${VALIDATION_TIMEOUT_SECONDS}" "${VLLM_VENV}/bin/python" - <<'PY'
import torch
print(torch.version.cuda or "")
PY
)"; then
    echo "Could not read the PyTorch CUDA runtime within ${VALIDATION_TIMEOUT_SECONDS}s." >&2
    exit 1
fi

if [[ "${actual_cuda}" != "${EXPECTED_CUDA_VERSION}" ]]; then
    echo "CUDA runtime mismatch: PyTorch reports ${actual_cuda}; expected ${EXPECTED_CUDA_VERSION}" >&2
    exit 1
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

echo "Starting vLLM ${VLLM_VERSION} with ${MODEL_ID}@${MODEL_REVISION}..."
# FlashInfer JIT invokes ninja by name. The executable is installed into this
# venv, so preserve it in PATH when launching vLLM through its absolute path.
export PATH="${VLLM_VENV}/bin:${PATH}"
nohup "${VLLM_VENV}/bin/vllm" serve "${MODEL_ID}" \
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
    >"${LOG_FILE}" 2>&1 < /dev/null &

echo "$!" > "${PID_FILE}"

deadline=$((SECONDS + START_TIMEOUT))
while (( SECONDS < deadline )); do
    if is_healthy; then
        printf '%s\n' "${signature}" > "${SIGNATURE_FILE}"
        echo "vLLM is ready."
        exit 0
    fi

    pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
        echo "vLLM terminated unexpectedly." >&2
        tail -n 120 "${LOG_FILE}" >&2 || true
        exit 1
    fi

    sleep 5
done

echo "vLLM startup timed out after ${START_TIMEOUT}s." >&2
tail -n 120 "${LOG_FILE}" >&2 || true
exit 1
