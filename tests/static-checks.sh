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

jq empty "${ROOT}/python-src/llm_coding/assets/config/opencode.base.json"
jq -e '
  .enabled_providers == ["runpod"]
  and .model == "runpod/PLACEHOLDER"
  and (.provider | keys == ["runpod"])
' "${ROOT}/python-src/llm_coding/assets/config/opencode.base.json" >/dev/null

grep -F -- '--enable-log-requests' "${ROOT}/docker/start-vllm.sh" >/dev/null
grep -F -- 'HOST_DRIVER_LIB=/usr/lib/x86_64-linux-gnu' \
    "${ROOT}/docker/start-vllm.sh" >/dev/null
grep -F -- 'export LD_LIBRARY_PATH=' \
    "${ROOT}/docker/start-vllm.sh" >/dev/null
grep -F -- '--tensor-parallel-size' "${ROOT}/docker/start-vllm.sh" >/dev/null
grep -F -- '--kv-cache-dtype' "${ROOT}/docker/start-vllm.sh" >/dev/null
grep -F -- '--max-num-seqs' "${ROOT}/docker/start-vllm.sh" >/dev/null
grep -F -- '--enforce-eager' "${ROOT}/docker/start-vllm.sh" >/dev/null
grep -F -- '--language-model-only' "${ROOT}/docker/start-vllm.sh" >/dev/null
grep -F -- '--reasoning-parser' "${ROOT}/docker/start-vllm.sh" >/dev/null
grep -F -- 'FROM vllm/vllm-openai:v0.28.0-cu129-ubuntu2404' \
    "${ROOT}/docker/Dockerfile.cuda129" >/dev/null
grep -F -- 'ENTRYPOINT ["/usr/local/bin/container-start"]' \
    "${ROOT}/docker/Dockerfile.cuda129" >/dev/null
grep -F -- '--host 127.0.0.1' \
    "${ROOT}/docker/start-vllm.cuda129.sh" >/dev/null
grep -F -- 'PasswordAuthentication=no' \
    "${ROOT}/docker/container-start.sh" >/dev/null
grep -F -- 'PUBLIC_KEY' "${ROOT}/docker/container-start.sh" >/dev/null
grep -F -- 'validate-runtime' \
    "${ROOT}/docker/Dockerfile.cuda129" >/dev/null
grep -F -- '/usr/local/bin/vllm-python' \
    "${ROOT}/docker/Dockerfile.cuda129" >/dev/null
grep -F -- 'pip check' "${ROOT}/docker/Dockerfile.cuda129" >/dev/null
grep -F -- 'runpod/pytorch:1.3.1-cu1281-torch2130-ubuntu2404@sha256:8ee5a5d7c421cedb3fc3a9550f1360cf385af3986d9fd60ca14b0c25ec7cc5a3' \
    "${ROOT}/docker/Dockerfile.cuda128" >/dev/null
grep -F -- 'VLLM_SOURCE_REVISION=2cf0a69' \
    "${ROOT}/docker/Dockerfile.cuda128" >/dev/null
grep -F -- "'setuptools-rust>=1.9.0'" \
    "${ROOT}/docker/Dockerfile.cuda128" >/dev/null
grep -F -- 'apt-get install -y --no-install-recommends cargo' \
    "${ROOT}/docker/Dockerfile.cuda128" >/dev/null
grep -F -- 'candidate=cuda129-${tag}' \
    "${ROOT}/.github/workflows/publish-runtime-image.yml" >/dev/null
grep -F -- 'Promote validated candidate to stable alias' \
    "${ROOT}/.github/workflows/publish-runtime-image.yml" >/dev/null
grep -F -- 'file: docker/Dockerfile.cuda128' \
    "${ROOT}/.github/workflows/publish-cuda128-runtime-image.yml" >/dev/null
grep -F -- 'cancel-in-progress: false' \
    "${ROOT}/.github/workflows/publish-cuda128-runtime-image.yml" >/dev/null
for forbidden in \
    'pip install vllm' 'uv pip install vllm' 'git clone vllm' cmake ninja cargo \
    'pip install torch' 'uv pip install torch'; do
    if grep -F -- "${forbidden}" "${ROOT}/docker/Dockerfile.cuda129"; then
        echo "CUDA 12.9 image must not install or build ${forbidden}." >&2
        exit 1
    fi
done

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

config_source_pattern="(source|\\.)[[:space:]]+.*config\\.env"
if grep -n -E "${config_source_pattern}" "${ROOT}/uninstall.sh"; then
    echo 'uninstall.sh must not source configuration files.' >&2
    exit 1
fi

removed_lifecycle_pattern="runtime-(up|down)\\.sh|socket-proxy\\.sh|/bin/tunnel\\.sh"
while IFS= read -r asset; do
    if [[ "${asset}" == "${ROOT}/tests/static-checks.sh" ]]; then
        continue
    fi
    if grep -n -E "${removed_lifecycle_pattern}" "${asset}"; then
        echo "Executable asset references a removed lifecycle script: ${asset}" >&2
        exit 1
    fi
done < <(find_scripts)

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
mapfile -t uninstall_commands < <(
    sed -n '/^ENTRY_POINTS=(/,/^)/ {
        s/^[[:space:]]*\([a-z][a-z0-9-]*\)[[:space:]]*$/\1/p
    }' "${ROOT}/uninstall.sh" | sort
)
if [[ "${installed_commands[*]}" != "${uninstall_commands[*]}" ]]; then
    echo 'uninstall.sh entry-point list diverges from [project.scripts].' >&2
    printf 'Installed: %s\nRemoved:   %s\n' \
        "${installed_commands[*]}" "${uninstall_commands[*]}" >&2
    exit 1
fi
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
