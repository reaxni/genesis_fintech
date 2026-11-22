"""Apply causes to a clean journal and record reversible group replacements."""

from datetime import datetime, timedelta

from genesis_audit.contracts.v1 import fx_evidence_id

from .history import BASE


class Mutations:
    def __init__(self, history):
        self.history = history
        self.issues = []
        self.changes = []
        self._pending = []

    def schedule(self, operation, *args):
        self._pending.append((operation, args))

    def commit(self):
        for operation, args in self._pending:
            operation(*args)
        self._pending.clear()

    def apply(self, kind, group, replacement, affected, amount, currency, evidence):
        issue_id = self.history.identifier()
        self.changes.append({
            "issue_id": issue_id, "rule": kind, "posting_group_id": group,
            "before": self.history.groups.get(group, []), "after": replacement,
        })
        self.history.groups[group] = replacement
        self.issues.append({
            "issue_id": issue_id, "type": kind, "affected_ids": affected,
            "expected_amount_minor": amount, "currency": currency,
            "evidence_sets": [evidence],
        })

    def duplicate(self, invoice, group, split_revenue=False):
        original = self.history.groups[group]
        second = self.history.identifier()
        repeated = []
        for row in original:
            booked = datetime.fromisoformat(row["timestamp"]) + timedelta(days=1)
            credits = (row["credit_minor"],)
            if split_revenue and row["account"] == "revenue":
                credits = tuple(item["amount_minor"] for item in invoice["line_items"])
            repeated.extend(dict(row, row_id=self.history.identifier(), posting_group_id=second,
                                 credit_minor=credit, timestamp=booked.isoformat())
                            for credit in credits)
        repeated_ids = [row["row_id"] for row in repeated]
        self.apply(
            "duplicate_invoice", second, repeated, [invoice["invoice_id"], *repeated_ids],
            invoice["total_minor"], BASE,
            [invoice["doc_id"], *[row["row_id"] for row in original], *repeated_ids],
        )

    def _with_fx_adjustment(self, rows, group, moment, entity, payment_id):
        rows = [row for row in rows if row["account"] not in ("fx_gain", "fx_loss")]
        difference = sum(row["debit_minor"] - row["credit_minor"] for row in rows)
        if difference:
            rows.append(self.history.fx_adjustment(group, moment, entity, payment_id, difference))
        return rows

    def allocation(self, payment, group, invoice, applied):
        before = self.history.groups[group]
        invoice_id = invoice["invoice_id"]
        target = next(row for row in before if row["invoice_id"] == invoice_id
                      and row["allocation_minor"] is not None)
        previous = target["allocation_minor"]
        currency = payment["currency"]
        moment = datetime.fromisoformat(payment["timestamp"])
        carrying = self.history.diverse and currency != BASE
        basis = datetime.fromisoformat(invoice["issued_at"]) if carrying else moment
        converted = self.history.convert(applied, currency, basis)
        residual = self.history.convert(previous, currency, moment) - self.history.convert(applied, currency, moment)
        rows = [row for row in before if row is not target]
        if applied:
            updated = dict(target, allocation_minor=applied, credit_minor=converted)
            if updated["fx_source_minor"] is not None:
                updated["fx_source_minor"] = applied
            rows.append(updated)
        unapplied = self.history.row(
            group, moment, target["entity_id"], "unapplied_cash", credit=residual,
            invoice_id=invoice_id, payment_id=payment["payment_id"],
        )
        rows.append(unapplied)
        if carrying:
            rows = self._with_fx_adjustment(rows, group, moment, target["entity_id"], payment["payment_id"])
        self.apply(
            "payment_allocation", group, rows, [payment["payment_id"], invoice_id],
            previous - applied, currency, [payment["doc_id"], invoice["doc_id"],
                                          *[row["row_id"] for row in rows
                                            if row["account"] in ("receivables", "unapplied_cash")]],
        )

    def fx(self, payment, group, wrong_rate):
        before = self.history.groups[group]
        target = next(row for row in before if row["fx_source_minor"] is not None)
        currency = target["fx_source_currency"]
        moment = datetime.fromisoformat(target["event_at"])
        incorrect = self.history.convert(target["fx_source_minor"], currency, moment, wrong_rate)
        amount_field = "debit_minor" if target["account"] == "cash" else "credit_minor"
        difference = target[amount_field] - incorrect
        updated = dict(target, **{amount_field: incorrect})
        rows = [updated if row is target else row for row in before]
        rows = self._with_fx_adjustment(rows, group, moment, target["entity_id"], payment["payment_id"])
        evidence = fx_evidence_id({
            "date": moment.astimezone(self.history.zone).date().isoformat(),
            "source_currency": currency, "target_currency": BASE,
        })
        self.apply(
            "fx_conversion", group, rows,
            [target["row_id"]], abs(difference), BASE, [target["row_id"], evidence],
        )

    def cutoff(self, invoice, group, booked):
        before = self.history.groups[group]
        after = [dict(row, timestamp=booked.isoformat()) for row in before]
        row_ids = [row["row_id"] for row in after]
        self.apply("period_cutoff", group, after, row_ids, invoice["total_minor"], BASE, row_ids)
