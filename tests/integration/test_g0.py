"""Hand-calculated fixture and trusted adapter signature."""

import json
import unittest
from pathlib import Path

import judge_adapter
from genesis_audit.runner.oracle import oracle_findings

FIXTURE = Path(__file__).parent / "fixtures/g0_partial_payment"


class G0Tests(unittest.TestCase):
    def test_score_and_oracle(self):
        expected = json.loads((FIXTURE / "expected_score.json").read_text(encoding="utf-8"))
        submitted = json.loads((FIXTURE / "expected_findings.json").read_text(encoding="utf-8"))
        self.assertEqual(judge_adapter.score_episode(str(FIXTURE), submitted, 4), expected)
        self.assertEqual(
            judge_adapter.score_episode(str(FIXTURE), oracle_findings(FIXTURE), 4), expected
        )
