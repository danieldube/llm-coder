#!/usr/bin/env bash
set -euo pipefail

# RunPod documents PUBLIC_KEY for custom Pod images. SSH_PUBLIC_KEY is accepted
# temporarily so the existing CUDA 12.8 controller contract remains usable.
public_key="${PUBLIC_KEY:-${SSH_PUBLIC_KEY:-}}"
if [[ -z "${public_key}" ]]; then
    echo 'RunPod PUBLIC_KEY is required to start SSH.' >&2
    exit 64
fi

install -d -m 0700 /root/.ssh /run/sshd
printf '%s\n' "${public_key}" > /root/.ssh/authorized_keys
chmod 0600 /root/.ssh/authorized_keys

# Host keys are generated at first container start, never embedded in an image
# layer. Existing keys are retained across a Pod stop/resume when RunPod keeps
# the container filesystem.
ssh-keygen -A

exec /usr/sbin/sshd -D -e \
    -o PasswordAuthentication=no \
    -o KbdInteractiveAuthentication=no \
    -o PermitRootLogin=prohibit-password \
    -o PubkeyAuthentication=yes
