"""Public structural check of a submit payload.

This only says whether the *shape* is valid (types, required keys, the
30/10/1000 limits, episode_id). It never compares with truth, so the reply
cannot leak whether a finding is correct.

When role C's scorer is installed, its own `parse_findings` is the single
source of truth, so the reply to the policy can never disagree with the
scorer's verdict. The local check is only a fallback for running the env
without the scoring package (unit tests before PR 2 is merged).
"""

from __future__ import annotations

from typing import Any, Optional

from genesis_audit.contracts.v1 import EVIDENCE_LIMIT, EXPLANATION_LIMIT, FINDING_LIMIT

try:  # role C, trusted side
    from genesis_audit.scoring.validation import InvalidSubmit, parse_findings
except ImportError:  # pragma: no cover - depends on which PRs are merged
    InvalidSubmit = None  # type: ignore[assignment,misc]
    parse_findings = None  # type: ignore[assignment]

REQUIRED_KEYS = (
    "type",
    "affected_ids",
    "claimed_amount_minor",
    "currency",
    "evidence_ids",
    "explanation",
)


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def structure_error(payload: Any, episode_id: str) -> Optional[str]:
    """Return a short public reason, or None when the structure is valid."""
    if parse_findings is not None:
        try:
            parse_findings(payload, episode_id)
        except InvalidSubmit as error:
            return str(error)
        return None
    return local_structure_error(payload, episode_id)


def local_structure_error(payload: Any, episode_id: str) -> Optional[str]:
    """Fallback mirror of the scorer's structural rules."""
    if not isinstance(payload, dict):
        return "findings must be an object"
    if not isinstance(payload.get("episode_id"), str):
        return "episode_id must be a string"
    if payload["episode_id"] != episode_id:
        return "episode_id does not match the current episode"
    records = payload.get("findings")
    if not isinstance(records, list):
        return "findings.findings must be a list"
    if len(records) > FINDING_LIMIT:
        return f"at most {FINDING_LIMIT} findings are allowed"
    for position, record in enumerate(records):
        if not isinstance(record, dict):
            return f"finding {position} must be an object"
        missing = [key for key in REQUIRED_KEYS if key not in record]
        if missing:
            return f"finding {position} misses required fields"
        if not isinstance(record["type"], str) or not isinstance(record["currency"], str):
            return f"finding {position}: type and currency must be strings"
        if type(record["claimed_amount_minor"]) is not int:  # bool is rejected
            return f"finding {position}: claimed_amount_minor must be an integer"
        if not _string_list(record["affected_ids"]):
            return f"finding {position}: affected_ids must be a list of strings"
        if not _string_list(record["evidence_ids"]):
            return f"finding {position}: evidence_ids must be a list of strings"
        if len(record["evidence_ids"]) > EVIDENCE_LIMIT:
            return f"finding {position}: at most {EVIDENCE_LIMIT} evidence ids"
        explanation = record["explanation"]
        if not isinstance(explanation, str) or len(explanation) > EXPLANATION_LIMIT:
            return f"finding {position}: explanation must be a string of at most {EXPLANATION_LIMIT} characters"
    return None
