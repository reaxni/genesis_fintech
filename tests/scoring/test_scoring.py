"""Published reward and matcher cases, independent of the generator."""

import itertools
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from genesis_audit.contracts.v1 import ISSUE_TYPES
from genesis_audit.scoring import score_episode
from genesis_audit.scoring.matcher import select_pairs

FIXTURE = Path(__file__).resolve().parents[1] / "integration/fixtures/g0_partial_payment"
PRIMARY = "payment_allocation"
ALTERNATE = "duplicate_invoice"
PROOF = ["doc-inv-01", "doc-pay-01"]
AVAILABLE_EXTRA = "row-06"


def issue(issue_id="A", affected=None, proof=None):
    return {
        "issue_id": issue_id, "type": PRIMARY, "affected_ids": affected or ["row-01"],
        "expected_amount_minor": 10000, "currency": "KZT",
        "evidence_sets": [proof or PROOF],
    }


def finding(**changes):
    result = {
        "type": PRIMARY, "affected_ids": ["row-01"], "claimed_amount_minor": 10000,
        "currency": "KZT", "evidence_ids": PROOF, "explanation": "public proof",
    }
    result.update(changes)
    return result


class PublishedScoringTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.episode = Path(temp.name) / "episode"
        shutil.copytree(FIXTURE, self.episode)

    def score(self, findings, truth=None, *, steps=10, tolerance=0):
        (self.episode / "truth.json").write_text(
            json.dumps({"issues": [issue()] if truth is None else truth}), encoding="utf-8"
        )
        (self.episode / "policy.json").write_text(
            json.dumps({"amount_tolerance_minor": {PRIMARY: tolerance}}), encoding="utf-8"
        )
        return score_episode(
            str(self.episode), {"episode_id": "g0-partial", "findings": findings}, steps
        )

    def assert_score(self, got, counts, expected_reward):
        self.assertEqual((got["tp"], got["fp"], got["fn"]), counts)
        self.assertAlmostEqual(got["return"], expected_reward, delta=1e-9)
        tp, fp, fn = counts
        if tp is None:
            self.assertIsNone(got["f1"])
            self.assertIsNone(got["precision"])
            self.assertIsNone(got["recall"])
            return
        self.assertAlmostEqual(got["precision"], tp / (tp + fp) if tp + fp else 0)
        self.assertAlmostEqual(got["recall"], tp / (tp + fn) if tp + fn else 0)
        total = 2 * tp + fp + fn
        self.assertAlmostEqual(got["f1"], 2 * tp / total if total else 1)

    def test_r01_to_r20(self):
        missing = (0, 1, 1)
        structural = (None, None, None)
        mass = [
            finding(type=kind, affected_ids=[f"row-{number:02d}"])
            for kind in ISSUE_TYPES
            for number in range(1, 6)
        ]
        double = [issue(), issue("B", ["row-02"])]
        cases = [
            ([finding()], None, 10, (1, 0, 0), .98),
            ([], None, 10, (0, 0, 1), -1),
            ([], [], 10, (0, 0, 0), .98),
            ([finding()], [], 10, (0, 1, 0), -1),
            ([finding(), finding()], None, 10, (1, 1, 0), 47 / 150),
            ([finding(type=ALTERNATE)], None, 10, missing, -1),
            ([finding(affected_ids=["row-02"])], None, 10, missing, -1),
            ([finding(claimed_amount_minor=10001)], None, 10, missing, -1),
            ([finding(currency="USD")], None, 10, missing, -1),
            ([finding(evidence_ids=PROOF[:1])], None, 10, missing, -1),
            ([finding(evidence_ids=[PROOF[0], "U"])], None, 10, missing, -1),
            (mass, None, 10, (1, 19, 0), -871 / 1050),
            ([finding()], double, 10, (1, 0, 1), 47 / 150),
            ([finding(), finding(type=ALTERNATE)], double, 10, (1, 1, 1), -.02),
            ([finding()], None, 0, (1, 0, 0), 1),
            ([finding()], None, 50, (1, 0, 0), .90),
            ([finding()], None, 100, (1, 0, 0), .80),
            ([finding()] * 31, None, 10, structural, -1),
            ([finding(evidence_ids=[str(i) for i in range(11)])], None, 10, structural, -1),
            ([finding(explanation="x" * 1001)], None, 10, structural, -1),
        ]
        for number, (findings, truth, steps, counts, reward) in enumerate(cases, 1):
            with self.subTest(case=f"R{number:02d}"):
                self.assert_score(self.score(findings, truth, steps=steps), counts, reward)

    def test_m01_to_m09_and_m12(self):
        truth = [issue(affected=["row-01", "row-02"])]
        signed = {"affected_ids": ["row-01", "row-02"]}
        tp, fp, invalid = (1, 0, 0), (0, 1, 1), (None, None, None)
        cases = [
            ("M01", [finding(affected_ids=["row-02", "row-01", "row-01"])], 0, tp, .98),
            ("M02", [finding(affected_ids=["row-01", "row-02", "row-03"])], 0, fp, -1),
            ("M03", [finding(**signed, evidence_ids=PROOF[::-1] + [AVAILABLE_EXTRA])], 0, tp, .98),
            ("M04", [finding(**signed, evidence_ids=PROOF + ["U"])], 0, fp, -1),
            ("M05", [finding(**signed, evidence_ids=[PROOF[0]] * 10 + PROOF[1:])], 0, invalid, -1),
            ("M06a", [finding(**signed, currency="kzt")], 0, fp, -1),
            ("M06b", [finding(**signed, currency="KZT ")], 0, fp, -1),
            ("M07", [finding(**signed, claimed_amount_minor=10001)], 0, fp, -1),
            ("M08a", [finding(**signed, claimed_amount_minor=10001)], 1, tp, .98),
            ("M08b", [finding(**signed, claimed_amount_minor=10002)], 1, fp, -1),
            ("M09a", [finding(**signed, claimed_amount_minor="10000")], 0, invalid, -1),
            ("M09b", [finding(**signed, claimed_amount_minor=True)], 0, invalid, -1),
            ("M12", [finding(**signed)] * 2, 0, (1, 1, 0), 47 / 150),
        ]
        for case, findings, tolerance, counts, reward in cases:
            with self.subTest(case=case):
                self.assert_score(self.score(findings, truth, tolerance=tolerance), counts, reward)

    def test_m10_and_m11(self):
        self.assertEqual(
            select_pairs([("A", 0), ("A", 1), ("B", 0)]), (("A", 1), ("B", 0)),
        )
        complete = [("A", 0), ("A", 1), ("B", 0), ("B", 1)]
        self.assertEqual(select_pairs(complete[::-1]), (("A", 0), ("B", 1)))
        truth = [issue(proof=[PROOF[0]]), issue("B", proof=[PROOF[1]])]
        full, half = finding(), finding(evidence_ids=PROOF[:1])
        for order in ([full, half], [half, full]):
            self.assert_score(self.score(order, truth), (2, 0, 0), .98)

    def test_all_three_by_three_graphs_against_brute_force(self):
        universe = list(itertools.product("ABC", range(3)))
        for mask in range(1 << len(universe)):
            edges = [edge for i, edge in enumerate(universe) if mask & (1 << i)]
            expected = ()
            for size in range(3, 0, -1):
                candidates = [
                    pairs for pairs in itertools.combinations(edges, size)
                    if len({a for a, _ in pairs}) == len({b for _, b in pairs}) == size
                ]
                if candidates:
                    expected = min(candidates)
                    break
            self.assertEqual(select_pairs(edges), expected, mask)
