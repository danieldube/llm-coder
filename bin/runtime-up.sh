#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

require_command flock
require_command jq
require_command ssh
require_command ssh-keygen
require_command systemctl

exec 9>"${STATE_DIR}/runtime.lock"
flock 9

create_pod() {
    local public_key
    local body
    local response

    [[ -r "${RUNPOD_SSH_KEY}.pub" ]] || fatal "SSH public key not found: ${RUNPOD_SSH_KEY}.pub"
    public_key="$(cat "${RUNPOD_SSH_KEY}.pub")"

    body="$(
        jq -n \
            --arg name "${RUNPOD_POD_NAME}" \
            --arg image "${RUNPOD_IMAGE}" \
            --arg gpu "${RUNPOD_GPU_TYPE}" \
            --arg cloud "${RUNPOD_CLOUD_TYPE}" \
            --arg public_key "${public_key}" \
            --arg mount_path "${RUNPOD_VOLUME_MOUNT_PATH}" \
            --arg network_volume "${RUNPOD_NETWORK_VOLUME_ID:-}" \
            --argjson container_disk "${RUNPOD_CONTAINER_DISK_GB}" \
            --argjson volume "${RUNPOD_VOLUME_GB}" \
            --argjson min_ram "${RUNPOD_MIN_RAM_PER_GPU}" \
            --argjson min_vcpu "${RUNPOD_MIN_VCPU_PER_GPU}" \
            '
            {
                name: $name,
                imageName: $image,
                cloudType: $cloud,
                computeType: "GPU",
                gpuTypeIds: [$gpu],
                gpuTypePriority: "availability",
                gpuCount: 1,
                interruptible: false,
                supportPublicIp: true,
                containerDiskInGb: $container_disk,
                volumeMountPath: $mount_path,
                minRAMPerGPU: $min_ram,
                minVCPUPerGPU: $min_vcpu,
                ports: ["22/tcp"],
                env: {
                    SSH_PUBLIC_KEY: $public_key
                }
            }
            + if ($network_volume | length) > 0
              then {networkVolumeId: $network_volume}
              else {volumeInGb: $volume}
              end
            '
    )"

    echo "Creating RunPod '${RUNPOD_POD_NAME}'..." >&2
    response="$(runpod_api POST /pods "${body}")"

    jq -er '.id' <<<"${response}"
}

pod="$(find_pod_by_name)"

if [[ -z "${pod}" ]]; then
    pod_id="$(create_pod)"
else
    pod_id="$(jq -r '.id' <<<"${pod}")"
    desired_status="$(jq -r '.desiredStatus' <<<"${pod}")"

    case "${desired_status}" in
        RUNNING)
            echo "RunPod ${pod_id} is already running." >&2
            ;;
        EXITED)
            echo "Starting RunPod ${pod_id}..." >&2
            runpod_api POST "/pods/${pod_id}/start" >/dev/null
            ;;
        TERMINATED)
            fatal "RunPod ${pod_id} is TERMINATED. Delete it or change RUNPOD_POD_NAME before retrying."
            ;;
        *)
            echo "RunPod ${pod_id} reports status '${desired_status}'; waiting for it to become usable." >&2
            ;;
    esac
fi

echo "Using RunPod ${pod_id}." >&2

deadline=$((SECONDS + RUNPOD_START_TIMEOUT_SECONDS))
ssh_host=""
ssh_port=""

while (( SECONDS < deadline )); do
    pod="$(runpod_api GET "/pods/${pod_id}")"

    ssh_host="$(jq -r '.publicIp // empty' <<<"${pod}")"
    ssh_port="$(jq -r '.portMappings["22"] // empty' <<<"${pod}")"

    if [[ -n "${ssh_host}" && -n "${ssh_port}" ]]; then
        break
    fi

    sleep 5
done

[[ -n "${ssh_host}" && -n "${ssh_port}" ]] || fatal "RunPod did not expose SSH before timeout."

known_hosts="${STATE_DIR}/known_hosts"
touch "${known_hosts}"
chmod 600 "${known_hosts}"

# RunPod can assign a different external address/port after resume and the
# container's SSH host key can be regenerated. Keep host keys scoped to this
# stack rather than polluting ~/.ssh/known_hosts.
ssh-keygen -R "[${ssh_host}]:${ssh_port}" -f "${known_hosts}" >/dev/null 2>&1 || true

SSH=(
    ssh
    -T
    -i "${RUNPOD_SSH_KEY}"
    -p "${ssh_port}"
    -o BatchMode=yes
    -o ConnectTimeout=5
    -o ServerAliveInterval=30
    -o ServerAliveCountMax=3
    -o StrictHostKeyChecking=accept-new
    -o UserKnownHostsFile="${known_hosts}"
    "root@${ssh_host}"
)

echo "Waiting for SSH..." >&2
ssh_error_file="${STATE_DIR}/ssh-last-error"
while ! "${SSH[@]}" true >/dev/null 2>"${ssh_error_file}"; do
    if (( SECONDS >= deadline )); then
        cat "${ssh_error_file}" >&2 || true
        fatal "SSH did not become available before timeout."
    fi

    # RunPod can reassign the external SSH endpoint while a Pod resumes. Do
    # not keep retrying a stale address or port for the full startup timeout.
    refreshed_pod="$(runpod_api GET "/pods/${pod_id}")"
    refreshed_host="$(jq -r '.publicIp // empty' <<<"${refreshed_pod}")"
    refreshed_port="$(jq -r '.portMappings["22"] // empty' <<<"${refreshed_pod}")"
    if [[ -n "${refreshed_host}" && -n "${refreshed_port}" ]] \
        && [[ "${refreshed_host}" != "${ssh_host}" || "${refreshed_port}" != "${ssh_port}" ]]; then
        echo "RunPod reassigned the SSH endpoint; retrying." >&2
        ssh_host="${refreshed_host}"
        ssh_port="${refreshed_port}"
        ssh-keygen -R "[${ssh_host}]:${ssh_port}" -f "${known_hosts}" >/dev/null 2>&1 || true
        SSH=(
            ssh
            -T
            -i "${RUNPOD_SSH_KEY}"
            -p "${ssh_port}"
            -o BatchMode=yes
            -o ConnectTimeout=5
            -o ServerAliveInterval=30
            -o ServerAliveCountMax=3
            -o StrictHostKeyChecking=accept-new
            -o UserKnownHostsFile="${known_hosts}"
            "root@${ssh_host}"
        )
    fi
    sleep 5
done

echo "Ensuring remote vLLM runtime..." >&2
"${SSH[@]}" \
    bash -s -- \
    "${VLLM_VERSION}" \
    "${VLLM_CUDA_VERSION}" \
    "${MODEL_ID}" \
    "${MODEL_REVISION}" \
    "${SERVED_MODEL_NAME}" \
    "${CONTEXT_SIZE}" \
    "${VLLM_GPU_MEMORY_UTILIZATION}" \
    "${VLLM_TOOL_CALL_PARSER}" \
    "${REMOTE_VLLM_PORT}" \
    "${VLLM_START_TIMEOUT_SECONDS}" \
    < "${INSTALL_DIR}/remote/ensure-vllm.sh"

runtime_env="${STATE_DIR}/runtime.env"
{
    write_shell_assignment SSH_HOST "${ssh_host}"
    write_shell_assignment SSH_PORT "${ssh_port}"
    write_shell_assignment SSH_KEY "${RUNPOD_SSH_KEY}"
    write_shell_assignment LOCAL_TUNNEL_PORT "${LOCAL_TUNNEL_PORT}"
    write_shell_assignment REMOTE_VLLM_PORT "${REMOTE_VLLM_PORT}"
} > "${runtime_env}.tmp"
chmod 600 "${runtime_env}.tmp"
mv "${runtime_env}.tmp" "${runtime_env}"

systemctl --user restart llm-coding-tunnel.service

tunnel_deadline=$((SECONDS + 60))
while (( SECONDS < tunnel_deadline )); do
    if curl --fail --silent --max-time 2 \
        "http://127.0.0.1:${LOCAL_TUNNEL_PORT}/v1/models" \
        | jq -e --arg model "${SERVED_MODEL_NAME}" '.data[]? | select(.id == $model)' \
        >/dev/null 2>&1; then
        echo "LLM runtime is ready." >&2
        exit 0
    fi
    sleep 2
done

journalctl --user -u llm-coding-tunnel.service -n 30 --no-pager >&2 || true
fatal "SSH tunnel did not become healthy."
