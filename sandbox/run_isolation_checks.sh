#!/usr/bin/env bash
# Run the C18–C20 checks and write results to artifacts/isolation/.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
for f in compose.yaml Dockerfile judge_adapter.py src/genesis_audit/runner/session.py; do
    [[ -f "$f" ]] || { echo "missing $f" >&2; exit 2; }
done
# Docker Desktop on macOS keeps its credential helper outside the default PATH
[[ -d /Applications/Docker.app/Contents/Resources/bin ]] && export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"

project="genesis_b_iso_$$"
out="artifacts/isolation"
dc() { docker compose --project-name "$project" "$@"; }
cleanup() {
    docker rm -f "$project-c18-hang" "$project-c18-slow" >/dev/null 2>&1 || true
    dc down --volumes --rmi local >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ ! -d episodes/train || ! -d episodes/dev ]]; then
    echo "Missing published train or dev corpus in episodes/" >&2
    exit 2
fi
mkdir -p "$out"

echo "== build"
dc build >/dev/null

echo "== unit/contract/integration suites inside the Linux verify container"
dc run --rm --no-deps -T --pull never -e GENESIS_FULL_CHECKS=1 verify bash -euo pipefail -c '
    python --version
    uname -sm
    for s in contract_g0 scoring integration contract; do
        echo "-- tests/$s"; python -m unittest discover -s "tests/$s" 2>&1 | tail -1
    done' | tee "$out/suites.txt"

echo "== full Compose verify (oracle + 3 policies over Unix socket)"
dc up --pull never --no-build --abort-on-container-exit --exit-code-from verify >/dev/null
dc cp verify:/out/. "$out/"

echo "== C20: probe from inside the policy container"
dc run --rm --no-deps -T --pull never policy python - < sandbox/probe_policy_container.py > "$out/probe.json"

echo "== C18: real 60 s deadline with a hung and a slow policy (~60 s)"
pids=()
for sc in hang slow; do
    dc run --rm --no-deps -T --pull never --name "$project-c18-$sc" policy \
        python - "$sc" "c18$sc" < sandbox/c18_policy.py >/dev/null 2>&1 &
    dc run --rm --no-deps -T --pull never verify \
        python - "c18$sc" < sandbox/c18_trusted.py > "$out/c18_$sc.json" &
    pids+=($!)
done
wait "${pids[@]}"

python3 - "$out" <<'PY'
import json, sys
from collections import defaultdict
out = sys.argv[1]
probe = json.load(open(f"{out}/probe.json"))
leaks = [k for k, v in probe.items() if isinstance(v, dict) and v.get("allowed")]
assert not leaks, f"policy container could: {leaks}"
assert probe["episode_or_secret_files_found"] == [], probe["episode_or_secret_files_found"]
assert probe["CapEff"] == "0000000000000000" and probe["NoNewPrivs"] == "1"
assert probe["uid_gid"] != "0:0"
print("C20 probe: no network, no docker socket, read-only fs, no episode/scorer files, uid", probe["uid_gid"])
for sc in ("hang", "slow"):
    r = json.load(open(f"{out}/c18_{sc}.json"))
    assert r["truncated"] and r["timed_out"] and not r["terminated"], r
    assert r["frozen_findings"]["findings"] == [] and r["nonzero_rewards"] == 0, r
    assert 59.9 <= r["elapsed_seconds"] <= 61, r
    print(f"C18 {sc}: truncated after {r['elapsed_seconds']:.2f}s, steps={r['steps']}, findings empty")
rewards = [json.loads(l)["reward"] for l in open(f"{out}/trajectories.jsonl")]
assert rewards and all(type(x) in (int, float) and x == 0 for x in rewards)
agg = defaultdict(list)
for row in map(json.loads, open(f"{out}/metrics.jsonl")):
    agg[(row["policy"], row.get("mode", "closed"))].append(row["return"])
print(f"C19: {len(rewards)} closed policy steps, all rewards 0")
for key, values in sorted(agg.items()):
    print(f"  {key[0]:15s} {key[1]:6s} n={len(values):2d} mean return={sum(values)/len(values):.3f}")
PY
echo "OK: results in $out/"
