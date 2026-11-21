"""Role B scripted policies.

Entry point (G0): choose_action(policy_name, observation, history, *, rng).
Names: "random", "reconciliation", "mark_all". Each returns one public
step action {"name", "args"}. Runs in the isolated policy container, so it
imports only the standard library and genesis_audit.contracts.v1.
"""

from __future__ import annotations

from typing import Any, List

from genesis_audit.contracts.v1 import JSONDict

from . import mark_all, random_policy, reconciliation

POLICIES = {
    "random": random_policy.choose,
    "reconciliation": reconciliation.choose,
    "mark_all": mark_all.choose,
}

__all__ = ["choose_action", "POLICIES"]


def choose_action(policy_name: str, observation: JSONDict, history: List[JSONDict], *, rng: Any) -> JSONDict:
    try:
        policy = POLICIES[policy_name]
    except KeyError:
        raise ValueError(f"unknown policy {policy_name!r}; expected one of {sorted(POLICIES)}") from None
    return policy(observation, history, rng)
