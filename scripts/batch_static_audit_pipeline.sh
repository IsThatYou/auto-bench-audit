#!/usr/bin/env bash
# Kept for old commands. The logic now lives in `bench-audit batch`.
#
#   bash scripts/batch_static_audit_pipeline.sh --domain medical_health
# is the same as
#   AUDIT_RUN_DIR=data/multi_domain_all bench-audit batch --domain medical_health
#
# Output folder: ${MULTI_DOMAIN_AUDIT_RUN_DIR}, default ./data/multi_domain_all.
# The tracker is in that folder (streaming_pipeline_tracking.json).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
[ -f "${ROOT_DIR}/.env" ] && { set -a; source "${ROOT_DIR}/.env"; set +a; }

export AUDIT_RUN_DIR="${MULTI_DOMAIN_AUDIT_RUN_DIR:-${ROOT_DIR}/data/multi_domain_all}"
export BENCH_AUDIT_DEBUG_PROMPTS="${BENCH_AUDIT_DEBUG_PROMPTS:-0}"

if [ -x "${ROOT_DIR}/.venv/bin/bench-audit" ]; then
    exec "${ROOT_DIR}/.venv/bin/bench-audit" batch "$@"
fi
exec bench-audit batch "$@"
