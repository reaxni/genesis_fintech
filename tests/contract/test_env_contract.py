"""Role B contract tests C01-C20 for AuditEnv (process-local part).

Run:  PYTHONPATH=src python -m unittest discover -s tests/contract -v

C18 here uses a controlled clock; the real 60 s run and the container
isolation part of C20 are exercised on Linux through the Compose runner.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from genesis_audit.contracts.v1 import ACTION_LIMIT, ACTIONS, PAGE_LIMIT, TABLES  # noqa: E402
from genesis_audit.env import AuditEnv  # noqa: E402

FIXTURE = ROOT / "tests/integration/fixtures/g0_partial_payment"
SEED = 424242
PRIVATE_MARKERS = (
    "truth", "provenance", "issue_id", "issue-01", "expected_amount_minor",
    "evidence_sets", "changes", "tp", "fp", "fn", "f1", "precision", "recall",
    "score", "seed", str(SEED),
)


# --------------------------------------------------------------- helpers


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class RecordingScorer:
    """Stand-in for score_episode: returns a recognisable value, logs calls."""

    def __init__(self, value: float = 0.5) -> None:
        self.value = value
        self.calls = []

    def __call__(self, episode_dir, findings, inspection_steps):
        self.calls.append((episode_dir, findings, inspection_steps))
        return {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0,
                "f1": 1.0, "return": self.value}


def read(a, b=None):
    return {"name": a, "args": b or {}}


LEDGER_READ = read("get_record", {"table": "ledger", "id": "row-01"})


def expected_findings():
    return json.loads((FIXTURE / "expected_findings.json").read_text(encoding="utf-8"))


def make_clean_episode(target: Path) -> Path:
    """C: clean twin of the fixture - pay-02 fully allocated, empty truth."""
    shutil.copytree(FIXTURE, target)
    for private in ("expected_findings.json", "expected_score.json"):
        (target / private).unlink()
    with (FIXTURE / "ledger.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    fields = list(rows[0].keys())
    clean = []
    for row in rows:
        if row["row_id"] == "row-06":
            row = dict(row, credit_minor="7000000", allocation_minor="7000000")
        if row["row_id"] == "row-07":
            continue
        clean.append(row)
    with (target / "ledger.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(clean)
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(episode_id="g0-clean", row_count=len(clean))
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (target / "truth.json").write_text(json.dumps({"issues": []}), encoding="utf-8")
    (target / "provenance.json").write_text(
        json.dumps({"schema_version": "1.0", "changes": []}), encoding="utf-8")
    return target


def make_large_episode(target: Path, rows: int = 137) -> Path:
    """L: enough ledger rows for several pages, including equal amounts."""
    shutil.copytree(FIXTURE, target)
    with (FIXTURE / "ledger.csv").open(encoding="utf-8", newline="") as handle:
        src = list(csv.DictReader(handle))
    fields = list(src[0].keys())
    out = []
    for i in range(rows):
        base = dict(src[i % len(src)])
        base["row_id"] = f"row-{i + 1:04d}"
        base["posting_group_id"] = f"pg-{i // 2:04d}"
        out.append(base)
    with (target / "ledger.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out)
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(episode_id="g0-large", row_count=rows)
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return target


class EnvTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="genesis-b-tests-")
        cls.CLEAN = make_clean_episode(Path(cls._tmp) / "clean")
        cls.LARGE = make_large_episode(Path(cls._tmp) / "large")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def make(self, episode=FIXTURE, mode="closed", scorer=None, clock=None):
        env = AuditEnv(
            generate_episode=None,
            score_episode=scorer or RecordingScorer(),
            **({"clock": clock} if clock else {}),
        )
        observation, info = env.reset(SEED, {
            "mode": mode, "episode_dir": str(episode), "team_config": {},
        })
        return env, observation, info

    def assertStepShape(self, result):
        self.assertEqual(len(result), 5)
        observation, reward, terminated, truncated, info = result
        self.assertIs(type(terminated), bool)
        self.assertIs(type(truncated), bool)
        self.assertIn(type(reward), (int, float))
        self.assertIsInstance(info, dict)
        self.assertEqual(set(observation), {"result", "error", "remaining_steps"})
        json.dumps(result, allow_nan=False)
        return result


# ------------------------------------------------------------------ C01-C03


class ResetContract(EnvTestCase):
    def test_c01_reset_returns_json_pair(self):
        env, observation, info = self.make(self.CLEAN)
        self.assertIsInstance(observation, dict)
        self.assertIsInstance(info, dict)
        json.dumps([observation, info], allow_nan=False)

    def test_c02_first_observation_keys_and_no_secrets(self):
        _, observation, info = self.make(self.CLEAN)
        self.assertEqual(set(observation), {"task", "manifest", "policy", "remaining_steps"})
        self.assertIsInstance(observation["task"], str)
        self.assertEqual(observation["remaining_steps"], ACTION_LIMIT)
        blob = json.dumps([observation, info])
        self.assertNotIn(str(SEED), blob)
        self.assertNotIn(str(self.CLEAN), blob)
        self.assertNotIn("issue", json.dumps(observation["manifest"]))

    def test_c03_manifest_matches_data(self):
        env, observation, _ = self.make(FIXTURE)
        manifest = observation["manifest"]
        for key in ("schema_version", "episode_id", "base_currency", "period_start",
                    "period_end", "timezone", "row_count", "document_count", "rounding"):
            self.assertIn(key, manifest)
        rows = self._walk(env, "ledger")
        docs = self._walk(env, "invoices") + self._walk(env, "payments")
        self.assertEqual(manifest["row_count"], len(rows))
        self.assertEqual(manifest["document_count"], len(docs))

    def test_closed_uses_given_dir_ids_and_order(self):
        env, observation, _ = self.make(FIXTURE)
        self.assertEqual(observation["manifest"]["episode_id"], "g0-partial")
        ids = [r["row_id"] for r in self._walk(env, "ledger")]
        with (FIXTURE / "ledger.csv").open(encoding="utf-8", newline="") as handle:
            on_disk = [row["row_id"] for row in csv.DictReader(handle)]
        self.assertEqual(ids, on_disk)

    def test_reset_starts_fresh_state(self):
        env, _, _ = self.make(FIXTURE)
        for _ in range(5):
            env.step(LEDGER_READ)
        env.step(read("submit", {"findings": expected_findings()}))
        observation, _ = env.reset(SEED, {"mode": "closed", "episode_dir": str(FIXTURE)})
        self.assertEqual(observation["remaining_steps"], ACTION_LIMIT)
        _, _, terminated, truncated, _ = env.step(LEDGER_READ)
        self.assertEqual((terminated, truncated), (False, False))

    def test_closed_requires_episode_dir(self):
        env = AuditEnv(score_episode=RecordingScorer())
        with self.assertRaises(ValueError):
            env.reset(0, {"mode": "closed"})

    def test_train_without_dir_uses_generator_callback(self):
        calls = []

        def generate(seed, config, output_dir):
            calls.append((seed, config))
            for name in os.listdir(FIXTURE):
                if not name.startswith("expected_"):
                    shutil.copy(FIXTURE / name, Path(output_dir) / name)

        env = AuditEnv(generate_episode=generate, score_episode=RecordingScorer())
        observation, _ = env.reset(7, {"mode": "train", "team_config": {"k": 1}})
        self.assertEqual(calls, [(7, {"k": 1})])
        self.assertEqual(observation["manifest"]["episode_id"], "g0-partial")
        env.close()

    @staticmethod
    def _walk(env, table):
        records, cursor = [], None
        while True:
            obs, *_ = env.step(read("list_records", {"table": table, "cursor": cursor, "limit": 50}))
            records += obs["result"]["records"]
            cursor = obs["result"]["next_cursor"]
            if cursor is None:
                return records


# ------------------------------------------------------------------ C04-C08


class ToolContract(EnvTestCase):
    def test_c04_list_records_step_shape(self):
        env, _, _ = self.make(self.CLEAN)
        observation, reward, terminated, truncated, _ = self.assertStepShape(
            env.step(read("list_records", {"table": "ledger", "cursor": None, "limit": 50})))
        self.assertEqual(reward, 0)
        self.assertFalse(terminated or truncated)
        self.assertEqual(set(observation["result"]), {"records", "next_cursor"})
        self.assertEqual(observation["remaining_steps"], ACTION_LIMIT - 1)

    def test_c05_pagination_reaches_all_rows_without_gaps(self):
        env, observation, _ = self.make(self.LARGE)
        seen, cursor, pages = [], None, 0
        while True:
            obs, *_ = self.assertStepShape(env.step(read(
                "list_records", {"table": "ledger", "cursor": cursor, "limit": 50})))
            page = obs["result"]["records"]
            self.assertLessEqual(len(page), PAGE_LIMIT)
            seen += [row["row_id"] for row in page]
            pages += 1
            cursor = obs["result"]["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(pages, 3)
        self.assertEqual(len(seen), observation["manifest"]["row_count"])
        self.assertEqual(len(seen), len(set(seen)))

    def test_c05_limit_above_50_is_capped(self):
        env, _, _ = self.make(self.LARGE)
        obs, *_ = env.step(read("list_records", {"table": "ledger", "limit": 1000}))
        self.assertEqual(len(obs["result"]["records"]), PAGE_LIMIT)

    def test_c06_same_cursor_same_page(self):
        env, _, _ = self.make(self.LARGE)
        first, *_ = env.step(read("list_records", {"table": "ledger", "cursor": None, "limit": 50}))
        cursor = first["result"]["next_cursor"]
        a, *_ = env.step(read("list_records", {"table": "ledger", "cursor": cursor, "limit": 50}))
        b, *_ = env.step(read("list_records", {"table": "ledger", "cursor": cursor, "limit": 50}))
        self.assertEqual(a["result"], b["result"])
        self.assertEqual(a["remaining_steps"] - 1, b["remaining_steps"])

    def test_every_table_listable(self):
        env, _, _ = self.make(FIXTURE)
        for table in TABLES:
            obs, *_ = env.step(read("list_records", {"table": table}))
            self.assertIsNone(obs["error"], table)
            self.assertTrue(obs["result"]["records"], table)

    def test_c07_get_record_exact(self):
        env, _, _ = self.make(FIXTURE)
        obs, *_ = self.assertStepShape(env.step(read("get_record", {"table": "ledger", "id": "row-06"})))
        row = obs["result"]
        self.assertEqual(row["row_id"], "row-06")
        self.assertEqual(row["allocation_minor"], 2000000)
        self.assertIs(type(row["credit_minor"]), int)
        self.assertIsNone(row["fx_source_minor"])  # CSV empty -> JSON null
        obs, *_ = env.step(read("get_record", {"table": "payments", "id": "pay-02"}))
        self.assertEqual(obs["result"]["doc_id"], "doc-pay-02")
        obs, *_ = env.step(read("get_record", {"table": "fx", "id": "2026-03-05:USD:KZT"}))
        self.assertEqual(obs["result"]["rate"], "500.00")
        self.assertIsInstance(obs["result"]["rate"], str)

    def test_c08_get_document_exact(self):
        env, _, _ = self.make(FIXTURE)
        obs, *_ = self.assertStepShape(env.step(read("get_document", {"doc_id": "doc-inv-01"})))
        self.assertEqual(obs["result"]["invoice_id"], "inv-01")
        self.assertEqual(obs["result"]["total_minor"], 12000000)

    def test_returned_objects_cannot_mutate_env(self):
        env, _, _ = self.make(FIXTURE)
        obs, *_ = env.step(read("get_record", {"table": "ledger", "id": "row-06"}))
        obs["result"]["allocation_minor"] = 7000000
        again, *_ = env.step(read("get_record", {"table": "ledger", "id": "row-06"}))
        self.assertEqual(again["result"]["allocation_minor"], 2000000)


# ------------------------------------------------------------------ C09-C14


class ErrorContract(EnvTestCase):
    def test_c09_calculator_via_step(self):
        env, _, _ = self.make(FIXTURE)
        obs, *_ = self.assertStepShape(env.step(read("calculator", {"expression": "0.1+0.2"})))
        self.assertEqual(obs["result"], "0.3")

    def test_c10_calculator_rejects_code(self):
        env, _, _ = self.make(FIXTURE)
        for expression in ("open('truth.json').read()", "__import__('os').system('id')",
                           "../truth.json", "1e999999", "2**999999"):
            obs, reward, terminated, truncated, _ = self.assertStepShape(
                env.step(read("calculator", {"expression": expression})))
            self.assertIsNone(obs["result"])
            self.assertTrue(obs["error"]["code"].startswith("calc_"))
            self.assertFalse(terminated or truncated)

    def _assert_error_consumes_step(self, action, code):
        env, _, _ = self.make(FIXTURE)
        obs, reward, terminated, truncated, _ = self.assertStepShape(env.step(action))
        self.assertIsNone(obs["result"])
        self.assertEqual(obs["error"]["code"], code)
        self.assertEqual(set(obs["error"]), {"code", "message"})
        self.assertEqual(obs["remaining_steps"], ACTION_LIMIT - 1)
        self.assertEqual(reward, 0)
        self.assertFalse(terminated or truncated)
        self.assertNotIn(str(FIXTURE), json.dumps(obs))
        return env

    def test_c11_unknown_record(self):
        self._assert_error_consumes_step(read("get_record", {"table": "ledger", "id": "row-99"}), "unknown_id")

    def test_c12_unknown_document(self):
        env = self._assert_error_consumes_step(read("get_document", {"doc_id": "../truth.json"}), "unknown_document")
        obs, *_ = env.step(read("get_document", {"doc_id": "truth.json"}))
        self.assertEqual(obs["error"]["code"], "unknown_document")

    def test_c13_unknown_action(self):
        self._assert_error_consumes_step(read("read_file", {"path": "truth.json"}), "unknown_action")

    def test_malformed_actions_are_controlled(self):
        for action, code in (
            ("list_records", "invalid_action"),
            (None, "invalid_action"),
            ({"name": "get_record", "args": []}, "invalid_args"),
            (read("list_records", {"table": "truth"}), "unknown_table"),
            (read("list_records", {"table": "ledger", "cursor": "ledger:-1"}), "invalid_cursor"),
            (read("list_records", {"table": "ledger", "cursor": "fx:0"}), "invalid_cursor"),
            (read("list_records", {"table": "ledger", "cursor": "ledger:9999"}), "invalid_cursor"),
            (read("list_records", {"table": "ledger", "limit": True}), "invalid_args"),
            (read("list_records", {"table": "ledger", "limit": 0}), "invalid_args"),
            (read("get_record", {"table": "ledger", "id": 1}), "invalid_args"),
            (read("calculator", {"expression": 5}), "calc_invalid_type"),
        ):
            with self.subTest(action=action):
                self._assert_error_consumes_step(action, code)

    def test_c14_error_costs_one_step_and_access_survives(self):
        env, _, _ = self.make(FIXTURE)
        a, *_ = env.step(LEDGER_READ)
        b, *_ = env.step(read("get_record", {"table": "ledger", "id": "missing"}))
        c, *_ = env.step(LEDGER_READ)
        self.assertEqual([a["remaining_steps"], b["remaining_steps"], c["remaining_steps"]], [99, 98, 97])
        self.assertEqual(a["result"], c["result"])


# ------------------------------------------------------------------ C15-C18


class TerminationContract(EnvTestCase):
    def test_c15_submit_is_final_and_frozen(self):
        scorer = RecordingScorer()
        env, _, _ = self.make(FIXTURE, scorer=scorer)
        env.step(LEDGER_READ)
        obs, reward, terminated, truncated, info = self.assertStepShape(
            env.step(read("submit", {"findings": expected_findings()})))
        self.assertEqual(obs["result"], {"accepted": True})
        self.assertEqual((reward, terminated, truncated), (0, True, False))
        frozen = env.outcome()
        score = env.final_score()
        for action in (LEDGER_READ, read("submit", {"findings": {"episode_id": "g0-partial", "findings": []}})):
            after = self.assertStepShape(env.step(action))
            self.assertEqual(after[0]["error"]["code"], "episode_finished")
            self.assertEqual(after[1:4], (0, True, False))
        self.assertEqual(env.outcome(), frozen)
        self.assertEqual(env.final_score(), score)
        self.assertEqual(len(scorer.calls), 1)
        self.assertEqual(frozen["inspection_steps"], 1)

    def test_c16_submit_as_action_100(self):
        scorer = RecordingScorer()
        env, _, _ = self.make(FIXTURE, scorer=scorer)
        for i in range(99):
            obs, reward, terminated, truncated, _ = env.step(LEDGER_READ)
            self.assertEqual((reward, terminated, truncated), (0, False, False))
        self.assertEqual(obs["remaining_steps"], 1)
        obs, reward, terminated, truncated, _ = env.step(read("submit", {"findings": expected_findings()}))
        self.assertEqual((reward, terminated, truncated), (0, True, False))
        self.assertEqual(obs["remaining_steps"], 0)
        self.assertEqual(env.outcome()["inspection_steps"], 99)
        self.assertEqual(env.final_score()["return"], scorer.value)
        self.assertEqual(scorer.calls[0][2], 99)

    def test_c17_100_inspections_truncate_with_empty_findings(self):
        env, _, _ = self.make(FIXTURE)
        for i in range(100):
            result = env.step(LEDGER_READ if i % 2 else read("nope"))
        _, reward, terminated, truncated, _ = result
        self.assertEqual((reward, terminated, truncated), (0, False, True))
        self.assertEqual(env.outcome()["findings"], {"episode_id": "g0-partial", "findings": []})
        late = env.step(read("submit", {"findings": expected_findings()}))
        self.assertEqual(late[0]["error"]["code"], "episode_finished")
        self.assertEqual(late[2:4], (False, True))
        self.assertEqual(env.outcome()["findings"]["findings"], [])

    def test_c18_time_limit_truncates_with_controlled_clock(self):
        clock = FakeClock()
        env, _, _ = self.make(FIXTURE, clock=clock)
        env.step(LEDGER_READ)
        clock.now += 59.9  # policy thinking time counts
        _, _, terminated, truncated, _ = env.step(LEDGER_READ)
        self.assertEqual((terminated, truncated), (False, False))
        clock.now += 0.1
        obs, reward, terminated, truncated, _ = env.step(read("submit", {"findings": expected_findings()}))
        self.assertEqual((reward, terminated, truncated), (0, False, True))
        self.assertEqual(obs["error"]["code"], "timeout")
        self.assertEqual(env.outcome()["findings"]["findings"], [])

    def test_c18_runner_truncate_hook(self):
        env, _, _ = self.make(FIXTURE)
        env.step(LEDGER_READ)
        obs, reward, terminated, truncated, _ = env.truncate()
        self.assertEqual((reward, terminated, truncated), (0, False, True))
        self.assertEqual(obs["error"]["code"], "timeout")
        self.assertEqual(env.truncate()[2:4], (False, True))
        self.assertEqual(env.outcome()["findings"]["findings"], [])

    def test_invalid_submit_structure_ends_episode(self):
        bad = expected_findings()
        bad["findings"] = bad["findings"] * 31
        env, _, _ = self.make(FIXTURE)
        obs, reward, terminated, truncated, _ = self.assertStepShape(env.step(read("submit", {"findings": bad})))
        self.assertEqual(obs["error"]["code"], "invalid_submit")
        self.assertEqual((reward, terminated, truncated), (0, True, False))
        for broken in (None, {"episode_id": "other", "findings": []},
                       {"episode_id": "g0-partial", "findings": [{"type": "x"}]}):
            env, _, _ = self.make(FIXTURE)
            obs, *_ = env.step(read("submit", {"findings": broken}))
            self.assertEqual(obs["error"]["code"], "invalid_submit")

    def test_submit_reply_does_not_reveal_correctness(self):
        replies = []
        for findings in (expected_findings(), {"episode_id": "g0-partial", "findings": []}):
            env, _, _ = self.make(FIXTURE)
            replies.append(env.step(read("submit", {"findings": findings})))
        self.assertEqual(replies[0], replies[1])


# ------------------------------------------------------------------ C19-C20


class BoundaryContract(EnvTestCase):
    def test_c19_closed_reward_is_numeric_zero_everywhere(self):
        scorer = RecordingScorer(value=0.992)
        env, _, info = self.make(FIXTURE, scorer=scorer)
        trajectory = [env.step(LEDGER_READ) for _ in range(3)]
        trajectory.append(env.step(read("submit", {"findings": expected_findings()})))
        for observation, reward, terminated, truncated, info in trajectory:
            self.assertIn(type(reward), (int, float))
            self.assertEqual(reward, 0)
            self.assertEqual(info, {})
            self.assertNotIn("0.992", json.dumps(observation))
        self.assertEqual(env.final_score()["return"], 0.992)  # trusted side only

    def test_train_returns_terminal_reward_only(self):
        scorer = RecordingScorer(value=0.998)
        env, _, _ = self.make(FIXTURE, mode="train", scorer=scorer)
        _, reward, *_ = env.step(LEDGER_READ)
        self.assertEqual(reward, 0)
        _, reward, terminated, _, info = env.step(read("submit", {"findings": expected_findings()}))
        self.assertTrue(terminated)
        self.assertAlmostEqual(reward, 0.998)
        self.assertNotIn("metrics", info)
        self.assertEqual(scorer.calls[0][2], 1)

    def test_c20_full_walk_has_no_private_data(self):
        for episode in (self.CLEAN, FIXTURE):
            with self.subTest(episode=episode.name):
                env, observation, info = self.make(episode)
                channel = [observation, info]
                for table in TABLES:
                    cursor = None
                    while True:
                        result = env.step(read("list_records", {"table": table, "cursor": cursor}))
                        channel.append(result)
                        cursor = result[0]["result"]["next_cursor"]
                        if cursor is None:
                            break
                for doc in ("doc-inv-01", "doc-pay-01", "doc-pay-02"):
                    channel.append(env.step(read("get_document", {"doc_id": doc})))
                for probe in (read("get_document", {"doc_id": "truth.json"}),
                              read("get_record", {"table": "truth", "id": "issue-01"}),
                              read("calculator", {"expression": "open('provenance.json')"}),
                              read("shell", {"cmd": "cat truth.json"}),
                              read("sql", {"query": "select * from truth"})):
                    result = env.step(probe)
                    self.assertIsNotNone(result[0]["error"])
                    channel.append(result)
                channel.append(env.step(read("submit", {"findings": {
                    "episode_id": observation["manifest"]["episode_id"], "findings": []}})))
                self._assert_clean(channel, episode)

    def _assert_clean(self, channel, episode):
        blob = json.dumps(channel, ensure_ascii=False)
        self.assertNotIn(str(episode), blob)
        self.assertNotIn(str(Path(episode).parent), blob)
        lowered = blob.lower()
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(f'"{marker}"', lowered, marker)  # never as a key or value
        self.assertNotIn("traceback", lowered)
        truth = json.loads((episode / "truth.json").read_text(encoding="utf-8"))
        for issue in truth["issues"]:
            self.assertNotIn(issue["issue_id"], blob)

    def test_env_never_opens_private_files(self):
        import builtins
        real_open = builtins.open
        opened = []

        def spy(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        from unittest import mock
        answer = expected_findings()  # read by the test, before spying
        with mock.patch("builtins.open", spy), mock.patch("pathlib.Path.open",
                lambda self, *a, **k: spy(str(self), *a, **k)):
            env = AuditEnv(score_episode=None)
            env.reset(SEED, {"mode": "closed", "episode_dir": str(FIXTURE)})
            for _ in range(3):
                env.step(LEDGER_READ)
            env.step(read("submit", {"findings": answer}))
        names = {Path(p).name for p in opened}
        self.assertFalse(names & {"truth.json", "provenance.json", "expected_findings.json",
                                  "expected_score.json"}, names)

    def test_policy_visible_action_names_are_the_contract(self):
        self.assertEqual(ACTIONS, ("list_records", "get_record", "get_document", "calculator", "submit"))


# ----------------------------------------------------- review regressions


class ReviewRegressions(EnvTestCase):
    def test_huge_numeric_cursor_is_controlled(self):
        env, _, _ = self.make(self.LARGE)
        for cursor in ("ledger:" + "1" * 5000, "ledger:" + "9" * 10, "ledger:0" + "0" * 20):
            obs, *_ = self.assertStepShape(env.step(read("list_records", {"table": "ledger", "cursor": cursor})))
            self.assertEqual(obs["error"]["code"], "invalid_cursor")

    def test_nan_or_infinity_in_submit_is_invalid_not_a_crash(self):
        for raw in ('{"episode_id":"g0-partial","findings":[],"x":NaN}',
                    '{"episode_id":"g0-partial","findings":[{"type":"payment_allocation",'
                    '"affected_ids":[],"claimed_amount_minor":Infinity,"currency":"KZT",'
                    '"evidence_ids":[],"explanation":""}]}'):
            env, _, _ = self.make(FIXTURE)
            obs, reward, terminated, truncated, _ = env.step(read("submit", {"findings": json.loads(raw)}))
            self.assertEqual(obs["error"]["code"], "invalid_submit")
            self.assertEqual((reward, terminated, truncated), (0, True, False))
            json.dumps(obs, allow_nan=False)
            self.assertIsNone(env.outcome()["findings"])

    def test_malformed_csv_raises_episode_load_error(self):
        from genesis_audit.env.loader import EpisodeLoadError
        cases = {
            "ledger.csv": lambda text: text.replace("row-03,", "row-03,extra,", 1),
            "fx.csv": lambda text: "date,source_currency,target_currency,rate\n,USD,KZT,500\n",
            "invoices.jsonl": lambda text: text + "[1,2]\n",
            "manifest.json": lambda text: "{not json",
        }
        for filename, mutate in cases.items():
            with self.subTest(file=filename), tempfile.TemporaryDirectory() as tmp:
                episode = Path(tmp) / "ep"
                shutil.copytree(FIXTURE, episode)
                path = episode / filename
                path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8")
                env = AuditEnv(score_episode=None)
                with self.assertRaises(EpisodeLoadError):
                    env.reset(0, {"mode": "closed", "episode_dir": str(episode)})

    def test_local_structure_check_agrees_with_dispatcher(self):
        from genesis_audit.env import submit as submit_module
        good = expected_findings()
        too_many = dict(good, findings=good["findings"] * 31)
        long_text = dict(good, findings=[dict(good["findings"][0], explanation="x" * 1001)])
        bool_amount = dict(good, findings=[dict(good["findings"][0], claimed_amount_minor=True)])
        for payload, valid in ((good, True), (too_many, False), (long_text, False),
                               (bool_amount, False), (None, False),
                               ({"episode_id": "other", "findings": []}, False)):
            with self.subTest(valid=valid):
                local = submit_module.local_structure_error(payload, "g0-partial")
                active = submit_module.structure_error(payload, "g0-partial")
                self.assertEqual(local is None, valid)
                self.assertEqual(active is None, valid)


if __name__ == "__main__":
    unittest.main()
