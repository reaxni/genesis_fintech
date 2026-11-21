"""Seeded random exploration baseline.

Every decision comes from the policy-owned `rng` (random.Random) and the
public observations, so the same rng seed and episode give the same
trajectory. It explores with random public actions and stops with a
probability of 1/15 per step (or when the budget is almost gone), then
submits 0-3 random findings built from IDs it has seen.
"""

from __future__ import annotations

from typing import Any, List

from genesis_audit.contracts.v1 import ISSUE_TYPES, TABLES, JSONDict

from .common import action, exchanges, episode_id, initial_observation, remaining_steps, submit

STOP_PROBABILITY = 1 / 15


def _seen(observation: JSONDict, history: List[JSONDict]):
    rows, docs, cursors, amounts = [], [], [], []
    for act, after in exchanges(observation, history):
        result = after.get("result") if isinstance(after, dict) else None
        found = []
        if isinstance(result, dict) and isinstance(result.get("records"), list):
            found = [r for r in result["records"] if isinstance(r, dict)]
            if isinstance(result.get("next_cursor"), str):
                cursors.append((act["args"].get("table"), result["next_cursor"]))
        elif isinstance(result, dict):
            found = [result]
        for record in found:
            if isinstance(record.get("row_id"), str):
                rows.append(record["row_id"])
            if isinstance(record.get("doc_id"), str):
                docs.append(record["doc_id"])
            for key in ("debit_minor", "credit_minor", "amount_minor", "total_minor"):
                if type(record.get(key)) is int and record[key] > 0:
                    amounts.append(record[key])
    return rows, docs, cursors, amounts


def choose(observation: JSONDict, history: List[JSONDict], rng: Any) -> JSONDict:
    rows, docs, cursors, amounts = _seen(observation, history)
    stop = rng.random() < STOP_PROBABILITY
    if stop or remaining_steps(observation) <= 1:
        base = (initial_observation(observation, history).get("manifest") or {}).get("base_currency", "")
        findings = []
        for _ in range(rng.randint(0, 3) if rows or docs else 0):
            pool = rows + docs
            findings.append({
                "type": rng.choice(ISSUE_TYPES),
                "affected_ids": rng.sample(pool, k=min(len(pool), rng.randint(1, 2))),
                "claimed_amount_minor": rng.choice(amounts) if amounts else 0,
                "currency": base,
                "evidence_ids": rng.sample(pool, k=min(len(pool), rng.randint(1, 4))),
                "explanation": "Random guess.",
            })
        return submit(episode_id(observation, history), findings)

    roll = rng.random()
    if roll < 0.35 or not (rows or docs):
        if cursors and rng.random() < 0.5:
            table, cursor = rng.choice(cursors)
            return action("list_records", table=table, cursor=cursor, limit=50)
        return action("list_records", table=rng.choice(TABLES), cursor=None, limit=rng.randint(1, 50))
    if roll < 0.65 and rows:
        return action("get_record", table="ledger", id=rng.choice(rows))
    if roll < 0.9 and docs:
        return action("get_document", doc_id=rng.choice(docs))
    a, b = (rng.choice(amounts) if amounts else rng.randint(0, 999) for _ in range(2))
    return action("calculator", expression=f"{a}{rng.choice('+-*')}{b}")
