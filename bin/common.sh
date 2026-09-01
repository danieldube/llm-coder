#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/llm-coding"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/llm-coding"
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/llm-coding"

CONFIG_FILE="${CONFIG_DIR}/config.env"
SECRETS_FILE="${CONFIG_DIR}/secrets.env"

fatal() {
    echo "ERROR: $*" >&2
    exit 1
}

require_file() {
    [[ -f "$1" ]] || fatal "Missing required file: $1"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fatal "Required command not found: $1"
}

require_file "${CONFIG_FILE}"
# shellcheck source=/dev/null
source "${CONFIG_FILE}"

if [[ -f "${SECRETS_FILE}" ]]; then
    # shellcheck source=/dev/null
    source "${SECRETS_FILE}"
fi

: "${VLLM_VERSION:?VLLM_VERSION must be configured}"
: "${VLLM_CUDA_VERSION:?VLLM_CUDA_VERSION must be configured}"

case "${VLLM_CUDA_VERSION}" in
    129|130)
        ;;
    *)
        fatal "Unsupported VLLM_CUDA_VERSION=${VLLM_CUDA_VERSION}. Expected 129 or 130 for vLLM ${VLLM_VERSION}."
        ;;
esac

mkdir -p "${STATE_DIR}"

require_runpod_credentials() {
    if [[ -z "${RUNPOD_API_KEY:-}" || "${RUNPOD_API_KEY}" == "REPLACE_ME" ]]; then
        fatal "RUNPOD_API_KEY is not configured in ${SECRETS_FILE}"
    fi
}

runpod_api() {
    local method="$1"
    local path="$2"
    local body="${3:-}"

    require_runpod_credentials
    require_command curl

    local args=(
        --fail-with-body
        --silent
        --show-error
        --connect-timeout 10
        --max-time 120
        --request "${method}"
        --url "https://rest.runpod.io/v1${path}"
        --header "Authorization: Bearer ${RUNPOD_API_KEY}"
    )

    if [[ -n "${body}" ]]; then
        args+=(
            --header "Content-Type: application/json"
            --data "${body}"
        )
    fi

    curl "${args[@]}"
}

find_pod_by_name() {
    require_command jq

    local pods
    local count

    pods="$(runpod_api GET /pods)"
    count="$(
        jq \
            --arg name "${RUNPOD_POD_NAME}" \
            '[.[] | select(.name == $name)] | length' \
            <<<"${pods}"
    )"

    if (( count > 1 )); then
        fatal "More than one RunPod named '${RUNPOD_POD_NAME}' exists. Resolve the duplicate before continuing."
    fi

    jq -c \
        --arg name "${RUNPOD_POD_NAME}" \
        '.[] | select(.name == $name)' \
        <<<"${pods}" \
        | head -n 1
}

write_shell_assignment() {
    local key="$1"
    local value="$2"
    printf '%s=%q\n' "${key}" "${value}"
}
