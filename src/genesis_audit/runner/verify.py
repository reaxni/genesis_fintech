"""Trusted CLI for the open release corpus and one train demo."""

import argparse
import json
from pathlib import Path
from statistics import fmean

from judge_adapter import make_env, score_episode
from genesis_audit.contracts.files import read_json

from .oracle import oracle_findings
from .remote_policy import PolicyListener
from .session import run_episode

POLICIES = ("random", "reconciliation", "mark_all")


def corpus(root: Path) -> list[Path]:
    result = []
    for split in ("train", "dev"):
        directories = sorted(path.parent for path in (root / split).glob("*/manifest.json"))
        if len(directories) != 10:
            raise ValueError(f"expected 10 {split} episodes, got {len(directories)}")
        result.extend(directories)
    return result


def write_line(file, record):
    file.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def verify(episodes: Path, output: Path, socket_path: Path) -> None:
    release_corpus = corpus(episodes)
    ids = [read_json(path, "manifest.json")["episode_id"] for path in release_corpus]
    if len(set(ids)) != len(ids):
        raise ValueError("release corpus contains repeated episode_id values")
    output.mkdir(parents=True, exist_ok=True)
    results = {policy: [] for policy in ("oracle", *POLICIES)}
    train_episode = release_corpus[0]
    has_payment_demo = False
    with (
        PolicyListener(socket_path) as listener,
        (output / "trajectories.jsonl").open("w", encoding="utf-8") as trajectory,
        (output / "metrics.jsonl").open("w", encoding="utf-8") as metrics,
        (output / "train_demo.jsonl").open("w", encoding="utf-8") as demo,
    ):
        for episode in release_corpus:
            # Oracle stays in this process and submits through the same contract.
            findings = oracle_findings(episode)
            if not has_payment_demo and episode.parent.name == "train" and any(
                finding["type"] == "payment_allocation" for finding in findings["findings"]
            ):
                train_episode = episode
                has_payment_demo = True
            result = run_episode(
                make_env(), episode_dir=episode, seed=0,
                choose=lambda _obs, _deadline: {"name": "submit", "args": {"findings": findings}},
                score=score_episode,
                record=lambda row: write_line(trajectory, {"policy": "oracle", **row}),
            )
            if result["fp"] != 0 or result["fn"] != 0 or not result["terminated"]:
                raise ValueError("oracle did not reproduce the trusted issue set")
            results["oracle"].append(result)
            write_line(metrics, {"policy": "oracle", **result})
            for policy in POLICIES:
                with listener.episode(policy) as choose:
                    result = run_episode(
                        make_env(), episode_dir=episode, seed=0, choose=choose,
                        score=score_episode, record=lambda row: write_line(
                            trajectory, {"policy": policy, **row}
                        ),
                    )
                results[policy].append(result)
                write_line(metrics, {"policy": policy, **result})
                if result["timed_out"]:
                    raise TimeoutError(f"{policy} exceeded the episode deadline")
        with listener.episode("reconciliation") as choose:
            result = run_episode(
                make_env(), episode_dir=train_episode, seed=0, choose=choose,
                score=score_episode, mode="train", record=lambda row: write_line(demo, row),
            )
        write_line(metrics, {"policy": "reconciliation", "mode": "train", **result})
        if result["timed_out"]:
            raise TimeoutError("train demo exceeded the episode deadline")

    for policy, outcomes in results.items():
        correct = sum(outcome["tp"] or 0 for outcome in outcomes)
        false_positives = sum(outcome["fp"] or 0 for outcome in outcomes)
        missed = sum(outcome["fn"] or 0 for outcome in outcomes)
        invalid = sum(outcome["f1"] is None for outcome in outcomes)
        mean_return = fmean(outcome["return"] for outcome in outcomes)
        print(f"{policy}: episodes={len(outcomes)}, TP={correct}, FP={false_positives}, "
              f"FN={missed}, invalid={invalid}, mean_return={mean_return:.3f}")
    print(f"Train demo: episode={result['episode_id']}, steps={result['steps']}, "
          f"return={result['return']:.3f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    args = parser.parse_args()
    verify(args.episodes, args.output, args.socket)


if __name__ == "__main__":
    main()
