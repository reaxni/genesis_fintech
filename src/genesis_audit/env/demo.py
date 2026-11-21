"""Record a readable demo trajectory of a scripted policy (trusted side).

    PYTHONPATH=src python -m genesis_audit.env.demo \
        tests/integration/fixtures/g0_partial_payment --policy reconciliation \
        [--output trajectory.jsonl] [--mode closed]

The policy runs in-process here for convenience; the isolated run goes
through the Compose runner. Each JSONL line is one public exchange: the
action and the observation/reward/flags returned to the policy. The final
line is a trusted summary (outcome and, if a scorer is available, score);
it is not part of what the policy sees.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from random import Random

from genesis_audit.policies import POLICIES, choose_action

from .core import AuditEnv


def _scorer():
    try:
        from genesis_audit.scoring import score_episode  # role C, optional here
        return score_episode
    except Exception:
        return None


def run(episode_dir: str, policy: str, mode: str = "closed", seed: int = 0):
    env = AuditEnv(score_episode=_scorer())
    observation, _ = env.reset(seed, {"mode": mode, "episode_dir": episode_dir, "team_config": {}})
    rng = Random(0)
    history = []
    lines = []
    terminated = truncated = False
    while not (terminated or truncated):
        act = choose_action(policy, deepcopy(observation), deepcopy(history), rng=rng)
        history.append({"observation": observation, "action": act})
        observation, reward, terminated, truncated, _ = env.step(act)
        lines.append({"step": len(history), "action": act, "observation": observation,
                      "reward": reward, "terminated": terminated, "truncated": truncated})
    summary = {"trusted_summary": env.outcome(), "score": env.final_score()}
    return lines, summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("episode_dir")
    parser.add_argument("--policy", default="reconciliation", choices=sorted(POLICIES))
    parser.add_argument("--mode", default="closed", choices=("closed", "train"))
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    lines, summary = run(args.episode_dir, args.policy, args.mode)
    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        for line in lines + [summary]:
            out.write(json.dumps(line, ensure_ascii=False) + "\n")
    finally:
        if out is not sys.stdout:
            out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
