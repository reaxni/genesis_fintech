"""Role B scripted policies and finance reconciliation rules.

Run:  PYTHONPATH=src python -m unittest discover -s tests/contract -v
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from random import Random

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from genesis_audit.contracts.v1 import ACTION_LIMIT  # noqa: E402
from genesis_audit.env import AuditEnv  # noqa: E402
from genesis_audit.policies import DEMO_PLANS, POLICIES, choose_action  # noqa: E402
from genesis_audit.policies.reconciliation import DEMO_PAGE_BUDGET, reconcile  # noqa: E402

FIXTURE = ROOT / "tests/integration/fixtures/g0_partial_payment"
POLICY_DIR = ROOT / "src/genesis_audit/policies"


def run_policy(name, episode=FIXTURE, rng_seed=0):
    env = AuditEnv(score_episode=None)
    observation, _ = env.reset(0, {"mode": "closed", "episode_dir": str(episode)})
    rng, history, trajectory = Random(rng_seed), [], []
    terminated = truncated = False
    while not (terminated or truncated):
        act = choose_action(name, deepcopy(observation), deepcopy(history), rng=rng)
        json.dumps(act, allow_nan=False)  # must be JSON for the IPC channel
        history.append({"observation": observation, "action": act})
        observation, reward, terminated, truncated, _ = env.step(act)
        trajectory.append((act, observation))
    return env, trajectory


def matches(finding, issue):
    """Independent re-statement of the published match rule."""
    return (
        finding["type"] == issue["type"]
        and set(finding["affected_ids"]) == set(issue["affected_ids"])
        and finding["currency"] == issue["currency"]
        and finding["claimed_amount_minor"] == issue["expected_amount_minor"]
        and any(set(proof) <= set(finding["evidence_ids"]) for proof in issue["evidence_sets"])
    )


class PolicyBoundary(unittest.TestCase):
    ALLOWED = {"__future__", "typing", "collections", "datetime", "decimal", "functools", "random",
               "zoneinfo", "math", "genesis_audit.contracts.v1"}

    def test_policies_import_only_stdlib_and_contract(self):
        for path in POLICY_DIR.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import inside the package
                        continue
                    names = [node.module]
                else:
                    continue
                for name in names:
                    with self.subTest(file=path.name, module=name):
                        self.assertIn(name, self.ALLOWED)

    def test_unknown_policy(self):
        with self.assertRaises(ValueError):
            choose_action("oracle", {}, [], rng=Random(0))

    def test_registry_names(self):
        self.assertEqual(set(POLICIES), {"random", "reconciliation", "reconciliation_aggregate", "mark_all"})


class PolicyRuns(unittest.TestCase):
    def test_every_policy_reaches_a_valid_submit(self):
        for name in POLICIES:
            with self.subTest(policy=name):
                env, trajectory = run_policy(name)
                last_action, last_obs = trajectory[-1]
                self.assertEqual(last_action["name"], "submit")
                self.assertEqual(last_obs["result"], {"accepted": True})
                self.assertLessEqual(len(trajectory), ACTION_LIMIT)
                self.assertTrue(env.outcome()["terminated"])

    def test_reconciliation_finds_the_fixture_issue_exactly(self):
        env, trajectory = run_policy("reconciliation")
        truth = json.loads((FIXTURE / "truth.json").read_text(encoding="utf-8"))
        findings = env.outcome()["findings"]["findings"]
        self.assertEqual(len(findings), 1)
        self.assertTrue(matches(findings[0], truth["issues"][0]))
        self.assertEqual(env.outcome()["inspection_steps"], 4)  # expected_score: 0.992

    def test_aggregate_skips_fx_only_when_unused(self):
        domestic, domestic_trace = run_policy("reconciliation_aggregate")
        baseline, _ = run_policy("reconciliation")
        self.assertEqual(domestic.outcome()["findings"], baseline.outcome()["findings"])
        self.assertEqual(domestic.outcome()["inspection_steps"], baseline.outcome()["inspection_steps"] - 1)
        self.assertFalse(any(act["args"].get("table") == "fx" for act, _ in domestic_trace))

        foreign_episode = ROOT / "episodes/train/episode-08"
        foreign, foreign_trace = run_policy("reconciliation_aggregate", foreign_episode)
        self.assertTrue(any(act["args"].get("table") == "fx" for act, _ in foreign_trace))
        self.assertTrue(any(f["type"] == "fx_conversion" for f in foreign.outcome()["findings"]["findings"]))

    def test_aggregate_finds_split_duplicate_with_all_evidence(self):
        episode = ROOT / "episodes/train/episode-04"
        improved, _ = run_policy("reconciliation_aggregate", episode)
        baseline, _ = run_policy("reconciliation", episode)
        issues = json.loads((episode / "truth.json").read_text(encoding="utf-8"))["issues"]
        findings = improved.outcome()["findings"]["findings"]
        self.assertEqual(len(findings), len(issues))
        self.assertTrue(all(any(matches(finding, issue) for finding in findings) for issue in issues))
        self.assertEqual(len(baseline.outcome()["findings"]["findings"]), len(issues) - 1)
        self.assertEqual(improved.outcome()["inspection_steps"], baseline.outcome()["inspection_steps"])

    def test_budgeted_plans_cap_reads_and_submit_only_proven_findings(self):
        episode = ROOT / "episodes/dev/episode-10"
        issues = json.loads((episode / "truth.json").read_text(encoding="utf-8"))["issues"]
        for plan in DEMO_PLANS:
            with self.subTest(plan=plan):
                env, trajectory = run_policy(plan, episode)
                self.assertLessEqual(len(trajectory) - 1, DEMO_PAGE_BUDGET)
                self.assertEqual(trajectory[-1][0]["name"], "submit")
                self.assertTrue(env.outcome()["terminated"])
                for finding in env.outcome()["findings"]["findings"]:
                    self.assertTrue(any(matches(finding, issue) for issue in issues))

    def test_budgeted_plan_ignores_ledger_after_page_error(self):
        first = {"manifest": {"episode_id": "sample", "period_start": "2026-03-01T00:00:00+05:00",
                              "period_end": "2026-04-01T00:00:00+05:00"}, "policy": {}}
        row = {"row_id": "r1", "timestamp": "2026-02-28T11:00:00+05:00",
               "event_at": "2026-03-01T11:00:00+05:00", "currency": "KZT", "debit_minor": 300}
        page = {"result": {"records": [row], "next_cursor": "ledger:50"}}
        history = [
            {"observation": first, "action": {"name": "list_records", "args": {"table": "ledger", "cursor": None}}},
            {"observation": page, "action": {"name": "list_records", "args": {"table": "ledger", "cursor": "ledger:50"}}},
        ]
        final = {"result": None, "error": {"code": "invalid_cursor"}, "remaining_steps": 1}
        action = choose_action("ledger_docs", final, history, rng=Random(0))
        self.assertEqual(action["name"], "submit")
        self.assertEqual(action["args"]["findings"]["findings"], [])

    def test_reconciliation_is_silent_on_clean_twin(self):
        from test_env_contract import make_clean_episode  # same folder
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            clean = make_clean_episode(Path(tmp) / "clean")
            env, _ = run_policy("reconciliation", clean)
            self.assertEqual(env.outcome()["findings"]["findings"], [])

    def test_random_is_reproducible_and_seed_sensitive(self):
        _, a = run_policy("random", rng_seed=0)
        _, b = run_policy("random", rng_seed=0)
        _, c = run_policy("random", rng_seed=1)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_mark_all_flags_more_than_truth(self):
        env, _ = run_policy("mark_all")
        self.assertGreater(len(env.outcome()["findings"]["findings"]), 1)


MANIFEST = {
    "episode_id": "unit", "base_currency": "KZT", "timezone": "Asia/Almaty",
    "period_start": "2026-03-01T00:00:00+05:00", "period_end": "2026-04-01T00:00:00+05:00",
    "rounding": "HALF_EVEN", "currency_exponents": {"KZT": 2, "USD": 2},
}
POLICY = {"period_boundary": "[start,end)", "fx_date_basis": "event_date_local", "rounding": "HALF_EVEN"}


def row(row_id, group, account, debit=0, credit=0, cur="KZT", ts="2026-03-10T09:00:00+05:00",
        event=None, invoice=None, payment=None, alloc=None, alloc_cur=None, src=None, src_cur=None):
    return {"row_id": row_id, "timestamp": ts, "account": account, "debit_minor": debit,
            "credit_minor": credit, "currency": cur, "entity_id": "ent-01", "invoice_id": invoice,
            "payment_id": payment, "posting_group_id": group, "event_at": event or ts,
            "allocation_minor": alloc, "allocation_currency": alloc_cur,
            "fx_source_minor": src, "fx_source_currency": src_cur}


class ReconciliationRules(unittest.TestCase):
    """CONTRACT_G0 worked examples, one per issue type plus traps."""

    def test_duplicate_invoice_10000(self):
        ledger = [
            row("r1", "g1", "receivables", debit=1000000, invoice="inv-1"),
            row("r2", "g1", "revenue", credit=1000000, invoice="inv-1"),
            row("r3", "g2", "receivables", debit=1000000, invoice="inv-1", ts="2026-03-12T09:00:00+05:00"),
            row("r4", "g2", "revenue", credit=1000000, invoice="inv-1", ts="2026-03-12T09:00:00+05:00"),
            # trap: same amount, different invoice -> not a duplicate
            row("r5", "g3", "receivables", debit=1000000, invoice="inv-2"),
            row("r6", "g3", "revenue", credit=1000000, invoice="inv-2"),
        ]
        invoices = [{"invoice_id": "inv-1", "doc_id": "d1"}, {"invoice_id": "inv-2", "doc_id": "d2"}]
        found = reconcile({"ledger": ledger, "invoices": invoices}, MANIFEST, POLICY)
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["type"], set(f["affected_ids"]), f["claimed_amount_minor"]),
                         ("duplicate_invoice", {"inv-1", "r3", "r4"}, 1000000))
        self.assertEqual(len(reconcile({"ledger": ledger, "invoices": invoices}, MANIFEST, POLICY,
                                       aggregate_duplicates=True)), 1)

    def test_reversal_is_not_a_duplicate(self):
        ledger = [
            row("r1", "g1", "receivables", debit=1000000, invoice="inv-1"),
            row("r2", "g1", "revenue", credit=1000000, invoice="inv-1"),
            row("r3", "g2", "revenue", debit=1000000, invoice="inv-1"),
            row("r4", "g2", "receivables", credit=1000000, invoice="inv-1"),
        ]
        self.assertEqual(reconcile({"ledger": ledger}, MANIFEST, POLICY), [])
        self.assertEqual(reconcile({"ledger": ledger}, MANIFEST, POLICY, aggregate_duplicates=True), [])

    def test_fx_100_usd_at_500_booked_49000(self):
        ledger = [
            row("r1", "g1", "cash", debit=4900000, src=10000, src_cur="USD", ts="2026-03-05T09:00:00+05:00"),
            row("r2", "g1", "revenue", credit=4900000, ts="2026-03-05T09:00:00+05:00"),
        ]
        fx = [{"date": "2026-03-05", "source_currency": "USD", "target_currency": "KZT", "rate": "500.00"}]
        found = reconcile({"ledger": ledger, "fx": fx}, MANIFEST, POLICY)
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["type"], f["affected_ids"], f["claimed_amount_minor"], f["currency"]),
                         ("fx_conversion", ["r1"], 100000, "KZT"))
        self.assertEqual(set(f["evidence_ids"]), {"r1", "2026-03-05:USD:KZT"})

    def test_fx_uses_local_event_date(self):
        # 23:30 UTC on 4 March is 5 March in Almaty
        ts = "2026-03-04T23:30:00+00:00"
        ledger = [row("r1", "g1", "cash", debit=5000000, src=10000, src_cur="USD", ts=ts)]
        fx = [{"date": "2026-03-04", "source_currency": "USD", "target_currency": "KZT", "rate": "490"},
              {"date": "2026-03-05", "source_currency": "USD", "target_currency": "KZT", "rate": "500"}]
        self.assertEqual(reconcile({"ledger": ledger, "fx": fx}, MANIFEST, POLICY), [])

    def test_cutoff_event_march_booked_february(self):
        feb, mar = "2026-02-28T09:00:00+05:00", "2026-03-01T09:00:00+05:00"
        ledger = [
            row("r1", "g1", "receivables", debit=1500000, ts=feb, event=mar, invoice="inv-1"),
            row("r2", "g1", "revenue", credit=1500000, ts=feb, event=mar, invoice="inv-1"),
        ]
        found = reconcile({"ledger": ledger}, MANIFEST, POLICY)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["type"], set(found[0]["affected_ids"]), found[0]["claimed_amount_minor"]),
                         ("period_cutoff", {"r1", "r2"}, 1500000))

    def test_cutoff_boundary_is_half_open(self):
        end = "2026-04-01T00:00:00+05:00"
        ledger = [row("r1", "g1", "cash", debit=1, ts=end, event=end)]
        self.assertEqual(reconcile({"ledger": ledger}, MANIFEST, POLICY), [])

    def test_interaction_partial_allocation_and_fx(self):
        # 100 USD intended, 80 USD allocated (20 USD short); 80 USD at 490 instead of 500
        ts = "2026-03-05T09:00:00+05:00"
        ledger = [
            row("r1", "g1", "cash", debit=3920000, src=8000, src_cur="USD", ts=ts, payment="p1"),
            row("r2", "g1", "receivables", credit=3920000, ts=ts, invoice="i1", payment="p1",
                alloc=8000, alloc_cur="USD"),
        ]
        payments = [{"payment_id": "p1", "doc_id": "dp1", "currency": "USD", "amount_minor": 10000,
                     "intended_allocations": [{"invoice_id": "i1", "amount_minor": 10000, "currency": "USD"}]}]
        invoices = [{"invoice_id": "i1", "doc_id": "di1"}]
        fx = [{"date": "2026-03-05", "source_currency": "USD", "target_currency": "KZT", "rate": "500"}]
        found = {f["type"]: f for f in reconcile(
            {"ledger": ledger, "payments": payments, "invoices": invoices, "fx": fx}, MANIFEST, POLICY)}
        self.assertEqual(set(found), {"payment_allocation", "fx_conversion"})
        self.assertEqual((found["payment_allocation"]["claimed_amount_minor"],
                          found["payment_allocation"]["currency"]), (2000, "USD"))
        self.assertEqual((found["fx_conversion"]["claimed_amount_minor"],
                          found["fx_conversion"]["currency"]), (80000, "KZT"))

    def test_split_payment_trap_is_clean(self):
        # 120000 invoice paid 50000 + 70000, each fully allocated
        ledger = [
            row("r1", "g1", "cash", debit=5000000, payment="p1"),
            row("r2", "g1", "receivables", credit=5000000, invoice="i1", payment="p1", alloc=5000000, alloc_cur="KZT"),
            row("r3", "g2", "cash", debit=7000000, payment="p2"),
            row("r4", "g2", "receivables", credit=7000000, invoice="i1", payment="p2", alloc=7000000, alloc_cur="KZT"),
        ]
        payments = [
            {"payment_id": "p1", "doc_id": "dp1", "intended_allocations": [{"invoice_id": "i1", "amount_minor": 5000000, "currency": "KZT"}]},
            {"payment_id": "p2", "doc_id": "dp2", "intended_allocations": [{"invoice_id": "i1", "amount_minor": 7000000, "currency": "KZT"}]},
        ]
        self.assertEqual(reconcile({"ledger": ledger, "payments": payments}, MANIFEST, POLICY), [])


class ReviewRegressions(unittest.TestCase):
    def test_odd_public_policy_values_do_not_crash(self):
        ledger = [row("r1", "g1", "cash", debit=4900000, src=10000, src_cur="USD", ts="2026-03-05T09:00:00+05:00")]
        fx = [{"date": "2026-03-05", "source_currency": "USD", "target_currency": "KZT", "rate": "500"}]
        for policy in ({"fx_date_basis": None}, {"fx_date_basis": 5, "rounding": ["x"]}, None, "oops"):
            with self.subTest(policy=policy):
                found = reconcile({"ledger": ledger, "fx": fx}, MANIFEST, policy)
                self.assertEqual([f["type"] for f in found], ["fx_conversion"])
        self.assertEqual(reconcile({"ledger": [{"row_id": 1}], "payments": [None]}, None, None), [])

    def test_unapplied_row_survives_evidence_cap(self):
        ts = "2026-03-05T09:00:00+05:00"
        ledger = [row("cash", "g", "cash", debit=1000, payment="p")]
        intended = []
        for i in range(9):
            inv = f"i{i}"
            want = 100 if i else 200
            intended.append({"invoice_id": inv, "amount_minor": want, "currency": "KZT"})
            ledger.append(row(f"a{i}", "g", "receivables", credit=100, invoice=inv, payment="p",
                              alloc=100, alloc_cur="KZT", ts=ts))
        ledger.append(row("unapplied", "g", "unapplied_cash", credit=100, payment="p", ts=ts))
        payments = [{"payment_id": "p", "doc_id": "dp", "intended_allocations": intended}]
        invoices = [{"invoice_id": f"i{i}", "doc_id": f"di{i}"} for i in range(9)]
        found = reconcile({"ledger": ledger, "payments": payments, "invoices": invoices}, MANIFEST, POLICY)
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["affected_ids"], f["claimed_amount_minor"]), (["p", "i0"], 100))
        self.assertLessEqual(len(f["evidence_ids"]), 10)
        self.assertTrue({"dp", "di0", "a0", "unapplied"} <= set(f["evidence_ids"]))

    def test_duplicate_order_uses_real_instants(self):
        # g-late is lexically smaller but happens later: 23:00 UTC = 04:00+05 next day
        late, early = "2026-03-10T23:00:00+00:00", "2026-03-11T01:00:00+05:00"
        ledger = [
            row("l1", "g-late", "receivables", debit=500, invoice="inv", ts=late),
            row("l2", "g-late", "revenue", credit=500, invoice="inv", ts=late),
            row("e1", "g-early", "receivables", debit=500, invoice="inv", ts=early),
            row("e2", "g-early", "revenue", credit=500, invoice="inv", ts=early),
        ]
        found = reconcile({"ledger": ledger}, MANIFEST, POLICY)
        dup = [f for f in found if f["type"] == "duplicate_invoice"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(set(dup[0]["affected_ids"]), {"inv", "l1", "l2"})

    def test_unknown_exponent_is_skipped_not_assumed(self):
        ledger = [row("r1", "g1", "cash", debit=50000, src=100, src_cur="JPY", ts="2026-03-05T09:00:00+05:00")]
        fx = [{"date": "2026-03-05", "source_currency": "JPY", "target_currency": "KZT", "rate": "5"}]
        self.assertEqual(reconcile({"ledger": ledger, "fx": fx}, MANIFEST, POLICY), [])

    def test_payment_group_across_boundary_is_not_cutoff(self):
        ledger = [
            row("c1", "g1", "cash", debit=100, payment="p", ts="2026-04-01T00:10:00+05:00",
                event="2026-03-31T23:00:00+05:00"),
            row("c2", "g1", "receivables", credit=100, payment="p", invoice="i",
                ts="2026-04-01T00:10:00+05:00", event="2026-03-31T23:00:00+05:00"),
        ]
        self.assertEqual(reconcile({"ledger": ledger}, MANIFEST, POLICY), [])


if __name__ == "__main__":
    unittest.main()
