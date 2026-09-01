#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

require_command jq

base="${INSTALL_DIR}/config/opencode.base.json"
out="${STATE_DIR}/opencode.json"
require_file "${base}"

jq \
    --arg base_url "http://127.0.0.1:${LOCAL_PROXY_PORT}/v1" \
    --arg served_model "${SERVED_MODEL_NAME}" \
    --arg display_name "${MODEL_DISPLAY_NAME}" \
    --argjson context "${CONTEXT_SIZE}" \
    --argjson output "${MAX_OUTPUT_TOKENS}" \
    '
      .model = ("runpod/" + $served_model)
      | .provider.runpod.options.baseURL = $base_url
      | .provider.runpod.models = {
          ($served_model): {
            name: $display_name,
            limit: {
              context: $context,
              output: $output
            }
          }
        }
    ' \
    "${base}" > "${out}.tmp"

chmod 600 "${out}.tmp"
mv "${out}.tmp" "${out}"
