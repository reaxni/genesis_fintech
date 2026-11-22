"""Recompute the public accounting facts, then audit truth and causal history."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from zoneinfo import ZoneInfo

from genesis_audit.contracts.v1 import EVIDENCE_LIMIT, SCHEMA_VERSION, fx_evidence_id
from genesis_audit.data.export import read_episode


@dataclass(frozen=True)
class Fact:
    kind: str
    affected: frozenset
    currency: str
    amount: int
    evidence: frozenset

    @property
    def key(self):
        return self.kind, self.affected


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _unique(records, key):
    index = {record[key]: record for record in records}
    _require(len(records) == len(index), f"duplicate {key}")
    return index


def _timestamp(value):
    moment = datetime.fromisoformat(value)
    _require(moment.utcoffset() is not None, "timestamp lacks a UTC offset")
    return moment


def _fact(kind, affected, currency, amount, evidence):
    return Fact(kind, frozenset(affected), currency, amount, frozenset(evidence))


class Accounting:
    def __init__(self, episode):
        manifest = episode["manifest"]
        self.base = manifest["base_currency"]
        self.exponents = manifest["currency_exponents"]
        self.zone = ZoneInfo(manifest["timezone"])
        self.start, self.end = (_timestamp(manifest[key]).astimezone(timezone.utc)
                               for key in ("period_start", "period_end"))
        policy = episode["policy"]
        _require(manifest["schema_version"] == SCHEMA_VERSION and self.start < self.end,
                 "unsupported version or invalid period")
        _require((policy["period_boundary"], policy["fx_date_basis"], policy["rounding"])
                 == ("[start,end)", "event_date_local", "HALF_EVEN"), "unsupported policy")
        _require(manifest["rounding"] == policy["rounding"], "rounding differs from policy")
        settlement_basis = policy.get("fx_settlement_basis", "event_date_local")
        _require(settlement_basis in ("event_date_local", "invoice_date_local"), "unsupported FX settlement basis")
        self.carrying_value = settlement_basis == "invoice_date_local"
        _require(self.base in self.exponents and all(type(n) is int and 0 <= n <= 9
                 for n in self.exponents.values()), "invalid currency exponents")
        self.invoices = _unique(episode["invoices"], "invoice_id")
        self.payments = _unique(episode["payments"], "payment_id")
        documents = [*episode["invoices"], *episode["payments"]]
        self.document_ids = set(_unique(documents, "doc_id"))
        self.quotes = {}
        for record in episode["fx"]:
            key = fx_evidence_id(record)
            quote = Decimal(record["rate"])
            date.fromisoformat(record["date"])
            _require(key not in self.quotes and quote.is_finite() and quote > 0
                     and record["source_currency"] in self.exponents
                     and record["target_currency"] == self.base, "invalid or duplicate FX quote")
            self.quotes[key] = quote
        _require(manifest["row_count"] == len(episode["ledger"])
                 and manifest["document_count"] == len(documents), "manifest counts differ")
        self._check_documents()

    def _check_documents(self):
        _unique(list(self.invoices.values()), "source_reference")
        for invoice in self.invoices.values():
            total = invoice["total_minor"]
            items = [line["amount_minor"] for line in invoice["line_items"]]
            _require(type(total) is int and total > 0 and invoice["currency"] == self.base
                     and all(type(item) is int and item > 0 for item in items)
                     and total == sum(items), "invoice line items do not reconcile")
            _require(_timestamp(invoice["issued_at"]) <= _timestamp(invoice["due_at"]),
                     "invoice is due before it was issued")
        authorized_surplus = defaultdict(int)
        totals = defaultdict(int)
        payments = sorted(self.payments.values(), key=lambda p: _timestamp(p["timestamp"]))
        for payment in payments:
            amount, refs = payment["amount_minor"], payment["invoice_refs"]
            moment = _timestamp(payment["timestamp"])
            _require(type(amount) is int and amount != 0 and refs and len(refs) == len(set(refs))
                     and all(ref in self.invoices for ref in refs)
                     and payment["currency"] in self.exponents, "invalid payment document")
            _require(len({self.invoices[ref]["entity_id"] for ref in refs}) == 1,
                     "payment has multiple counterparties")
            _require(all(_timestamp(self.invoices[ref]["issued_at"]) <= moment for ref in refs),
                     "payment precedes its source invoice")
            intended = _unique(payment["intended_allocations"], "invoice_id")
            _require(all(i in refs and a["currency"] == payment["currency"]
                     and type(a["amount_minor"]) is int and a["amount_minor"] > 0
                     for i, a in intended.items()), "invalid intended allocation")
            intended_total = sum(a["amount_minor"] for a in intended.values())
            if amount < 0:
                _require(len(refs) == 1 and not intended, "refund needs one original invoice")
                authorized_surplus[refs[0]] -= self.convert(-amount, payment["currency"], moment)[0]
                _require(authorized_surplus[refs[0]] >= 0,
                         "refund exceeds earlier authorized surplus for the linked invoice")
            else:
                _require(intended_total <= amount, "intended allocation exceeds payment")
                authorized_surplus[refs[0]] += self.convert(amount - intended_total,
                                                           payment["currency"], moment)[0]
                for ref, allocation in intended.items():
                    basis = _timestamp(self.invoices[ref]["issued_at"]) if self.carrying_value else moment
                    totals[ref] += self.convert(allocation["amount_minor"], payment["currency"], basis)[0]
        _require(all(amount <= self.invoices[ref]["total_minor"] for ref, amount in totals.items()),
                 "intended allocations exceed the invoice total")

    def convert(self, amount, source, moment):
        if source == self.base or amount == 0:
            return amount, None
        local_day = moment.astimezone(self.zone).date().isoformat()
        key = f"{local_day}:{source}:{self.base}"
        _require(key in self.quotes, f"missing public FX rate {key}")
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            value = Decimal(amount).scaleb(self.exponents[self.base] - self.exponents[source])
            return int((value * self.quotes[key]).quantize(Decimal(1))), key

    def inspect(self, ledger):
        rows = _unique(ledger, "row_id")
        _require(len(self.document_ids | self.quotes.keys() | rows.keys())
                 == len(self.document_ids) + len(self.quotes) + len(rows), "evidence IDs collide")
        groups = defaultdict(list)
        for row in ledger:
            groups[row["posting_group_id"]].append(row)
        recognized = defaultdict(list)
        payments = {}
        facts = []
        for group in groups.values():
            booked, event = self._check_group(group)
            payment_id = group[0]["payment_id"]
            if payment_id is None:
                invoice = self._recognition(group, event)
                recognized[invoice["invoice_id"]].append((booked, group))
                if (self.start <= booked < self.end) != (self.start <= event < self.end):
                    ids = [row["row_id"] for row in group]
                    facts.append(_fact("period_cutoff", ids, self.base, invoice["total_minor"], ids))
            else:
                _require(payment_id in self.payments and payment_id not in payments,
                         "payment posting has no unique source document")
                payment = self.payments[payment_id]
                payments[payment_id] = group
                facts.extend(self._payment(payment, group, event))
        _require(recognized.keys() == self.invoices.keys() and payments.keys() == self.payments.keys(),
                 "groups and source documents differ")
        for invoice_id, entries in recognized.items():
            entries.sort(key=lambda entry: entry[0])
            original_time, original = entries[0]
            invoice = self.invoices[invoice_id]
            for booked, duplicated in entries[1:]:
                _require(booked > original_time, "original and duplicate have ambiguous booking dates")
                ids = [row["row_id"] for row in duplicated]
                evidence = [invoice["doc_id"], *[row["row_id"] for row in original], *ids]
                facts.append(_fact("duplicate_invoice", [invoice_id, *ids], self.base,
                                   invoice["total_minor"], evidence))
        result = {fact.key: fact for fact in facts}
        _require(len(result) == len(facts), "ambiguous issues")
        return result

    def _check_group(self, group):
        booked = {_timestamp(row["timestamp"]).astimezone(timezone.utc) for row in group}
        events = {_timestamp(row["event_at"]).astimezone(timezone.utc) for row in group}
        _require(len(booked) == len(events) == 1
                 and len({row["payment_id"] for row in group}) == 1
                 and len({row["entity_id"] for row in group}) == 1, "inconsistent posting group")
        total = 0
        for row in group:
            debit, credit = row["debit_minor"], row["credit_minor"]
            _require(type(debit) is int and type(credit) is int and debit >= 0 and credit >= 0
                     and bool(debit) != bool(credit) and row["currency"] == self.base,
                     "invalid posting amount")
            _require(row["invoice_id"] is None or row["invoice_id"] in self.invoices,
                     "posting has a missing invoice")
            for minor, currency in (("allocation_minor", "allocation_currency"),
                                    ("fx_source_minor", "fx_source_currency")):
                amount, code = row[minor], row[currency]
                _require((amount is None and code is None) or
                         (type(amount) is int and amount > 0 and code in self.exponents),
                         "invalid source or allocation amount")
            total += debit - credit
        _require(total == 0, "debits and credits do not balance")
        return booked.pop(), events.pop()

    def _recognition(self, group, event):
        invoice_ids = {row["invoice_id"] for row in group}
        _require(len(invoice_ids) == 1 and invoice_ids <= self.invoices.keys(),
                 "recognition group must address one invoice")
        invoice = self.invoices[invoice_ids.pop()]
        amount = invoice["total_minor"]
        receivables = [row for row in group if row["account"] == "receivables"]
        revenue = [row for row in group if row["account"] == "revenue"]
        _require(len(receivables) == 1 and receivables[0]["debit_minor"] == amount
                 and len(group) == len(receivables) + len(revenue)
                 and all(row["credit_minor"] > 0 for row in revenue)
                 and sum(row["credit_minor"] for row in revenue) == amount
                 and event == _timestamp(invoice["issued_at"])
                 and all(r["entity_id"] == invoice["entity_id"] and r["allocation_minor"] is None
                         and r["fx_source_minor"] is None for r in group),
                 "recognition does not reflect the source invoice")
        return invoice

    def _payment(self, payment, group, event):
        payment_id, source = payment["payment_id"], payment["currency"]
        amount, refs = payment["amount_minor"], payment["invoice_refs"]
        _require(event == _timestamp(payment["timestamp"]), "payment event differs from document")
        entity = self.invoices[refs[0]]["entity_id"]
        _require(all(r["entity_id"] == entity and (r["invoice_id"] is None or r["invoice_id"] in refs)
                     for r in group), "payment row has the wrong invoice or counterparty")
        cash = [r for r in group if r["account"] == "cash"]
        expected, cash_quote = self.convert(abs(amount), source, event)
        _require(len(cash) == 1, "payment needs one cash posting")
        cash, = cash
        cash_booked = cash["debit_minor"] - cash["credit_minor"]
        carrying = self.carrying_value and source != self.base and amount > 0
        source_amount = cash["fx_source_minor"], cash["fx_source_currency"]
        if carrying:
            _require(source_amount == (amount, source) and cash_booked > 0,
                     "cash conversion lacks its original currency and amount")
        else:
            _require(source_amount == (None, None) and cash_booked == (expected if amount > 0 else -expected),
                     "cash does not reconcile to payment")
        unapplied = [row for row in group if row["account"] == "unapplied_cash"]
        allocated = [row for row in group if row["account"] == "receivables"]
        adjustments = [row for row in group if row["account"] in ("fx_gain", "fx_loss")]
        _require(1 + len(unapplied) + len(allocated) + len(adjustments) == len(group)
                 and cash["allocation_minor"] is None
                 and all(row["allocation_minor"] is None and row["fx_source_minor"] is None
                         for row in unapplied + adjustments),
                 "unexpected account or allocation on payment")
        intended = {a["invoice_id"]: a["amount_minor"] for a in payment["intended_allocations"]}
        if amount < 0:
            _require(len(unapplied) == 1 and unapplied[0]["debit_minor"] == expected
                     and unapplied[0]["invoice_id"] == refs[0] and not allocated and not adjustments,
                     "refund does not clear the source invoice surplus")
            return []
        applied = defaultdict(int)
        allocated_at_rate = 0
        facts = []
        if carrying and cash_booked != expected:
            facts.append(_fact("fx_conversion", [cash["row_id"]], self.base,
                               abs(expected - cash_booked), [cash["row_id"], cash_quote]))
        for row in allocated:
            invoice_id = row["invoice_id"]
            chunk = row["allocation_minor"]
            _require(invoice_id in intended and row["allocation_currency"] == source
                     and type(chunk) is int and chunk > 0 and row["credit_minor"] > 0,
                     "journal allocation lacks an intended invoice")
            applied[invoice_id] += chunk
            correct, quote = self.convert(chunk, source, event)
            allocated_at_rate += correct
            if carrying:
                issued = _timestamp(self.invoices[invoice_id]["issued_at"])
                original, _ = self.convert(chunk, source, issued)
                _require(row["credit_minor"] == original and row["fx_source_minor"] is None,
                         "receivable does not clear at its historical carrying value")
            elif source != self.base:
                _require((row["fx_source_minor"], row["fx_source_currency"]) == (chunk, source),
                         "FX source does not match the allocation")
                delta = abs(correct - row["credit_minor"])
                if delta:
                    facts.append(_fact("fx_conversion", [row["row_id"]], self.base, delta,
                                       [row["row_id"], quote]))
            else:
                _require(row["credit_minor"] == chunk and row["fx_source_minor"] is None,
                         "domestic allocation differs from posting")
        _require(all(amount <= intended[ref] for ref, amount in applied.items())
                 and all(row["credit_minor"] > 0 and row["invoice_id"] in refs for row in unapplied)
                 and sum(row["credit_minor"] for row in unapplied) == expected - allocated_at_rate,
                 "unapplied cash does not reconcile to the actual allocation")
        delta = cash_booked - sum(row["credit_minor"] for row in allocated + unapplied)
        _require(sum(r["credit_minor"] - r["debit_minor"] for r in adjustments) == delta
                 and all((r["account"], bool(r["credit_minor"])) in
                         (("fx_gain", True), ("fx_loss", False)) for r in adjustments),
                 "FX balancing accounts do not reconcile")
        visible_rows = [r["row_id"] for r in allocated + unapplied]
        for ref, planned in intended.items():
            missing = planned - applied[ref]
            if missing:
                facts.append(_fact("payment_allocation", [payment_id, ref], source, missing,
                                   [payment["doc_id"], self.invoices[ref]["doc_id"], *visible_rows]))
        return facts


def verify_episode(directory):
    """Raise ValueError on accounting, evidence, or provenance disagreement."""
    episode = read_episode(directory)
    accounting = Accounting(episode)
    observed = accounting.inspect(episode["ledger"])
    evidence = accounting.document_ids | accounting.quotes.keys() | {
        row["row_id"] for row in episode["ledger"]
    }
    issues = _unique(episode["truth"]["issues"], "issue_id")
    declared = {}
    for issue in issues.values():
        key = issue["type"], frozenset(issue["affected_ids"])
        fact = observed.get(key)
        _require(fact is not None and key not in declared and type(issue["expected_amount_minor"]) is int
                 and (fact.amount, fact.currency) == (issue["expected_amount_minor"], issue["currency"]),
                 "truth does not match public accounting")
        _require(issue["evidence_sets"] and all(
            0 < len(proof) <= EVIDENCE_LIMIT and fact.evidence <= set(proof) <= evidence
            for proof in issue["evidence_sets"]
        ), "truth lacks a complete publicly addressable proof")
        declared[key] = fact
    _require(observed == declared, "truth does not cover every public issue")
    _verify_history(episode, accounting, issues, observed)
    return tuple(observed.values())


def _verify_history(episode, accounting, issues, observed):
    provenance = episode["provenance"]
    changes = provenance["changes"]
    _require(provenance["schema_version"] == SCHEMA_VERSION and
             _unique(changes, "issue_id").keys() == issues.keys(), "provenance does not cover truth")
    journal = _unique(episode["ledger"], "row_id")
    expected = observed.copy()
    for change in reversed(changes):
        issue = issues[change["issue_id"]]
        group = change["posting_group_id"]
        before, after = (_unique(change[state], "row_id") for state in ("before", "after"))
        _require(change["rule"] == issue["type"] and after and
                 all(row["posting_group_id"] == group for row in before.values()) and
                 after == {uid: row for uid, row in journal.items() if row["posting_group_id"] == group},
                 "causal patch differs from the journal")
        for uid in after:
            del journal[uid]
        _require(not (journal.keys() & before.keys()), "causal patch collides with another group")
        journal.update(before)
        del expected[(issue["type"], frozenset(issue["affected_ids"]))]
        _require(accounting.inspect(list(journal.values())) == expected,
                 "reverting this cause must remove exactly its declared issue")
