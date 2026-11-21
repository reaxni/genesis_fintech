"""Maximum matching with the official deterministic tie-break."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from .validation import Finding

Edge = tuple[str, int]


@dataclass(frozen=True)
class Issue:
    issue_id: str
    type: str
    affected_ids: frozenset[str]
    amount_minor: int
    currency: str
    evidence_sets: tuple[frozenset[str], ...]


def _cardinality(edges: Collection[Edge]) -> int:
    """Find maximum cardinality through augmenting paths."""
    neighbours: dict[str, list[int]] = {}
    for issue_id, finding_index in edges:
        neighbours.setdefault(issue_id, []).append(finding_index)
    owners: dict[int, str] = {}

    def augment(issue_id: str, visited: set[int]) -> bool:
        for index in neighbours[issue_id]:
            if index in visited:
                continue
            visited.add(index)
            if index not in owners or augment(owners[index], visited):
                owners[index] = issue_id
                return True
        return False

    for issue_id in neighbours:
        augment(issue_id, set())
    return len(owners)


def select_pairs(edges: Collection[Edge]) -> tuple[Edge, ...]:
    """Return the lexicographically smallest sorted maximum matching."""
    remaining = sorted(set(edges))
    target = _cardinality(remaining)
    selected: list[Edge] = []

    while target:
        for position, (issue_id, index) in enumerate(remaining):
            later = [
                edge for edge in remaining[position + 1:]
                if edge[0] != issue_id and edge[1] != index
            ]
            if _cardinality(later) == target - 1:
                selected.append((issue_id, index))
                remaining = later
                target -= 1
                break
        else:
            raise AssertionError("maximum matching became infeasible")
    return tuple(selected)


def match_findings(
    issues: tuple[Issue, ...],
    findings: tuple[Finding, ...],
    available: frozenset[str],
    tolerances: Mapping[str, int],
) -> tuple[Edge, ...]:
    edges = [
        (issue.issue_id, index)
        for issue in issues
        for index, finding in enumerate(findings)
        if (
            issue.type == finding.type
            and issue.affected_ids == finding.affected_ids
            and issue.currency == finding.currency
            and abs(issue.amount_minor - finding.amount_minor) <= tolerances.get(issue.type, 0)
            and finding.evidence_ids <= available
            and any(proof <= finding.evidence_ids for proof in issue.evidence_sets)
        )
    ]
    return select_pairs(edges)
