"""Build reference findings in the trusted process only."""

from pathlib import Path

from genesis_audit.contracts.files import public_evidence_ids, read_json
from genesis_audit.scoring.scorer import load_issues


def oracle_findings(episode_dir: Path) -> dict:
    episode_id = read_json(episode_dir, "manifest.json")["episode_id"]
    available = public_evidence_ids(episode_dir)
    findings = []
    for issue in sorted(load_issues(episode_dir), key=lambda item: item.issue_id):
        complete = [sorted(proof) for proof in issue.evidence_sets if proof <= available]
        if not complete:
            raise ValueError("cannot construct an oracle answer without public evidence")
        findings.append({
            "type": issue.type,
            "affected_ids": sorted(issue.affected_ids),
            "claimed_amount_minor": issue.amount_minor,
            "currency": issue.currency,
            "evidence_ids": min(complete, key=lambda proof: (len(proof), proof)),
            "explanation": "Complete public evidence set supplied by trusted oracle.",
        })
    return {"episode_id": episode_id, "findings": findings}
