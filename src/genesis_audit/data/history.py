"""Synthetic accounts receivable, payment allocation, and a balanced journal."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from random import Random
from zoneinfo import ZoneInfo

from genesis_audit.contracts.v1 import SCHEMA_VERSION

EXPONENTS = {"KZT": 2, "USD": 2, "EUR": 2, "JPY": 0}
BASE = "KZT"


@dataclass
class History:
    seed: int
    timezone_name: str
    episode_id: str
    invoices: list = field(default_factory=list)
    payments: list = field(default_factory=list)
    groups: dict = field(default_factory=dict)
    rates: dict = field(default_factory=dict)

    def __post_init__(self):
        self.rng = Random(self.seed)
        self.zone = ZoneInfo(self.timezone_name)
        self.start = datetime(2026, 3, 1, tzinfo=self.zone)
        self.end = datetime(2026, 4, 1, tzinfo=self.zone)

    def identifier(self):
        return f"{self.rng.getrandbits(80):020x}"

    def moment(self, day, hour=9):
        return self.start.replace(day=day, hour=hour)

    def rate(self, moment, currency, value):
        date = moment.astimezone(self.zone).date().isoformat()
        self.rates[(date, currency)] = str(value)

    def convert(self, amount, currency, moment, rate=None):
        if currency == BASE:
            return amount
        day = moment.astimezone(self.zone).date().isoformat()
        rate = Decimal(self.rates[(day, currency)] if rate is None else rate)
        value = Decimal(amount) * rate * Decimal(10) ** (EXPONENTS[BASE] - EXPONENTS[currency])
        return int(value.to_integral_value(rounding=ROUND_HALF_EVEN))

    def row(self, group, moment, entity_id, account, *, debit=0, credit=0,
            invoice_id=None, payment_id=None, allocation=None, source=None):
        allocation_minor, allocation_currency = allocation or (None, None)
        fx_source_minor, fx_source_currency = source or (None, None)
        return {
            "row_id": self.identifier(), "timestamp": moment.isoformat(), "account": account,
            "debit_minor": debit, "credit_minor": credit, "currency": BASE,
            "entity_id": entity_id, "invoice_id": invoice_id, "payment_id": payment_id,
            "posting_group_id": group, "event_at": moment.isoformat(),
            "allocation_minor": allocation_minor, "allocation_currency": allocation_currency,
            "fx_source_minor": fx_source_minor, "fx_source_currency": fx_source_currency,
        }

    def invoice(self, amount, moment, *, entity=None, pieces=None, split_revenue=False):
        entity = entity or self.identifier()
        invoice_id = self.identifier()
        group = self.identifier()
        parts = pieces or (amount,)
        document = {
            "doc_id": self.identifier(), "invoice_id": invoice_id, "entity_id": entity,
            "source_reference": self.identifier(),
            "issued_at": moment.isoformat(),
            "due_at": (moment + timedelta(days=30)).isoformat(),
            "currency": BASE, "total_minor": amount,
            "line_items": [
                {"description": f"Services {i}", "amount_minor": part}
                for i, part in enumerate(parts, 1)
            ],
        }
        self.invoices.append(document)
        self.groups[group] = [
            self.row(group, moment, entity, "receivables", debit=amount, invoice_id=invoice_id),
            *(self.row(group, moment, entity, "revenue", credit=part, invoice_id=invoice_id)
              for part in (parts if split_revenue else (amount,))),
        ]
        return document, group

    def payment(self, amount, moment, invoices, *, currency=BASE, allocations=None, refund=False):
        entity = invoices[0]["entity_id"]
        refs = [invoice["invoice_id"] for invoice in invoices]
        amounts = ([] if refund else [amount]) if allocations is None else allocations
        targets = [
            {"invoice_id": invoice_id, "amount_minor": part, "currency": currency}
            for invoice_id, part in zip(refs, amounts)
        ]
        payment_id = self.identifier()
        group = self.identifier()
        args = (group, moment, entity)
        bank_amount = self.convert(amount, currency, moment)
        kwargs = {"payment_id": payment_id}
        if refund:
            rows = [
                self.row(*args, "cash", credit=bank_amount, **kwargs),
                self.row(*args, "unapplied_cash", debit=bank_amount, invoice_id=refs[0], **kwargs),
            ]
        else:
            rows = [self.row(*args, "cash", debit=bank_amount, **kwargs)]
            for target in targets:
                part = target["amount_minor"]
                rows.append(self.row(
                    *args, "receivables", credit=self.convert(part, currency, moment),
                    invoice_id=target["invoice_id"], allocation=(part, currency),
                    source=(part, currency) if currency != BASE else None, **kwargs,
                ))
            residual = bank_amount - sum(row["credit_minor"] for row in rows)
            if residual:
                rows.append(self.row(
                    *args, "unapplied_cash", credit=residual, invoice_id=refs[0], **kwargs,
                ))
        doc = {
            "doc_id": self.identifier(), "payment_id": payment_id,
            "timestamp": moment.isoformat(), "currency": currency,
            "amount_minor": -amount if refund else amount,
            "invoice_refs": refs, "intended_allocations": targets,
        }
        self.payments.append(doc)
        self.groups[group] = rows
        return doc, group

    def background(self, count, *, detailed=False):
        for index in range(count):
            amount = self.rng.randrange(40_000, 2_500_000)
            date = self.moment(self.rng.randrange(2, 24))
            if detailed and index % 3 == 0:
                count_parts = 2 + index % 5
                part, remainder = divmod(amount, count_parts)
                pieces = [part] * (count_parts - 1) + [part + remainder]
                invoice, _ = self.invoice(amount, date, pieces=pieces, split_revenue=True)
            else:
                invoice, _ = self.invoice(amount, date)
            self.payment(amount, date + timedelta(days=2), [invoice])

    def records(self, issues, changes, generator_version):
        ledger = [row for group in self.groups.values() for row in group]
        for records in (ledger, self.invoices, self.payments):
            self.rng.shuffle(records)
        return {
            "manifest": {
                "schema_version": SCHEMA_VERSION, "episode_id": self.episode_id,
                "base_currency": BASE, "period_start": self.start.isoformat(),
                "period_end": self.end.isoformat(), "timezone": self.timezone_name,
                "row_count": len(ledger), "document_count": len(self.invoices) + len(self.payments),
                "rounding": "HALF_EVEN", "currency_exponents": EXPONENTS,
            },
            "policy": {"period_boundary": "[start,end)", "fx_date_basis": "event_date_local",
                       "rounding": "HALF_EVEN"},
            "ledger": ledger, "invoices": self.invoices, "payments": self.payments,
            "fx": [
                {"date": date, "source_currency": source, "target_currency": BASE, "rate": rate}
                for (date, source), rate in sorted(self.rates.items())
            ],
            "truth": {"issues": issues},
            "provenance": {
                "schema_version": SCHEMA_VERSION, "generator_version": generator_version,
                "seed": self.seed, "changes": changes,
            },
        }


def utc(moment):
    return moment.astimezone(timezone.utc)
