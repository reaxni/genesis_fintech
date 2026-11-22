"""Exercise paired episodes both independently and through the normal entry points."""

import importlib.util
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from genesis_audit.contracts.v1 import TABLES
from genesis_audit.data import generate_episode
from genesis_audit.data.export import read_episode, write_episode
from genesis_audit.verification import transform_episode, verify_episode


def semantic_signature(facts, inverse):
    return Counter(
        (fact.kind, frozenset(inverse.get(item, item) for item in fact.affected),
         fact.amount, fact.currency, frozenset(inverse.get(item, item) for item in fact.evidence))
        for fact in facts
    )


PAIRS = [(number, None) for number in range(1, 21)] + [
    (6, "stress"), (18, "diverse"), (19, "diverse"),
]


def build_pair(path, number, profile=None):
    source, target = path / "source", path / "transformed"
    config = {"scenario": f"D{number:02d}"}
    if profile:
        config["profile"] = profile
    generate_episode(500 + number, config, source)
    inverse = transform_episode(source, target, seed=16 + number,
                                shuffle=number <= 5 or number >= 11, rename_ids=number >= 6)
    return source, target, inverse


class VerificationTests(unittest.TestCase):
    def test_p01_to_p20_preserve_semantics_and_nested_order(self):
        with tempfile.TemporaryDirectory() as temp:
            for number, stress in PAIRS:
                with self.subTest(pair=f"P{number:02d}", stress=stress):
                    source, target, inverse = build_pair(Path(temp), number, stress)
                    old, new = read_episode(source), read_episode(target)
                    self.assertEqual(semantic_signature(verify_episode(source), {}),
                                     semantic_signature(verify_episode(target), inverse))
                    self.assertEqual(sorted(old["fx"], key=str), sorted(new["fx"], key=str))
                    self.assertEqual(old["policy"], new["policy"])
                    self.assertFalse(set(inverse) & set(inverse.values()))
                    for table, key in (("ledger", "row_id"), ("invoices", "invoice_id"),
                                       ("payments", "payment_id")):
                        before = [item[key] for item in old[table]]
                        after = [inverse.get(item[key], item[key]) for item in new[table]]
                        self.assertCountEqual(before, after)
                        self.assertEqual(before == after, 6 <= number <= 10)
                    payments = {p["payment_id"]: p for p in old["payments"]}
                    for payment in new["payments"]:
                        original = payments[inverse.get(payment["payment_id"], payment["payment_id"])]
                        mapped = [inverse.get(ref, ref) for ref in payment["invoice_refs"]]
                        self.assertEqual(mapped, original["invoice_refs"])
                        mapped = [dict(a, invoice_id=inverse.get(a["invoice_id"], a["invoice_id"]))
                                  for a in payment["intended_allocations"]]
                        self.assertEqual(mapped, original["intended_allocations"])

    def test_rejects_mislabelled_amount_provenance_and_evidence(self):
        def amount(episode):
            episode["truth"]["issues"][0]["expected_amount_minor"] += 1

        def evidence(episode):
            episode["truth"]["issues"][0]["evidence_sets"][0].append("unknown-record")

        def provenance(episode):
            episode["provenance"]["changes"].reverse()

        def ledger(episode):
            row = next(item for item in episode["ledger"] if item["debit_minor"])
            row["debit_minor"] += 1

        with tempfile.TemporaryDirectory() as temp:
            for change in (amount, evidence, provenance, ledger):
                with self.subTest(change=change.__name__):
                    directory = Path(temp)
                    generate_episode(9, {"scenario": "D16"}, directory)
                    episode = read_episode(directory)
                    change(episode)
                    write_episode(directory, episode)
                    with self.assertRaises(ValueError):
                        verify_episode(directory)


@unittest.skipUnless(importlib.util.find_spec("judge_adapter")
                     and importlib.util.find_spec("genesis_audit.env")
                     and importlib.util.find_spec("genesis_audit.runner"),
                     "roles B and C must be present for full environment comparison")
class PairedEnvironmentTests(unittest.TestCase):
    def test_p01_to_p20_reset_replay_and_score(self):
        from judge_adapter import make_env, score_episode
        from genesis_audit.runner.oracle import oracle_findings

        def run(directory):
            env = make_env()
            result = [env.reset(seed=17, config={"mode": "closed", "episode_dir": str(directory)})]
            disk = read_episode(directory)
            for table in TABLES:
                response = env.step({"name": "list_records", "args": {"table": table, "limit": 50}})
                self.assertEqual(response[0]["result"]["records"], disk[table][:50])
                result.append(response)
            payload = oracle_findings(directory)
            result.append(env.step({"name": "submit", "args": {"findings": payload}}))
            steps = env.outcome()["inspection_steps"]
            self.assertEqual(steps, 4)
            score = score_episode(str(directory), payload, steps)
            self.assertEqual(score, env.final_score())
            return result, payload, score

        def findings_signature(payload, inverse):
            return Counter(
                (item["type"], item["claimed_amount_minor"], item["currency"],
                 frozenset(inverse.get(uid, uid) for uid in item["affected_ids"]),
                 frozenset(inverse.get(uid, uid) for uid in item["evidence_ids"]))
                for item in payload["findings"]
            )

        with tempfile.TemporaryDirectory() as temp:
            for number, stress in PAIRS:
                with self.subTest(pair=f"P{number:02d}", stress=stress):
                    source, target, inverse = build_pair(Path(temp), number, stress)
                    observations, payload, score = run(source)
                    paired, paired_payload, paired_score = run(target)
                    self.assertEqual(observations, run(source)[0])
                    self.assertEqual(score, paired_score)
                    self.assertEqual(score["fp"], 0)
                    self.assertEqual(score["fn"], 0)
                    self.assertEqual(score["return"], .992)
                    self.assertEqual(findings_signature(payload, {}),
                                     findings_signature(paired_payload, inverse))
                    self.assertEqual(observations[-1][1:4], (0, True, False))
                    self.assertEqual(paired[-1][1:4], (0, True, False))
                    rendered = json.dumps(observations + paired)
                    for private in ("seed", "truth", "provenance", "changes"):
                        self.assertNotIn(f'"{private}"', rendered)
