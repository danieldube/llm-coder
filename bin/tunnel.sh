#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/llm-coding"
RUNTIME_ENV="${STATE_DIR}/runtime.env"

[[ -f "${RUNTIME_ENV}" ]] || { echo "Missing ${RUNTIME_ENV}" >&2; exit 1; }
# shellcheck source=/dev/null
source "${RUNTIME_ENV}"

exec ssh \
    -N \
    -T \
    -i "${SSH_KEY}" \
    -p "${SSH_PORT}" \
    -o BatchMode=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile="${STATE_DIR}/known_hosts" \
    -L "127.0.0.1:${LOCAL_TUNNEL_PORT}:127.0.0.1:${REMOTE_VLLM_PORT}" \
    "root@${SSH_HOST}"
