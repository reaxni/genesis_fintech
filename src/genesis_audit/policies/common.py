"""Shared helpers for scripted policies.

Policies run inside the isolated policy container, which only ships the
standard library, genesis_audit.contracts.v1/wire and this package. So no
imports from env, scoring, data or runner are allowed here.

A policy is stateless: everything it knows is rebuilt from the public
`history` ([{"observation", "action"}, ...]) plus the current observation.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple

from genesis_audit.contracts.v1 import (
    ACTION_LIMIT,
    EVIDENCE_LIMIT,
    FINDING_LIMIT,
    PAGE_LIMIT,
    TABLES,
    JSONDict,
)

CRAWL_ORDER = ("ledger", "invoices", "payments", "fx")
assert set(CRAWL_ORDER) == set(TABLES)


def action(name: str, **args: Any) -> JSONDict:
    return {"name": name, "args": args}


def initial_observation(observation: JSONDict, history: List[JSONDict]) -> JSONDict:
    return history[0]["observation"] if history else observation


def episode_id(observation: JSONDict, history: List[JSONDict]) -> str:
    manifest = initial_observation(observation, history).get("manifest") or {}
    value = manifest.get("episode_id")
    return value if isinstance(value, str) else ""


def remaining_steps(observation: JSONDict) -> int:
    value = observation.get("remaining_steps")
    return value if isinstance(value, int) else ACTION_LIMIT


def exchanges(observation: JSONDict, history: List[JSONDict]) -> Iterator[Tuple[JSONDict, JSONDict]]:
    """Yield (action, observation_after_action) pairs of this episode."""
    for i, entry in enumerate(history):
        after = history[i + 1]["observation"] if i + 1 < len(history) else observation
        yield entry["action"], after


class Crawl:
    """Records collected so far through list_records, and what to read next."""

    def __init__(self, observation: JSONDict, history: List[JSONDict]) -> None:
        self.records: Dict[str, List[JSONDict]] = {table: [] for table in CRAWL_ORDER}
        self.next_cursor: Dict[str, Optional[str]] = {}
        self.finished: Dict[str, bool] = {table: False for table in CRAWL_ORDER}
        for act, after in exchanges(observation, history):
            if not isinstance(act, dict) or act.get("name") != "list_records":
                continue
            table = (act.get("args") or {}).get("table")
            if table not in self.records:
                continue
            result = after.get("result") if isinstance(after, dict) else None
            if not isinstance(result, dict) or after.get("error"):
                self.finished[table] = True  # do not loop on an erroring table
                continue
            self.records[table].extend(r for r in result.get("records", []) if isinstance(r, dict))
            cursor = result.get("next_cursor")
            self.next_cursor[table] = cursor
            if cursor is None:
                self.finished[table] = True

    def next_action(self) -> Optional[JSONDict]:
        for table in CRAWL_ORDER:
            if not self.finished[table]:
                return action("list_records", table=table,
                              cursor=self.next_cursor.get(table), limit=PAGE_LIMIT)
        return None

    @property
    def complete(self) -> bool:
        return all(self.finished.values())


def submit(episode: str, findings: List[JSONDict]) -> JSONDict:
    clean = []
    for finding in findings[:FINDING_LIMIT]:
        finding = dict(finding)
        finding["evidence_ids"] = list(dict.fromkeys(finding["evidence_ids"]))[:EVIDENCE_LIMIT]
        finding["explanation"] = finding.get("explanation", "")[:1000]
        clean.append(finding)
    return action("submit", findings={"episode_id": episode, "findings": clean})
