"""Simple reconciliation baseline.

1. Page through ledger, invoices, payments and fx with list_records.
2. Apply four public, documented checks (policy.json + manifest):
   * payment_allocation: intended allocation on the payment vs allocated
     amount in ledger rows of that payment and invoice;
   * duplicate_invoice: a second recognition posting group with the same
     postings for the same invoice;
   * fx_conversion: fx_source_minor * rate(event date) vs the base amount;
   * period_cutoff: recognition timestamp and event date fall on different
     sides of the [period_start, period_end) boundary.
3. Submit once. If the budget runs low, submit what is known.
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import date, datetime
from datetime import timezone as dt_timezone
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from genesis_audit.contracts.v1 import EVIDENCE_LIMIT, JSONDict

from .common import Crawl, episode_id, initial_observation, remaining_steps, submit

ROUNDING = {"HALF_EVEN": ROUND_HALF_EVEN, "HALF_UP": ROUND_HALF_UP}


def choose(observation: JSONDict, history: List[JSONDict], rng: Any) -> JSONDict:
    episode = episode_id(observation, history)
    crawl = Crawl(observation, history)
    nxt = crawl.next_action()
    if nxt is not None and remaining_steps(observation) > 1:
        return nxt
    first = initial_observation(observation, history)
    findings = reconcile(crawl.records, first.get("manifest") or {}, first.get("policy") or {})
    return submit(episode, findings)


# ---------------------------------------------------------------- analysis


def reconcile(records: Dict[str, List[JSONDict]], manifest: JSONDict, policy: JSONDict) -> List[JSONDict]:
    ledger = records.get("ledger", [])
    invoices = records.get("invoices", [])
    payments = records.get("payments", [])
    fx = records.get("fx", [])
    manifest = manifest if isinstance(manifest, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    checks = (
        lambda: payment_allocation(ledger, invoices, payments),
        lambda: duplicate_invoice(ledger, invoices),
        lambda: fx_conversion(ledger, fx, manifest, policy),
        lambda: period_cutoff(ledger, manifest),
    )
    findings: List[JSONDict] = []
    for check in checks:
        # An unexpected public value must never kill the policy process:
        # that would turn the episode into a 60 s timeout with no answer.
        try:
            findings += check()
        except Exception:
            continue
    return findings


def _int(value: Any) -> int:
    return value if type(value) is int else 0


def _cap(ids: List[str]) -> List[str]:
    return list(OrderedDict.fromkeys(i for i in ids if isinstance(i, str)))[:EVIDENCE_LIMIT]


def payment_allocation(ledger, invoices, payments) -> List[JSONDict]:
    invoice_doc = {i.get("invoice_id"): i.get("doc_id") for i in invoices}
    rows_by_payment: Dict[str, List[JSONDict]] = defaultdict(list)
    for row in ledger:
        if row.get("payment_id"):
            rows_by_payment[row["payment_id"]].append(row)
    out = []
    for payment in payments:
        pid = payment.get("payment_id")
        rows = rows_by_payment.get(pid, [])
        for intended in payment.get("intended_allocations") or []:
            inv, cur = intended.get("invoice_id"), intended.get("currency")
            want = _int(intended.get("amount_minor"))
            alloc_rows = [
                r for r in rows
                if r.get("invoice_id") == inv and r.get("allocation_minor") is not None
                and r.get("allocation_currency") == cur
            ]
            applied = sum(_int(r.get("allocation_minor")) for r in alloc_rows)
            shortfall = want - applied
            if shortfall <= 0:
                continue
            # Evidence priority under the 10-id cap: documents, this invoice's
            # allocation rows, then rows of this payment that carry no
            # allocation (e.g. unapplied cash, which proves where the rest
            # went), and only then allocation rows of other invoices.
            alloc_ids = {id(r) for r in alloc_rows}
            unallocated = [
                r for r in rows
                if id(r) not in alloc_ids and r.get("allocation_minor") is None
                and r.get("account") != "cash"
            ]
            other_allocations = [
                r for r in rows
                if id(r) not in alloc_ids and r.get("allocation_minor") is not None
            ]
            evidence = [payment.get("doc_id"), invoice_doc.get(inv)]
            evidence += [r["row_id"] for r in alloc_rows + unallocated + other_allocations]
            out.append({
                "type": "payment_allocation",
                "affected_ids": [pid, inv],
                "claimed_amount_minor": shortfall,
                "currency": cur,
                "evidence_ids": _cap(evidence),
                "explanation": f"Payment {pid} intends {want} minor units for {inv} "
                               f"but only {applied} are allocated in the ledger.",
            })
    return out


def _groups(ledger) -> "OrderedDict[str, List[JSONDict]]":
    groups: "OrderedDict[str, List[JSONDict]]" = OrderedDict()
    for row in ledger:
        groups.setdefault(row.get("posting_group_id") or row.get("row_id"), []).append(row)
    return groups


def _signature(rows) -> Tuple:
    return tuple(sorted(
        (r.get("account"), _int(r.get("debit_minor")), _int(r.get("credit_minor")), r.get("currency"))
        for r in rows
    ))


def duplicate_invoice(ledger, invoices) -> List[JSONDict]:
    invoice_doc = {i.get("invoice_id"): i.get("doc_id") for i in invoices}
    recognition: Dict[str, List[List[JSONDict]]] = defaultdict(list)
    for rows in _groups(ledger).values():
        if any(r.get("payment_id") for r in rows):
            continue
        invoice_ids = {r.get("invoice_id") for r in rows if r.get("invoice_id")}
        if len(invoice_ids) == 1:
            recognition[invoice_ids.pop()].append(rows)
    out = []
    for inv, groups in recognition.items():
        if len(groups) < 2:
            continue
        groups = sorted(groups, key=_earliest)
        original = groups[0]
        for dup in groups[1:]:
            if _signature(dup) != _signature(original):
                continue  # reversal or a different posting is not a duplicate
            amount = sum(_int(r.get("debit_minor")) for r in dup)
            evidence = [invoice_doc.get(inv)] + [r["row_id"] for r in original + dup]
            out.append({
                "type": "duplicate_invoice",
                "affected_ids": [inv] + [r["row_id"] for r in dup],
                "claimed_amount_minor": amount,
                "currency": dup[0].get("currency"),
                "evidence_ids": _cap(evidence),
                "explanation": f"Invoice {inv} is recognised twice with identical postings.",
            })
    return out


def _parse(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


_FAR_FUTURE = datetime.max.replace(tzinfo=dt_timezone.utc)


def _earliest(rows: List[JSONDict]) -> Tuple[datetime, str]:
    """Sort key: real instant (offsets compared correctly), then group id."""
    moments = [m for m in (_parse(r.get("timestamp")) for r in rows) if m is not None]
    moments = [m for m in moments if m.tzinfo is not None]
    return (min(moments) if moments else _FAR_FUTURE, str(rows[0].get("posting_group_id") or ""))


def _local_date(ts: Any, timezone: Any) -> Optional[date]:
    moment = _parse(ts)
    if moment is None:
        return None
    try:
        from zoneinfo import ZoneInfo
        return moment.astimezone(ZoneInfo(timezone)).date()
    except Exception:  # tz database may be absent in a slim image
        return moment.date()  # timestamps carry the local offset


def fx_conversion(ledger, fx, manifest, policy) -> List[JSONDict]:
    exponents = manifest.get("currency_exponents")
    exponents = exponents if isinstance(exponents, dict) else {}
    timezone = manifest.get("timezone")
    rounding_name = policy.get("rounding") or manifest.get("rounding")
    rounding = ROUNDING.get(rounding_name if isinstance(rounding_name, str) else "", ROUND_HALF_EVEN)
    basis = policy.get("fx_date_basis")
    # v1 basis is "event_date_local"; anything else that names the booking
    # timestamp switches to it, and a missing/odd value keeps the v1 default.
    use_event = not (isinstance(basis, str) and basis.startswith(("booking", "timestamp", "recogn")))
    rates = {}
    for rec in fx:
        try:
            rates[(rec.get("date"), rec.get("source_currency"), rec.get("target_currency"))] = Decimal(rec.get("rate"))
        except (InvalidOperation, TypeError):
            continue
    out = []
    for row in ledger:
        source_minor, source_cur = row.get("fx_source_minor"), row.get("fx_source_currency")
        if type(source_minor) is not int or not source_cur:
            continue
        target_cur = row.get("currency")
        day = _local_date(row.get("event_at") if use_event else row.get("timestamp"), timezone)
        if day is None:
            continue
        key = (day.isoformat(), source_cur, target_cur)
        rate = rates.get(key)
        if rate is None:
            continue
        e_src, e_tgt = exponents.get(source_cur), exponents.get(target_cur)
        if type(e_src) is not int or type(e_tgt) is not int:
            continue  # unknown minor-unit exponent: never assume 2 digits
        expected = (Decimal(source_minor) * rate * (Decimal(10) ** (e_tgt - e_src))).quantize(
            Decimal(1), rounding=rounding)
        actual = _int(row.get("debit_minor")) or _int(row.get("credit_minor"))
        diff = abs(int(expected) - actual)
        if diff == 0:
            continue
        out.append({
            "type": "fx_conversion",
            "affected_ids": [row["row_id"]],
            "claimed_amount_minor": diff,
            "currency": target_cur,
            "evidence_ids": [row["row_id"], ":".join(key)],
            "explanation": f"{source_minor} {source_cur} minor at {rate} should be {expected} "
                           f"{target_cur} minor, ledger has {actual}.",
        })
    return out


def period_cutoff(ledger, manifest) -> List[JSONDict]:
    start, end = _parse(manifest.get("period_start")), _parse(manifest.get("period_end"))
    if start is None or end is None:
        return []

    def inside(ts: Any) -> Optional[bool]:
        moment = _parse(ts)
        return None if moment is None else start <= moment < end

    out = []
    for gid, rows in _groups(ledger).items():
        # Only recognition groups: a payment's bank value date may legally
        # differ from its booking date and is not a cutoff error.
        if any(r.get("payment_id") for r in rows):
            continue
        booked = {inside(r.get("timestamp")) for r in rows}
        happened = {inside(r.get("event_at")) for r in rows}
        if None in booked | happened or len(booked) != 1 or len(happened) != 1:
            continue
        if booked == happened:
            continue
        amount = sum(_int(r.get("debit_minor")) for r in rows)
        ids = [r["row_id"] for r in rows]
        out.append({
            "type": "period_cutoff",
            "affected_ids": ids,
            "claimed_amount_minor": amount,
            "currency": rows[0].get("currency"),
            "evidence_ids": _cap(ids),
            "explanation": f"Posting group {gid} is recognised on the other side of the period "
                           f"boundary from its event date.",
        })
    return out
