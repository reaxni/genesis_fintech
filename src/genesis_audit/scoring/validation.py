"""Structure checks precede any set normalization."""

from dataclasses import dataclass

from genesis_audit.contracts.v1 import EVIDENCE_LIMIT, EXPLANATION_LIMIT, FINDING_LIMIT


class InvalidSubmit(ValueError):
    """The payload is structurally invalid."""


@dataclass(frozen=True)
class Finding:
    type: str
    affected_ids: frozenset[str]
    amount_minor: int
    currency: str
    evidence_ids: frozenset[str]


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def parse_findings(payload: object, episode_id: str) -> tuple[Finding, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("episode_id"), str):
        raise InvalidSubmit("missing or invalid episode_id")
    if payload["episode_id"] != episode_id:
        raise InvalidSubmit("episode_id does not match the current episode")
    records = payload.get("findings")
    if not isinstance(records, list) or len(records) > FINDING_LIMIT:
        raise InvalidSubmit("findings must be a list of at most 30 entries")
    result = []
    for record in records:
        if not isinstance(record, dict):
            raise InvalidSubmit("finding must be an object")
        explanation = record.get("explanation")
        if not isinstance(explanation, str) or len(explanation) > EXPLANATION_LIMIT:
            raise InvalidSubmit("explanation must be a string of at most 1000 characters")
        if not all(isinstance(record.get(key), str) for key in ("type", "currency")):
            raise InvalidSubmit("type and currency must be strings")
        amount = record.get("claimed_amount_minor")
        if type(amount) is not int:
            raise InvalidSubmit("claimed_amount_minor must be an integer")
        affected = record.get("affected_ids")
        evidence = record.get("evidence_ids")
        if not _is_string_list(affected):
            raise InvalidSubmit("affected_ids must be a list of strings")
        if not _is_string_list(evidence) or len(evidence) > EVIDENCE_LIMIT:
            raise InvalidSubmit("evidence_ids must be a list of at most 10 strings")
        result.append(Finding(
            record["type"], frozenset(affected), amount,
            record["currency"], frozenset(evidence),
        ))
    return tuple(result)
