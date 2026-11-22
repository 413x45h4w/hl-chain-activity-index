#!/usr/bin/env bash
# Minute loop for hosts where a long-running process is allowed.
# GitHub Actions cron cannot do this; see .github/workflows/collect.yml.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
exec python3 -m hl_activity run --interval "${INTERVAL:-60}" --workers "${WORKERS:-8}"
