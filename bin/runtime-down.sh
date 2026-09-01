#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

require_command flock
require_command jq
require_command systemctl

exec 9>"${STATE_DIR}/runtime.lock"
flock 9

systemctl --user stop llm-coding-tunnel.service >/dev/null 2>&1 || true

pod="$(find_pod_by_name)"
[[ -n "${pod}" ]] || exit 0

pod_id="$(jq -r '.id' <<<"${pod}")"
status="$(jq -r '.desiredStatus' <<<"${pod}")"

if [[ "${status}" == "RUNNING" ]]; then
    echo "Stopping RunPod ${pod_id}..." >&2
    runpod_api POST "/pods/${pod_id}/stop" >/dev/null
fi
