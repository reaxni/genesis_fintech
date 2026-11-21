"""Run one episode, keeping raw scoring on the trusted side."""

from collections.abc import Callable
from pathlib import Path
from time import monotonic

from genesis_audit.contracts.files import read_json
from genesis_audit.contracts.v1 import TIME_LIMIT_SECONDS, JSONDict, ManagedEnvironment, ScoreEpisode

ChooseAction = Callable[[JSONDict, float], JSONDict]


def run_episode(
    env: ManagedEnvironment,
    *,
    episode_dir: Path,
    seed: int,
    choose: ChooseAction,
    score: ScoreEpisode,
    record: Callable[[dict], None],
    mode: str = "closed",
    clock: Callable[[], float] = monotonic,
) -> dict:
    start = clock()
    deadline = start + TIME_LIMIT_SECONDS
    episode_id = read_json(episode_dir, "manifest.json")["episode_id"]
    observation, _ = env.reset(seed, {"mode": mode, "episode_dir": str(episode_dir), "team_config": {}})
    steps = 0
    inspections = 0
    findings = {"episode_id": episode_id, "findings": []}
    terminated = truncated = False
    timed_out = False
    while not (terminated or truncated):
        try:
            if clock() >= deadline:
                raise TimeoutError
            action = choose(observation, deadline)
            if clock() >= deadline:
                raise TimeoutError
        except TimeoutError:
            observation, reward, terminated, truncated, _ = env.truncate()
            if terminated or not truncated:
                raise ValueError("timeout did not truncate the environment")
            timed_out = True
            recorded_action = {"name": "timeout", "args": {}}
        else:
            observation, reward, terminated, truncated, _ = env.step(action)
            steps += 1
            is_submit = action.get("name") == "submit"
            if is_submit and terminated:
                args = action.get("args")
                findings = args.get("findings") if isinstance(args, dict) else None
            elif not is_submit:
                inspections += 1
            recorded_action = action
        if mode == "closed" and (type(reward) not in (int, float) or reward != 0):
            raise ValueError("closed environment returned a nonzero or nonnumeric reward")
        record({
            "episode_id": episode_id, "step": steps, "action": recorded_action,
            "observation": observation, "reward": reward,
            "terminated": terminated, "truncated": truncated,
        })
    return {
        **score(str(episode_dir), findings, inspections),
        "episode_id": episode_id, "inspection_steps": inspections, "steps": steps,
        "elapsed_seconds": clock() - start, "terminated": terminated, "truncated": truncated,
        "timed_out": timed_out,
    }
