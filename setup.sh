#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; assert sys.version_info >= (3, 12)' 2>/dev/null; then
  exec python3 -m server.bootstrap "$@"
fi
exec python -m server.bootstrap "$@"
