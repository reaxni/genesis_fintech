"""File readers for the trusted adapter and scorer."""

import csv
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .v1 import fx_evidence_id


def read_json(directory: Path, filename: str) -> Any:
    with (directory / filename).open(encoding="utf-8") as handle:
        return json.load(handle)


def read_json_lines(directory: Path, filename: str) -> Iterator[dict]:
    with (directory / filename).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def read_csv(directory: Path, filename: str) -> Iterator[dict]:
    with (directory / filename).open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def public_evidence_ids(directory: Path) -> frozenset[str]:
    """Return only evidence addressable through the public record tools."""
    ids: set[str] = set()
    ids.update(row["row_id"] for row in read_csv(directory, "ledger.csv"))
    for filename in ("invoices.jsonl", "payments.jsonl"):
        ids.update(record["doc_id"] for record in read_json_lines(directory, filename))
    ids.update(fx_evidence_id(row) for row in read_csv(directory, "fx.csv"))
    return frozenset(ids)
