#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/llm-coding"
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/llm-coding"
BIN_DIR="${HOME}/.local/bin"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
JETBRAINS_ACP="${HOME}/.jetbrains/acp.json"

agent_name="OpenCode RunPod"
if [[ -f "${CONFIG_DIR}/config.env" ]]; then
    # shellcheck source=/dev/null
    source "${CONFIG_DIR}/config.env"
    agent_name="${JETBRAINS_AGENT_NAME:-${agent_name}}"
fi

systemctl --user stop llm-coding-proxy.service >/dev/null 2>&1 || true
if [[ -x "${INSTALL_DIR}/bin/runtime-down.sh" ]]; then
    "${INSTALL_DIR}/bin/runtime-down.sh" || true
fi
systemctl --user disable --now llm-coding.socket >/dev/null 2>&1 || true

rm -f \
    "${SYSTEMD_USER_DIR}/llm-coding.socket" \
    "${SYSTEMD_USER_DIR}/llm-coding-proxy.service" \
    "${SYSTEMD_USER_DIR}/llm-coding-tunnel.service"

for name in llm-up llm-down llm-status llm-doctor opencode-runpod; do
    rm -f "${BIN_DIR}/${name}"
done

if [[ -f "${JETBRAINS_ACP}" ]] && command -v jq >/dev/null 2>&1; then
    tmp="${JETBRAINS_ACP}.tmp"
    jq --arg name "${agent_name}" 'del(.agent_servers[$name])' "${JETBRAINS_ACP}" > "${tmp}" && mv "${tmp}" "${JETBRAINS_ACP}"
fi

rm -rf "${INSTALL_DIR}"
systemctl --user daemon-reload

cat <<EOF
Removed the installed llm-coding runtime and JetBrains ACP entry.

Preserved intentionally:
  ${CONFIG_DIR}
  ~/.local/state/llm-coding
  the dedicated SSH key
  OpenCode itself
  the RunPod pod/persistent volume

Delete those manually if desired.

EOF
