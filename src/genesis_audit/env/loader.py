"""Load the public part of one episode directory into an immutable index.

Only the six public files are opened. truth.json and provenance.json are
never read here: scoring happens through the score_episode callback on the
trusted side. Record order and IDs are preserved exactly as on disk.
File parsing reuses genesis_audit.contracts.files; this module adds the
public type conventions (CSV empty cell -> null, *_minor -> int, FX rate
stays a decimal string) and the lookup indexes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from genesis_audit.contracts.files import read_csv, read_json, read_json_lines
from genesis_audit.contracts.v1 import TABLES, fx_evidence_id

PUBLIC_FILES = (
    "manifest.json",
    "policy.json",
    "ledger.csv",
    "invoices.jsonl",
    "payments.jsonl",
    "fx.csv",
)

# Primary key used by get_record for each table.
RECORD_KEYS = {
    "ledger": "row_id",
    "invoices": "invoice_id",
    "payments": "payment_id",
    "fx": None,  # business key date:source_currency:target_currency
}


class EpisodeLoadError(ValueError):
    """The episode directory is unreadable or inconsistent (trusted side only)."""


@dataclass(frozen=True)
class EpisodeIndex:
    episode_id: str
    manifest: Mapping[str, Any]
    policy: Mapping[str, Any]
    tables: Mapping[str, Tuple[Dict[str, Any], ...]]
    by_id: Mapping[str, Mapping[str, Dict[str, Any]]]
    documents: Mapping[str, Dict[str, Any]]


def _csv_rows(directory: Path, filename: str) -> Iterable[Dict[str, str]]:
    for number, row in enumerate(read_csv(directory, filename), start=2):
        if None in row:  # DictReader puts surplus cells under the None key
            raise EpisodeLoadError(f"{filename} line {number} has more cells than the header")
        yield row


def _ledger_value(filename: str, column: str, raw: Any) -> Any:
    if raw is None or raw == "":
        return None  # CSV empty cell of a nullable field -> JSON null
    if column.endswith("_minor"):
        try:
            return int(raw)
        except ValueError as exc:
            raise EpisodeLoadError(f"{filename} column {column} is not an integer") from exc
    return raw


def _read_ledger(directory: Path) -> List[Dict[str, Any]]:
    return [
        {column: _ledger_value("ledger.csv", column, value) for column, value in row.items()}
        for row in _csv_rows(directory, "ledger.csv")
    ]


def _read_fx(directory: Path) -> List[Dict[str, Any]]:
    # rate stays a decimal string: FX is Decimal-from-string by contract
    return [
        {column: (value if value not in (None, "") else None) for column, value in row.items()}
        for row in _csv_rows(directory, "fx.csv")
    ]


def _read_objects(directory: Path, filename: str) -> List[Dict[str, Any]]:
    records = list(read_json_lines(directory, filename))
    if not all(isinstance(record, dict) for record in records):
        raise EpisodeLoadError(f"{filename} must contain one object per line")
    return records


def _read_object(directory: Path, filename: str) -> Dict[str, Any]:
    value = read_json(directory, filename)
    if not isinstance(value, dict):
        raise EpisodeLoadError(f"{filename} must contain an object")
    return value


def _record_id(table: str, record: Dict[str, Any]) -> Any:
    key = RECORD_KEYS[table]
    if key is not None:
        return record.get(key)
    try:
        return fx_evidence_id(record)
    except (KeyError, TypeError):
        return None


def load_episode(episode_dir: str | Path) -> EpisodeIndex:
    directory = Path(episode_dir)
    missing = [name for name in PUBLIC_FILES if not (directory / name).is_file()]
    if missing:
        raise EpisodeLoadError(f"episode is missing public files: {', '.join(missing)}")

    try:
        manifest = _read_object(directory, "manifest.json")
        policy = _read_object(directory, "policy.json")
        tables: Dict[str, Tuple[Dict[str, Any], ...]] = {
            "ledger": tuple(_read_ledger(directory)),
            "invoices": tuple(_read_objects(directory, "invoices.jsonl")),
            "payments": tuple(_read_objects(directory, "payments.jsonl")),
            "fx": tuple(_read_fx(directory)),
        }
    except EpisodeLoadError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise EpisodeLoadError(f"cannot read episode files: {type(exc).__name__}") from exc

    episode_id = manifest.get("episode_id")
    if not isinstance(episode_id, str) or not episode_id:
        raise EpisodeLoadError("manifest.episode_id must be a nonempty string")
    assert set(tables) == set(TABLES)

    by_id: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for table, records in tables.items():
        index: Dict[str, Dict[str, Any]] = {}
        for record in records:
            record_id = _record_id(table, record)
            if not isinstance(record_id, str) or not record_id:
                raise EpisodeLoadError(f"{table} record without a string id")
            if record_id in index:
                raise EpisodeLoadError(f"duplicate id in {table}")
            index[record_id] = record
        by_id[table] = index

    documents: Dict[str, Dict[str, Any]] = {}
    for table in ("invoices", "payments"):
        for record in tables[table]:
            doc_id = record.get("doc_id")
            if not isinstance(doc_id, str) or not doc_id or doc_id in documents:
                raise EpisodeLoadError("documents need unique string doc_id")
            documents[doc_id] = record

    return EpisodeIndex(
        episode_id=episode_id,
        manifest=manifest,
        policy=policy,
        tables=tables,
        by_id=by_id,
        documents=documents,
    )
