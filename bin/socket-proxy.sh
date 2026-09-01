#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

proxy=""
for candidate in \
    /usr/lib/systemd/systemd-socket-proxyd \
    /usr/libexec/systemd/systemd-socket-proxyd; do
    if [[ -x "${candidate}" ]]; then
        proxy="${candidate}"
        break
    fi
done

if [[ -z "${proxy}" ]] && command -v systemd-socket-proxyd >/dev/null 2>&1; then
    proxy="$(command -v systemd-socket-proxyd)"
fi

[[ -n "${proxy}" ]] || fatal "systemd-socket-proxyd was not found."

exec "${proxy}" \
    --exit-idle-time="${IDLE_SHUTDOWN}" \
    "127.0.0.1:${LOCAL_TUNNEL_PORT}"
