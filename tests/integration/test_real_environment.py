"""Checks exercised against role B's actual environment during full verification."""

import os
import unittest
from pathlib import Path

import judge_adapter
from genesis_audit.runner.oracle import oracle_findings

FIXTURE = Path(__file__).parent / "fixtures/g0_partial_payment"


@unittest.skipUnless(os.environ.get("GENESIS_FULL_CHECKS") == "1", "requires role B")
class RealEnvironmentTests(unittest.TestCase):
    def reset(self, mode="closed"):
        env = judge_adapter.make_env()
        observation, info = env.reset(0, {
            "mode": mode, "episode_dir": str(FIXTURE), "team_config": {},
        })
        self.assertEqual(observation["manifest"]["episode_id"], "g0-partial")
        self.assertEqual(observation["remaining_steps"], 100)
        self.assertNotIn("seed", info)
        return env

    def test_c16_submit_at_100(self):
        env = self.reset()
        read = {"name": "get_record", "args": {"table": "ledger", "id": "row-01"}}
        for _ in range(99):
            _, reward, terminated, truncated, _ = env.step(read)
            self.assertEqual((reward, terminated, truncated), (0, False, False))
        _, reward, terminated, truncated, _ = env.step({
            "name": "submit", "args": {"findings": oracle_findings(FIXTURE)},
        })
        self.assertEqual((reward, terminated, truncated), (0, True, False))
        self.assertIn(type(reward), (int, float))

    def test_c17_100_inspections_truncate(self):
        env = self.reset()
        read = {"name": "get_record", "args": {"table": "ledger", "id": "row-01"}}
        for _ in range(100):
            _, reward, terminated, truncated, _ = env.step(read)
        self.assertEqual((reward, terminated, truncated), (0, False, True))

    def test_train_reward_comes_only_at_submit(self):
        env = self.reset("train")
        _, reward, terminated, truncated, _ = env.step({
            "name": "get_record", "args": {"table": "ledger", "id": "row-01"},
        })
        self.assertEqual((reward, terminated, truncated), (0, False, False))
        _, reward, terminated, truncated, info = env.step({
            "name": "submit", "args": {"findings": oracle_findings(FIXTURE)},
        })
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertAlmostEqual(reward, .998)
        self.assertNotIn("metrics", info)
