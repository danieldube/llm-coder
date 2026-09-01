#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"

while IFS= read -r script; do
    bash -n "${script}"
done < <(find "${ROOT}" -type f \( -name '*.sh' -o -name 'llm-up' -o -name 'llm-down' -o -name 'llm-status' -o -name 'llm-doctor' -o -name 'opencode-runpod' \) | sort)

jq empty "${ROOT}/config/opencode.base.json"

if command -v shellcheck >/dev/null 2>&1; then
    mapfile -t scripts < <(find "${ROOT}" -type f \( -name '*.sh' -o -name 'llm-up' -o -name 'llm-down' -o -name 'llm-status' -o -name 'llm-doctor' -o -name 'opencode-runpod' \) | sort)
    shellcheck -x "${scripts[@]}"
fi

echo "Static checks passed."
