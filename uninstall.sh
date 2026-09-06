#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/llm-coding"
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/llm-coding"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/llm-coding"
BIN_DIR="${HOME}/.local/bin"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user stop llm-coding-proxy.service >/dev/null 2>&1 || true
if [[ -x "${BIN_DIR}/llm-runtime" ]]; then
    if ! "${BIN_DIR}/llm-runtime" remove-integration; then
        printf '%s %s\n' \
            'Warning: llm-runtime could not completely remove the' \
            'IDE integration.' >&2
    fi
else
    printf '%s\n' \
        'Warning: llm-runtime is unavailable; the IDE integration was not removed.' >&2
fi
systemctl --user disable --now llm-coding.socket >/dev/null 2>&1 || true

rm -f \
    "${SYSTEMD_USER_DIR}/llm-coding.socket" \
    "${SYSTEMD_USER_DIR}/llm-coding-proxy.service" \
    "${SYSTEMD_USER_DIR}/llm-coding-tunnel.service"

ENTRY_POINTS=(
    llm-doctor
    llm-down
    llm-install
    llm-runtime
    llm-status
    llm-up
    opencode-runpod
)
for name in "${ENTRY_POINTS[@]}"; do
    rm -f "${BIN_DIR}/${name}"
done

rm -rf "${INSTALL_DIR}"
systemctl --user daemon-reload

cat <<EOF
Removed the installed llm-coding runtime files and commands.

Preserved intentionally:
  ${CONFIG_DIR}
  ${STATE_DIR}
  the dedicated SSH key
  OpenCode itself
  the RunPod pod/persistent volume

Delete those manually if desired.

EOF
