"""Data acceptance cases with independently calculated expected amounts."""

import tempfile
import unittest
from collections import Counter
from pathlib import Path

from genesis_audit.contracts.v1 import ISSUE_TYPES
from genesis_audit.data import generate_episode
from genesis_audit.data.corpus import build_corpus
from genesis_audit.data.export import read_episode
from genesis_audit.verification import verify_episode

EXPECTED = {
    6: [("duplicate_invoice", 1_000_000, "KZT")],
    7: [("duplicate_invoice", 7_345_000, "KZT")],
    8: [("duplicate_invoice", 1_825_000, "KZT")],
    9: [("payment_allocation", 3_100_000, "KZT")],
    10: [("payment_allocation", 5_000_000, "KZT")],
    11: [("payment_allocation", 2_360_000, "KZT")],
    12: [("fx_conversion", 100_000, "KZT")],
    13: [("fx_conversion", 13_000, "KZT")],
    14: [("period_cutoff", 6_700_000, "KZT")],
    15: [("period_cutoff", 2_230_000, "KZT")],
    16: [("payment_allocation", 2_000, "USD"), ("fx_conversion", 80_000, "KZT")],
    17: [("payment_allocation", 5_000, "EUR"), ("fx_conversion", 100_000, "KZT")],
    18: [("payment_allocation", 1_500, "USD"), ("fx_conversion", 20_250, "KZT")],
    19: [("period_cutoff", 12_000_000, "KZT"), ("duplicate_invoice", 12_000_000, "KZT")],
    20: [("period_cutoff", 9_265_000, "KZT"), ("duplicate_invoice", 9_265_000, "KZT")],
}


def files(directory):
    return {file.relative_to(directory): file.read_bytes()
            for file in directory.rglob("*") if file.is_file()}


class GenerationTests(unittest.TestCase):
    def test_d01_to_d20(self):
        with tempfile.TemporaryDirectory() as temp:
            for number in range(1, 21):
                scenario = f"D{number:02d}"
                with self.subTest(scenario=scenario):
                    directory = Path(temp) / scenario
                    generate_episode(300 + number, {"scenario": scenario}, directory)
                    episode = read_episode(directory)
                    self.assertGreaterEqual(episode["manifest"]["row_count"], 200)
                    self.assertLessEqual(episode["manifest"]["document_count"], 200)
                    self.assertCountEqual(
                        [(f.kind, f.amount, f.currency) for f in verify_episode(directory)],
                        EXPECTED.get(number, []),
                    )

    def test_independent_faults_and_config_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for issue in ISSUE_TYPES:
                with self.subTest(issue=issue):
                    generate_episode(7, {"errors": [issue]}, directory)
                    self.assertEqual([fact.kind for fact in verify_episode(directory)], [issue])
            count = {kind: 3 for kind in reversed(ISSUE_TYPES)}
            generate_episode(500, {"errors": count, "invoice_count": 80,
                                   "timezone": "America/New_York"}, directory)
            self.assertEqual(Counter(f.kind for f in verify_episode(directory)), count)
            episode = read_episode(directory)
            self.assertLessEqual(episode["manifest"]["document_count"], 200)
            self.assertTrue(any(row["fx_source_currency"] == "JPY" for row in episode["ledger"]))
            for invalid in ({"errors": {"fx_conversion": True}}, {"errors": ISSUE_TYPES},
                            {"invoice_count": 49}, {"errors": {"period_cutoff": 13}},
                            {"scenario": "D01", "errors": []}):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    generate_episode(7, invalid, directory)

    def test_deterministic_bytes_seed_and_config(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp) / "first", Path(temp) / "second"
            config = {"scenario": "D18", "timezone": "America/New_York"}
            generate_episode(42, config, first)
            generate_episode(42, dict(reversed(list(config.items()))), second)
            self.assertEqual(files(first), files(second))
            generate_episode(43, config, second)
            self.assertNotEqual(read_episode(first)["manifest"], read_episode(second)["manifest"])
            generate_episode(42, {"scenario": "D17", "timezone": config["timezone"]}, second)
            self.assertNotEqual(read_episode(first)["manifest"]["episode_id"],
                                read_episode(second)["manifest"]["episode_id"])

    def test_committed_corpus_rebuild_is_identical(self):
        published = Path(__file__).resolve().parents[2] / "episodes"
        with tempfile.TemporaryDirectory() as temp:
            rebuilt = Path(temp)
            build_corpus(rebuilt)
            ids = []
            for split in ("train", "dev"):
                self.assertEqual(files(published / split), files(rebuilt / split))
                self.assertEqual(len(list((rebuilt / split).glob("*/manifest.json"))), 10)
                for path in (rebuilt / split).glob("*/manifest.json"):
                    episode = read_episode(path.parent)
                    ids.append(episode["manifest"]["episode_id"])
            self.assertEqual(len(set(ids)), 20)

    def test_stress_cases_keep_proofs_and_clean_controls(self):
        published = Path(__file__).resolve().parents[2] / "episodes"
        stress_sizes = []
        for manifest in sorted(published.glob("*/*/manifest.json")):
            episode = read_episode(manifest.parent)
            config = episode["provenance"]["config"]
            if config.get("profile") != "stress":
                continue
            with self.subTest(scenario=config["scenario"]):
                facts = verify_episode(manifest.parent)
                stress_sizes.append(episode["manifest"]["row_count"])
                self.assertLessEqual(episode["manifest"]["document_count"], 200)
                self.assertGreaterEqual(sum(p["amount_minor"] < 0 for p in episode["payments"]), 2)
                installments = Counter(a["invoice_id"] for p in episode["payments"]
                                       for a in p["intended_allocations"])
                self.assertGreaterEqual(max(installments.values()), 4)
                if int(config["scenario"][1:]) <= 5:
                    self.assertFalse(facts)
                else:
                    self.assertIn(len(facts), range(3, 6))
                    self.assertTrue(any(f.kind == "duplicate_invoice" and len(f.affected) == 4
                                        for f in facts))
        self.assertEqual(len(stress_sizes), 10)
        self.assertGreater(max(stress_sizes) - min(stress_sizes), 100)
