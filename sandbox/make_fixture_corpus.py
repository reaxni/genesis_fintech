"""Build a stand-in release corpus from the G0 fixture until role A ships one.

    python sandbox/make_fixture_corpus.py episodes

Creates <out>/train/ep00..ep09 and <out>/dev/ep00..ep09 (the layout the
runner's verify expects). Even numbers are the partial-payment fixture,
odd numbers its clean twin (pay-02 fully allocated, empty truth). Every
episode gets a unique episode_id. Test-only files are not copied.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/fixtures/g0_partial_payment"
TEST_ONLY = ("expected_findings.json", "expected_score.json")


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def make_episode(target: Path, episode_id: str, clean: bool) -> None:
    shutil.copytree(FIXTURE, target, ignore=shutil.ignore_patterns(*TEST_ONLY))
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    manifest["episode_id"] = episode_id
    if clean:
        with (FIXTURE / "ledger.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        fields = list(rows[0])
        kept = []
        for row in rows:
            if row["row_id"] == "row-06":
                row = dict(row, credit_minor="7000000", allocation_minor="7000000")
            if row["row_id"] != "row-07":
                kept.append(row)
        with (target / "ledger.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(kept)
        manifest["row_count"] = len(kept)
        _write_json(target / "truth.json", {"issues": []})
        _write_json(target / "provenance.json", {"schema_version": "1.0", "changes": []})
    _write_json(target / "manifest.json", manifest)


def main(out: str) -> None:
    root = Path(out)
    if root.exists():
        raise SystemExit(f"{root} already exists; refusing to overwrite")
    for split in ("train", "dev"):
        for i in range(10):
            clean = bool(i % 2)
            kind = "clean" if clean else "partial"
            make_episode(root / split / f"ep{i:02d}", f"{split}-{i:02d}-{kind}", clean)
    print(f"wrote 20 episodes under {root}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "episodes")
