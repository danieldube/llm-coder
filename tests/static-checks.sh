#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"

find_scripts() {
    find "${ROOT}" \
        \( -path "${ROOT}/.git" -o -path "${ROOT}/.venv" \) -prune -o \
        -type f \
        \( -name '*.sh' -o -name 'llm-up' -o -name 'llm-down' \
        -o -name 'llm-status' -o -name 'llm-doctor' \
        -o -name 'opencode-runpod' \) -print | sort
}

while IFS= read -r script; do
    bash -n "${script}"
done < <(find_scripts)

jq empty "${ROOT}/config/opencode.base.json"
jq -e '
  .enabled_providers == ["runpod"]
  and .model == "runpod/PLACEHOLDER"
  and (.provider | keys == ["runpod"])
' "${ROOT}/config/opencode.base.json" >/dev/null

grep -F -- '--enable-log-requests' "${ROOT}/docker/start-vllm.sh" >/dev/null

if command -v shellcheck >/dev/null 2>&1; then
    mapfile -t scripts < <(find_scripts)
    shellcheck -x "${scripts[@]}"
fi

echo "Static checks passed."
