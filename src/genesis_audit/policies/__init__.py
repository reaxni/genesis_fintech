"""Role B scripted policies.

Entry point (G0): choose_action(policy_name, observation, history, *, rng).
Names: "random", "reconciliation", "reconciliation_aggregate", "mark_all". Each returns one public
step action {"name", "args"}. Runs in the isolated policy container, so it
imports only the standard library and genesis_audit.contracts.v1.
"""

from __future__ import annotations

from functools import partial
from typing import Any, List

from genesis_audit.contracts.v1 import JSONDict

from . import mark_all, random_policy, reconciliation

POLICIES = {
    "random": random_policy.choose,
    "reconciliation": reconciliation.choose,
    "reconciliation_aggregate": partial(reconciliation.choose, enhanced=True),
    "mark_all": mark_all.choose,
}

DEMO_PLANS = {
    name: partial(reconciliation.choose_budgeted, order=order)
    for name, order in reconciliation.DEMO_ORDERS.items()
}

__all__ = ["choose_action", "POLICIES"]


def choose_action(policy_name: str, observation: JSONDict, history: List[JSONDict], *, rng: Any) -> JSONDict:
    policy = POLICIES.get(policy_name) or DEMO_PLANS.get(policy_name)
    if policy is None:
        raise ValueError(f"unknown policy {policy_name!r}; expected one of {sorted(POLICIES | DEMO_PLANS)}")
    return policy(observation, history, rng)
