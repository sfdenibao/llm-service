#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
: "${LLM_LAB_TOOLS:?Run scripts/download_tools.py and set the verified directory}"
: "${API_KEY:?Set the service API key in the environment}"
profile="${BENCH_PROFILE:-optimized}"
exec .venv-bench/bin/python "$LLM_LAB_TOOLS/serving_benchmark.py" \
  --base-url "http://${HOST:-127.0.0.1}:${PORT:-8000}" --profile "$profile" \
  --config "$LLM_LAB_TOOLS/acceptance.json" --seed 20261018 --local-files-only \
  --out results/serving "$@"
