#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
for candidate in python3.12 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert sys.version_info[:2] == (3, 12)' 2>/dev/null; then
    exec "$candidate" -m server.bootstrap "$@"
  fi
done
echo 'Python 3.12 is required for the locked environment' >&2
exit 1
