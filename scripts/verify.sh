#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ "${1:-}" == "--bootstrap" ]]; then
    exec bash scripts/check.sh
fi
case "${1:-}" in
    ""|--container-smoke) ;;
    *) echo "Usage: bash scripts/verify.sh [--bootstrap|--container-smoke]" >&2; exit 2 ;;
esac
project="genesis_verify_$$"
compose=(docker compose --project-name "$project")
cleanup() {
    "${compose[@]}" down --volumes --rmi local >/dev/null 2>&1 || true
}
trap cleanup EXIT
command -v docker >/dev/null
if [[ "${1:-}" == "--container-smoke" ]]; then
    "${compose[@]}" build verify
    "${compose[@]}" run --rm --no-deps --no-TTY --pull never verify bash scripts/check.sh
    exit 0
fi
mkdir -p artifacts
"${compose[@]}" build
"${compose[@]}" run --rm --no-deps --no-TTY --pull never -e GENESIS_FULL_CHECKS=1 verify bash -ceu '
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
"${compose[@]}" up --pull never --no-build --abort-on-container-exit --exit-code-from verify || result=$?
if ! "${compose[@]}" cp verify:/out/. artifacts/; then
    if [[ "$result" -eq 0 ]]; then exit 1; fi
fi
exit "$result"
