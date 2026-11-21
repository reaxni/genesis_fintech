"""The three public entry points consumed by the trusted Genesis judge."""

from genesis_audit.scoring import score_episode

__all__ = ["generate_episode", "make_env", "score_episode"]


def generate_episode(seed: int, config: dict, output_dir: str) -> None:
    from genesis_audit.data import generate_episode as generate

    generate(seed, config, output_dir)


def make_env():
    from genesis_audit.env import AuditEnv

    return AuditEnv(generate_episode=generate_episode, score_episode=score_episode)
