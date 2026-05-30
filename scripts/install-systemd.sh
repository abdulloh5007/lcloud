#!/usr/bin/env bash
# Install + enable the LCloud systemd unit. Idempotent.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="${PROJECT_ROOT}/lcloud.service"
UNIT_DST="/etc/systemd/system/lcloud.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "must be run as root (or via sudo)" >&2
  exit 1
fi

if [[ ! -f "${UNIT_SRC}" ]]; then
  echo "missing ${UNIT_SRC}" >&2
  exit 2
fi

mkdir -p "${PROJECT_ROOT}/logs"

install -m 0644 "${UNIT_SRC}" "${UNIT_DST}"
systemctl daemon-reload
systemctl enable lcloud.service

echo "installed -> ${UNIT_DST}"
echo "to start:  systemctl start lcloud"
echo "to follow: journalctl -u lcloud -f"
