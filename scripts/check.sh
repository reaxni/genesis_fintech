#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
python -m unittest discover -s tests/contract_g0 -v
python -m unittest discover -s tests/scoring -v
python -m unittest discover -s tests/integration -v
