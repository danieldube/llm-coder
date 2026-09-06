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

while IFS= read -r metadata; do
    if [[ -e "${ROOT}/${metadata}" ]]; then
        echo 'Tracked egg-info metadata is forbidden; generate it only for packaging tests.' >&2
        exit 1
    fi
done < <(git -C "${ROOT}" ls-files | grep -E '(^|/)[^/]*\.egg-info/')

if grep -R -n -E \
    'runtime-(up|down)\.sh|socket-proxy\.sh|/bin/tunnel\.sh' \
    "${ROOT}/README.md" "${ROOT}/docs" "${ROOT}/SECURITY.md" \
    "${ROOT}/systemd"; then
    echo 'Documentation or systemd assets reference removed lifecycle scripts.' >&2
    exit 1
fi

license_file="$({
    sed -n 's/^license-files = \["\([^"]*\)"\]/\1/p' \
        "${ROOT}/pyproject.toml"
} | head -n 1)"
if [[ -z "${license_file}" || ! -f "${ROOT}/${license_file}" ]]; then
    echo 'The PEP 621 license-files declaration must name an existing file.' >&2
    exit 1
fi

mapfile -t installed_commands < <(
    sed -n '/^\[project\.scripts\]$/,/^\[/ {
        s/^\([a-z][a-z0-9-]*\) = .*/\1/p
    }' "${ROOT}/pyproject.toml" | sort
)
mapfile -t documented_commands < <(
    sed -n "s/^| \`\\([a-z][a-z0-9-]*\\)\` |.*/\\1/p" \
        "${ROOT}/README.md" | sort
)
if [[ "${installed_commands[*]}" != "${documented_commands[*]}" ]]; then
    echo 'README command table diverges from [project.scripts].' >&2
    printf 'Installed:  %s\nDocumented: %s\n' \
        "${installed_commands[*]}" "${documented_commands[*]}" >&2
    exit 1
fi

if command -v shellcheck >/dev/null 2>&1; then
    mapfile -t scripts < <(find_scripts)
    shellcheck -x "${scripts[@]}"
fi

echo "Static checks passed."
