"""Train a contextual bandit to choose a page order and evaluate it on fresh seeds."""

from collections import Counter, defaultdict
from pathlib import Path
from random import Random
from statistics import fmean
from tempfile import TemporaryDirectory

from judge_adapter import generate_episode, make_env, score_episode
from genesis_audit.contracts.files import read_json
from genesis_audit.contracts.v1 import ISSUE_TYPES
from genesis_audit.data.corpus import TIMEZONES
from genesis_audit.policies import DEMO_PLANS
from genesis_audit.policies.reconciliation import DEMO_PAGE_BUDGET
from genesis_audit.verification import verify_episode

from .session import run_episode

BASELINE_PLAN = "ledger_docs"
FULL_POLICIES = ("reconciliation", "reconciliation_aggregate")
LARGE_JOURNAL = 375
TRAIN_SEED = 84_010_000
TEST_SEED = 97_050_000
BALANCED_SEED = 98_010_000


def _cases(root, seed_base):
    cases = []
    for number in range(1, 21):
        seed = seed_base + number
        rng = Random(seed)
        directory = root / f"D{number:02d}"
        generate_episode(seed, {
            "scenario": directory.name,
            "profile": "diverse",
            "invoice_count": rng.randint(50, 80),
            "timezone": rng.choice(TIMEZONES),
        }, directory)
        verify_episode(directory)
        manifest = read_json(directory, "manifest.json")
        context = "large" if manifest["row_count"] > LARGE_JOURNAL else "small"
        cases.append((directory, manifest["episode_id"], context))
    return cases


def _play(listener, directory, policy, mode):
    observations = []

    def record(step):
        observations.append(step["reward"])

    with listener.episode(policy) as choose:
        result = run_episode(make_env(), episode_dir=directory, seed=0,
                             choose=choose, score=score_episode, record=record, mode=mode)
    if not result["terminated"] or result["timed_out"] or any(observations[:-1]):
        raise ValueError("unexpected reward or completion in the review demo")
    if policy in DEMO_PLANS and result["inspection_steps"] > DEMO_PAGE_BUDGET:
        raise ValueError("review demo exceeded its page budget")
    return result, observations[-1]


def _summary(results):
    return {
        "episodes": len(results),
        **{key: sum(row[key] for row in results) for key in ("tp", "fp", "fn")},
        "mean_return": fmean(row["return"] for row in results),
        "mean_inspections": fmean(row["inspection_steps"] for row in results),
    }


def _balanced_holdout(listener, root, record, used_ids):
    counts, results = Counter(), defaultdict(list)
    for number in range(8):
        seed = BALANCED_SEED + number
        rng = Random(seed)
        directory = root / str(number)
        generate_episode(seed, {
            "errors": {kind: 3 for kind in ISSUE_TYPES},
            "invoice_count": rng.randint(50, 80),
            "timezone": rng.choice(TIMEZONES),
        }, directory)
        facts = verify_episode(directory)
        episode_id = read_json(directory, "manifest.json")["episode_id"]
        if episode_id in used_ids or Counter(f.kind for f in facts) != dict.fromkeys(ISSUE_TYPES, 3):
            raise ValueError("invalid balanced holdout episode")
        used_ids.add(episode_id)
        counts.update(f.kind for f in facts)
        for policy in FULL_POLICIES:
            result, _ = _play(listener, directory, policy, "closed")
            results[policy].append(result)
            record({"phase": "balanced_holdout", "policy": policy,
                    **{k: result[k] for k in ("episode_id", "tp", "fp", "fn", "return", "inspection_steps")}})
    return {"issues_by_type": dict(counts),
            "policies": {policy: _summary(rows) for policy, rows in results.items()}}


def run_review_demo(listener, record, release_episode_ids):
    rewards = defaultdict(list)
    plans = tuple(DEMO_PLANS)
    with TemporaryDirectory(prefix="genesis-review-") as workspace:
        root = Path(workspace)
        train = _cases(root / "train", TRAIN_SEED)
        holdout = _cases(root / "holdout", TEST_SEED)
        train_ids = {episode_id for _, episode_id, _ in train}
        holdout_ids = {episode_id for _, episode_id, _ in holdout}
        used_ids = train_ids | holdout_ids | set(release_episode_ids)
        if len(used_ids) != 40 + len(release_episode_ids):
            raise ValueError("training, holdout and release IDs must be disjoint")

        for directory, episode_id, context in train:
            for plan in plans:
                _, reward = _play(listener, directory, plan, "train")
                rewards[context, plan].append(reward)
                record({"phase": "train", "episode_id": episode_id, "context": context,
                        "plan": plan, "observed_reward": reward})

        # Each plan is sampled once per train episode. Freeze the per-context means
        # and the greedy selection before running any held-out episode.
        contexts = sorted({context for _, _, context in train})
        means = {context: {plan: fmean(rewards[context, plan]) for plan in plans}
                 for context in contexts}
        learned = {context: max(values, key=values.get) for context, values in means.items()}
        evaluations = defaultdict(list)
        for directory, _, context in holdout:
            chosen = learned.get(context, BASELINE_PLAN)
            policies = {name: name for name in plans + FULL_POLICIES}
            policies["learned"] = chosen
            for label, plan in policies.items():
                result, _ = _play(listener, directory, plan, "closed")
                evaluations[label].append(result)
                record({"phase": "holdout", "context": context, "policy": label, "plan": plan,
                        **{k: result[k] for k in ("episode_id", "tp", "fp", "fn", "return", "inspection_steps")}})

        balanced = _balanced_holdout(listener, root / "balanced", record, used_ids)

    report = {"phase": "summary", "train_seed_base": TRAIN_SEED, "holdout_seed_base": TEST_SEED,
              "balanced_seed_base": BALANCED_SEED, "balanced_holdout": balanced,
              "train_episodes": len(train), "page_budget": DEMO_PAGE_BUDGET,
              "fixed_baseline": BASELINE_PLAN, "large_journal_over_rows": LARGE_JOURNAL,
              "train_means": means, "learned_plans": learned,
              "holdout": {policy: _summary(rows) for policy, rows in evaluations.items()}}
    record(report)
    return report
