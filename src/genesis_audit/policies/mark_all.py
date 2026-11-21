"""Mass-flag baseline: read everything, then flag every candidate entity.

High recall, poor precision. Shows that the reward punishes spraying
findings: each wrong finding is an FP.
"""

from __future__ import annotations

from typing import Any, List

from genesis_audit.contracts.v1 import FINDING_LIMIT, JSONDict

from .common import Crawl, episode_id, initial_observation, remaining_steps, submit


def choose(observation: JSONDict, history: List[JSONDict], rng: Any) -> JSONDict:
    crawl = Crawl(observation, history)
    nxt = crawl.next_action()
    if nxt is not None and remaining_steps(observation) > 1:
        return nxt
    base = (initial_observation(observation, history).get("manifest") or {}).get("base_currency", "")
    findings: List[JSONDict] = []
    invoice_doc = {i.get("invoice_id"): i.get("doc_id") for i in crawl.records["invoices"]}
    for payment in crawl.records["payments"]:
        for intended in payment.get("intended_allocations") or []:
            findings.append({
                "type": "payment_allocation",
                "affected_ids": [payment.get("payment_id"), intended.get("invoice_id")],
                "claimed_amount_minor": intended.get("amount_minor") or 0,
                "currency": intended.get("currency") or base,
                "evidence_ids": [payment.get("doc_id"), invoice_doc.get(intended.get("invoice_id"))],
                "explanation": "Flagged without verification.",
            })
    for invoice in crawl.records["invoices"]:
        findings.append({
            "type": "duplicate_invoice",
            "affected_ids": [invoice.get("invoice_id")],
            "claimed_amount_minor": invoice.get("total_minor") or 0,
            "currency": invoice.get("currency") or base,
            "evidence_ids": [invoice.get("doc_id")],
            "explanation": "Flagged without verification.",
        })
    for row in crawl.records["ledger"]:
        if row.get("fx_source_minor") is not None:
            findings.append({
                "type": "fx_conversion",
                "affected_ids": [row.get("row_id")],
                "claimed_amount_minor": 0,
                "currency": row.get("currency") or base,
                "evidence_ids": [row.get("row_id")],
                "explanation": "Flagged without verification.",
            })
    findings = [
        f for f in findings
        if all(isinstance(i, str) for i in f["affected_ids"] + f["evidence_ids"])
        and type(f["claimed_amount_minor"]) is int
    ]
    return submit(episode_id(observation, history), findings[:FINDING_LIMIT])
