#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ "${1:-}" == "--bootstrap" ]]; then
    exec bash scripts/check.sh
fi
mkdir -p artifacts
project="genesis_verify_$$"
cleanup() {
    docker compose --project-name "$project" down --volumes >/dev/null 2>&1 || true
}
trap cleanup EXIT
command -v docker >/dev/null
docker compose --project-name "$project" run --rm --no-deps --no-TTY --pull never -e GENESIS_FULL_CHECKS=1 verify bash -ceu '
    bash scripts/check.sh
    for suite in tests/data tests/contract tests/verification; do
        python - "$suite" <<'"'"'PY'"'"'
import sys
import unittest
suite = unittest.defaultTestLoader.discover(sys.argv[1])
if not suite.countTestCases():
    raise SystemExit(f"No tests collected in {sys.argv[1]}")
if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
    raise SystemExit(1)
PY
    done
'
result=0
docker compose --project-name "$project" up --pull never --no-build --abort-on-container-exit --exit-code-from verify || result=$?
if ! docker compose --project-name "$project" cp verify:/out/. artifacts/; then
    if [[ "$result" -eq 0 ]]; then exit 1; fi
fi
exit "$result"
