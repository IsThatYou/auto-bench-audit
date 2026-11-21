#!/usr/bin/env bash
# Kept for old commands. The logic now lives in `bench-audit audit`.
#
#   scripts/audit_one.sh --url https://github.com/centerforaisafety/hle --sample-n 3
# is the same as
#   bench-audit audit https://github.com/centerforaisafety/hle --sample-n 3
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [ -x "${ROOT_DIR}/.venv/bin/bench-audit" ]; then
    exec "${ROOT_DIR}/.venv/bin/bench-audit" audit "$@"
fi
exec bench-audit audit "$@"
