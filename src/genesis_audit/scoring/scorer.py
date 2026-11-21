"""Load a trusted episode and score one final payload."""

import logging
from pathlib import Path

from genesis_audit.contracts.files import public_evidence_ids, read_json
from genesis_audit.contracts.v1 import EVIDENCE_LIMIT, ISSUE_TYPES

from .matcher import Issue, match_findings
from .reward import invalid_metrics, metrics
from .validation import InvalidSubmit, parse_findings

logger = logging.getLogger(__name__)


def _trusted_ids(values: object, *, limit: int | None = None) -> frozenset[str]:
    if not isinstance(values, list) or not values:
        raise ValueError("trusted identifiers must be a nonempty list")
    if limit is not None and len(values) > limit:
        raise ValueError("trusted evidence set exceeds the proof limit")
    if not all(isinstance(item, str) and item for item in values):
        raise ValueError("trusted identifiers must be nonempty strings")
    return frozenset(values)


def load_issues(directory: Path) -> tuple[Issue, ...]:
    records = read_json(directory, "truth.json")["issues"]
    if not isinstance(records, list):
        raise ValueError("trusted issues must be a list")
    seen: set[str] = set()
    issues = []
    for record in records:
        issue_id = record["issue_id"]
        if not isinstance(issue_id, str) or not issue_id or issue_id in seen:
            raise ValueError("trusted issue_id must be a unique nonempty string")
        seen.add(issue_id)
        kind = record["type"]
        amount = record["expected_amount_minor"]
        currency = record["currency"]
        proofs = record["evidence_sets"]
        if kind not in ISSUE_TYPES or type(amount) is not int or amount < 0:
            raise ValueError("invalid trusted type or amount")
        if not isinstance(currency, str) or not currency:
            raise ValueError("invalid trusted currency")
        if not isinstance(proofs, list) or not proofs:
            raise ValueError("trusted issue must contain a proof")
        issues.append(Issue(
            issue_id, kind, _trusted_ids(record["affected_ids"]), amount, currency,
            tuple(_trusted_ids(proof, limit=EVIDENCE_LIMIT) for proof in proofs),
        ))
    return tuple(issues)


def score_episode(episode_dir: str, findings: dict, inspection_steps: int) -> dict:
    if type(inspection_steps) is not int or inspection_steps < 0:
        raise ValueError("inspection_steps must be a nonnegative integer")

    directory = Path(episode_dir)
    episode_id = read_json(directory, "manifest.json")["episode_id"]
    try:
        parsed = parse_findings(findings, episode_id)
    except InvalidSubmit as error:
        logger.warning("Invalid submit: %s", error)
        return invalid_metrics()

    tolerances = read_json(directory, "policy.json").get("amount_tolerance_minor", {})
    if not isinstance(tolerances, dict) or any(
        key not in ISSUE_TYPES or type(value) is not int or value < 0
        for key, value in tolerances.items()
    ):
        raise ValueError("invalid public amount tolerance")

    issues = load_issues(directory)
    available = public_evidence_ids(directory)
    if any(not any(proof <= available for proof in issue.evidence_sets) for issue in issues):
        raise ValueError("trusted issue lacks a complete publicly addressable proof")
    tp = len(match_findings(issues, parsed, available, tolerances))
    return metrics(tp, len(parsed) - tp, len(issues) - tp, inspection_steps)
