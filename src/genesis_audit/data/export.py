"""Encode and decode one trusted v1.0 episode directory."""

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from genesis_audit.contracts.files import read_csv, read_json, read_json_lines

LEDGER_FIELDS = (
    "row_id", "timestamp", "account", "debit_minor", "credit_minor", "currency",
    "entity_id", "invoice_id", "payment_id", "posting_group_id", "event_at",
    "allocation_minor", "allocation_currency", "fx_source_minor", "fx_source_currency",
)
FX_FIELDS = ("date", "source_currency", "target_currency", "rate")
JSON_FILES = ("manifest", "policy", "truth", "provenance")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def write_episode(directory, episode):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=directory, prefix=".episode-") as staging:
        staging = Path(staging)
        for key in JSON_FILES:
            (staging / f"{key}.json").write_bytes((_json(episode[key]) + "\n").encode())
        for key in ("invoices", "payments"):
            with (staging / f"{key}.jsonl").open("wb") as handle:
                for record in episode[key]:
                    handle.write((_json(record) + "\n").encode())
        for key, fields in (("ledger", LEDGER_FIELDS), ("fx", FX_FIELDS)):
            with (staging / f"{key}.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                writer.writerows(episode[key])
        for file in sorted(staging.iterdir()):
            file.replace(directory / file.name)


def read_episode(directory):
    directory = Path(directory)
    result = {key: read_json(directory, f"{key}.json") for key in JSON_FILES}
    for key in ("invoices", "payments"):
        result[key] = list(read_json_lines(directory, f"{key}.jsonl"))
    result["ledger"] = [
        {
            field: None if value == "" else int(value) if field.endswith("_minor") else value
            for field, value in record.items()
        }
        for record in read_csv(directory, "ledger.csv")
    ]
    result["fx"] = list(read_csv(directory, "fx.csv"))
    return result
