"""Verify the shared G0 episode without importing generator, environment, or scorer."""

import unittest
from collections import defaultdict
from pathlib import Path

from genesis_audit.contracts.files import public_evidence_ids, read_csv, read_json, read_json_lines
from genesis_audit.contracts.v1 import SCHEMA_VERSION

FIXTURE = Path(__file__).resolve().parents[1] / "integration/fixtures/g0_partial_payment"


class ContractFixtureTest(unittest.TestCase):
    def test_partial_payment_and_evidence(self):
        manifest = read_json(FIXTURE, "manifest.json")
        self.assertEqual(manifest["schema_version"], SCHEMA_VERSION)
        rows = list(read_csv(FIXTURE, "ledger.csv"))
        payments = list(read_json_lines(FIXTURE, "payments.jsonl"))
        invoices = list(read_json_lines(FIXTURE, "invoices.jsonl"))
        self.assertEqual(manifest["row_count"], len(rows))
        self.assertEqual(manifest["document_count"], len(payments) + len(invoices))

        net = defaultdict(int)
        for row in rows:
            net[row["posting_group_id"], row["currency"]] += (
                int(row["debit_minor"]) - int(row["credit_minor"])
            )
        self.assertTrue(all(amount == 0 for amount in net.values()))
        payment = next(row for row in payments if row["payment_id"] == "pay-02")
        intended = payment["intended_allocations"][0]["amount_minor"]
        applied = sum(
            int(row["allocation_minor"])
            for row in rows
            if row["payment_id"] == "pay-02" and row["allocation_minor"]
        )
        expected = read_json(FIXTURE, "expected_findings.json")
        self.assertEqual(expected["episode_id"], manifest["episode_id"])
        finding = expected["findings"][0]
        self.assertEqual(intended - applied, 5_000_000)
        self.assertEqual(finding["claimed_amount_minor"], intended - applied)
        self.assertTrue(set(finding["evidence_ids"]) <= public_evidence_ids(FIXTURE))
