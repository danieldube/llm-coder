#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/llm-coding"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/llm-coding"
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/llm-coding"
BIN_DIR="${HOME}/.local/bin"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
JETBRAINS_DIR="${HOME}/.jetbrains"
JETBRAINS_ACP="${JETBRAINS_DIR}/acp.json"

fatal() { echo "ERROR: $*" >&2; exit 1; }

for cmd in bash curl jq ssh ssh-keygen flock systemctl sed tar; do
    command -v "${cmd}" >/dev/null 2>&1 || fatal "Missing '${cmd}'. On Ubuntu install: sudo apt install curl jq openssh-client util-linux"
done

if [[ ! -x /usr/lib/systemd/systemd-socket-proxyd ]] \
    && [[ ! -x /usr/libexec/systemd/systemd-socket-proxyd ]] \
    && ! command -v systemd-socket-proxyd >/dev/null 2>&1; then
    fatal "systemd-socket-proxyd is unavailable on this system."
fi

mkdir -p "${CONFIG_DIR}" "${STATE_DIR}" "${INSTALL_DIR}" "${BIN_DIR}" "${SYSTEMD_USER_DIR}" "${JETBRAINS_DIR}"

# Install a clean snapshot while preserving user-owned config outside it.
rm -rf "${INSTALL_DIR}.new"
mkdir -p "${INSTALL_DIR}.new"
cp -a "${SOURCE_DIR}/." "${INSTALL_DIR}.new/"
rm -rf "${INSTALL_DIR}.new/.git" "${INSTALL_DIR}.new/llm-coding.zip" 2>/dev/null || true
rm -rf "${INSTALL_DIR}"
mv "${INSTALL_DIR}.new" "${INSTALL_DIR}"

find "${INSTALL_DIR}/bin" "${INSTALL_DIR}/remote" -type f -name '*.sh' -exec chmod 755 {} +
chmod 755 "${INSTALL_DIR}/bin/opencode-runpod" \
          "${INSTALL_DIR}/bin/llm-up" \
          "${INSTALL_DIR}/bin/llm-down" \
          "${INSTALL_DIR}/bin/llm-status" \
          "${INSTALL_DIR}/bin/llm-doctor"

if [[ ! -f "${CONFIG_DIR}/config.env" ]]; then
    cp "${INSTALL_DIR}/config/config.env.example" "${CONFIG_DIR}/config.env"
    chmod 600 "${CONFIG_DIR}/config.env"
    echo "Created ${CONFIG_DIR}/config.env"
fi

if [[ ! -f "${CONFIG_DIR}/secrets.env" ]]; then
    cp "${INSTALL_DIR}/config/secrets.env.example" "${CONFIG_DIR}/secrets.env"
    chmod 600 "${CONFIG_DIR}/secrets.env"
    echo "Created ${CONFIG_DIR}/secrets.env -- add your RunPod API key."
fi

# shellcheck source=/dev/null
source "${CONFIG_DIR}/config.env"

# Generate the dedicated RunPod key if necessary.
expanded_ssh_key="${RUNPOD_SSH_KEY/#\~/$HOME}"
if [[ ! -f "${expanded_ssh_key}" ]]; then
    mkdir -p "$(dirname "${expanded_ssh_key}")"
    ssh-keygen -q -t ed25519 -f "${expanded_ssh_key}" -N '' -C 'runpod-llm-coding'
    echo "Generated dedicated RunPod key: ${expanded_ssh_key}"
fi

# Normalize the config to an absolute key path so systemd does not depend on
# shell tilde expansion.
if [[ "${expanded_ssh_key}" != "${RUNPOD_SSH_KEY}" ]]; then
    sed -i "s|^RUNPOD_SSH_KEY=.*$|RUNPOD_SSH_KEY=\"${expanded_ssh_key}\"|" "${CONFIG_DIR}/config.env"
    RUNPOD_SSH_KEY="${expanded_ssh_key}"
fi

# Install exactly the pinned OpenCode version through the official installer.
opencode_bin="${HOME}/.opencode/bin/opencode"
installed_version=""
if [[ -x "${opencode_bin}" ]]; then
    installed_version="$(${opencode_bin} --version 2>/dev/null | tr -d '[:space:]' || true)"
fi
if [[ "${installed_version}" != "${OPENCODE_VERSION}" ]]; then
    echo "Installing OpenCode ${OPENCODE_VERSION}..."
    curl -fsSL https://opencode.ai/install \
        | bash -s -- --version "${OPENCODE_VERSION}" --no-modify-path
fi

# Render the socket unit because systemd socket units cannot source config.env.
sed "s/@LOCAL_PROXY_PORT@/${LOCAL_PROXY_PORT}/g" \
    "${INSTALL_DIR}/systemd/llm-coding.socket.in" \
    > "${SYSTEMD_USER_DIR}/llm-coding.socket"
cp "${INSTALL_DIR}/systemd/llm-coding-proxy.service" "${SYSTEMD_USER_DIR}/llm-coding-proxy.service"
cp "${INSTALL_DIR}/systemd/llm-coding-tunnel.service" "${SYSTEMD_USER_DIR}/llm-coding-tunnel.service"

# Stable convenience commands.
for name in llm-up llm-down llm-status llm-doctor opencode-runpod; do
    ln -sfn "${INSTALL_DIR}/bin/${name}" "${BIN_DIR}/${name}"
done

# Merge our ACP agent without overwriting unrelated agents.
if [[ -f "${JETBRAINS_ACP}" ]]; then
    cp "${JETBRAINS_ACP}" "${JETBRAINS_ACP}.backup.$(date +%Y%m%d%H%M%S)"
    jq empty "${JETBRAINS_ACP}" || fatal "Existing ${JETBRAINS_ACP} is invalid JSON; fix it before installation."
else
    printf '%s\n' '{"default_mcp_settings":{},"agent_servers":{}}' > "${JETBRAINS_ACP}"
fi

wrapper="${INSTALL_DIR}/bin/opencode-runpod"
tmp_acp="${JETBRAINS_ACP}.tmp"
jq \
    --arg name "${JETBRAINS_AGENT_NAME}" \
    --arg command "${wrapper}" \
    --argjson idea_mcp "${ENABLE_IDEA_MCP}" \
    --argjson custom_mcp "${ENABLE_CUSTOM_MCP}" \
    '
      .default_mcp_settings = (.default_mcp_settings // {})
      | .default_mcp_settings.use_idea_mcp = $idea_mcp
      | .default_mcp_settings.use_custom_mcp = $custom_mcp
      | .agent_servers = (.agent_servers // {})
      | .agent_servers[$name] = {command:$command,args:["acp"]}
    ' "${JETBRAINS_ACP}" > "${tmp_acp}"
mv "${tmp_acp}" "${JETBRAINS_ACP}"
chmod 600 "${JETBRAINS_ACP}"

systemctl --user daemon-reload
systemctl --user enable --now llm-coding.socket

cat <<EOF

Installation complete.

Next steps:
  1. Edit ${CONFIG_DIR}/secrets.env and set RUNPOD_API_KEY.
  2. Review ${CONFIG_DIR}/config.env.
  3. Run: ${BIN_DIR}/llm-doctor
  4. Run once for full initialization: ${BIN_DIR}/llm-doctor --activate
  5. In CLion AI Chat, select '${JETBRAINS_AGENT_NAME}'.

The GPU is started lazily on the first LLM request and stopped after ${IDLE_SHUTDOWN} of inactivity.

EOF
