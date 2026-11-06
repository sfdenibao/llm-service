#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export PATH="$PWD/.venv-gpu/bin:$PATH"
exec .venv-gpu/bin/python -m experiments.run "$@"
