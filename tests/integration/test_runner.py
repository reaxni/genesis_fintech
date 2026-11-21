"""Exercise C's runner boundary without replacing role B's environment."""

import io
import json
import socket
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from types import ModuleType
from unittest.mock import Mock, patch

import judge_adapter
from genesis_audit.contracts.wire import MAX_MESSAGE_BYTES, receive, send
from genesis_audit.runner.oracle import oracle_findings
from genesis_audit.runner.session import run_episode

FIXTURE = Path(__file__).parent / "fixtures/g0_partial_payment"


class FakeEnvironment:
    """Only a test double. Role B supplies the real environment."""

    def reset(self, seed, config):
        self.config = config
        return {"task": "audit", "remaining_steps": 100}, {}

    def step(self, action):
        done = action.get("name") == "submit"
        return {"result": {"accepted": True} if done else {}, "remaining_steps": 99}, 0, done, False, {}

    def truncate(self):
        return {"result": None, "error": {"code": "timeout"}, "remaining_steps": 100}, 0, False, True, {}


class RunnerTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux Unix-socket transport")
    def test_two_episodes_over_unix_socket(self):
        from genesis_audit.runner.remote_policy import PolicyListener

        observations = [{"remaining_steps": 100}, {"remaining_steps": 99}]
        action = {"name": "list_records", "args": {"table": "ledger", "limit": 1}}

        def worker(path):
            messages = []
            for _ in range(2):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(5)
                    client.connect(str(path))
                    with client.makefile("rb") as reader:
                        for _ in observations:
                            messages.append(receive(reader))
                            send(client, action)
                        self.assertEqual(reader.read(1), b"")
            return messages

        with TemporaryDirectory() as directory:
            path = Path(directory) / "genesis.sock"
            with PolicyListener(path) as listener, ThreadPoolExecutor(max_workers=1) as pool:
                client = pool.submit(worker, path)
                expected = []
                for policy in ("random", "reconciliation"):
                    with listener.episode(policy) as choose:
                        for observation in observations:
                            self.assertEqual(choose(observation, monotonic() + 5), action)
                            expected.append({"policy": policy, "observation": observation})
                self.assertEqual(client.result(timeout=5), expected)
            self.assertFalse(path.exists())

    def test_runner_scores_closed_without_revealing_reward(self):
        submitted = oracle_findings(FIXTURE)
        actions = iter([
            {"name": "list_records", "args": {"table": "ledger", "cursor": None, "limit": 1}},
            {"name": "submit", "args": {"findings": submitted}},
        ])
        records = []
        env = FakeEnvironment()
        score = run_episode(
            env, episode_dir=FIXTURE, seed=50,
            choose=lambda observation, deadline: next(actions),
            score=judge_adapter.score_episode, record=records.append,
        )
        self.assertEqual(env.config["episode_dir"], str(FIXTURE))
        self.assertEqual(score["inspection_steps"], 1)
        self.assertAlmostEqual(score["return"], .998)
        self.assertEqual([row["reward"] for row in records], [0, 0])
        self.assertNotIn("return", json.dumps(records))

    def test_timeout_scores_empty_findings(self):
        records = []
        clock_values = iter([0, 61, 61])
        def unexpected(*_args):
            self.fail("the deadline already expired")
        score = run_episode(
            FakeEnvironment(), episode_dir=FIXTURE, seed=0, choose=unexpected,
            score=judge_adapter.score_episode, record=records.append,
            clock=lambda: next(clock_values),
        )
        self.assertTrue(records[0]["truncated"])
        self.assertEqual(score["steps"], 0)
        self.assertEqual((score["tp"], score["fp"], score["fn"]), (0, 0, 1))

    def test_wire_valid_and_bounded(self):
        control = Mock()
        send(control, {"name": "submit"})
        payload = control.sendall.call_args.args[0]
        self.assertEqual(receive(io.BytesIO(payload)), {"name": "submit"})
        with self.assertRaisesRegex(ValueError, "IPC frame"):
            receive(io.BytesIO(b"x" * (MAX_MESSAGE_BYTES + 2)))
        with self.assertRaisesRegex(ValueError, "too large"):
            send(control, {"value": "x" * MAX_MESSAGE_BYTES})
        control.sendall.assert_called_once()
        with self.assertRaisesRegex(ValueError, "object"):
            receive(io.BytesIO(b"[]\n"))

    def test_adapter_forwards_roles_without_own_policy(self):
        calls = []
        data = ModuleType("genesis_audit.data")
        env = ModuleType("genesis_audit.env")
        data.generate_episode = lambda *args: calls.append(args)
        env.AuditEnv = lambda **kwargs: kwargs
        with patch.dict("sys.modules", {data.__name__: data, env.__name__: env}):
            judge_adapter.generate_episode(3, {"scenario": 1}, "episode")
            callbacks = judge_adapter.make_env()
        self.assertEqual(calls, [(3, {"scenario": 1}, "episode")])
        self.assertIs(callbacks["generate_episode"], judge_adapter.generate_episode)
        self.assertIs(callbacks["score_episode"], judge_adapter.score_episode)
