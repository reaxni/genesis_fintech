"""Stable G0 names and types. This module is safe to import in a policy."""

from collections.abc import Callable
from typing import Any, Protocol

SCHEMA_VERSION = "1.0"
ACTION_LIMIT = 100
PAGE_LIMIT = 50
TIME_LIMIT_SECONDS = 60
FINDING_LIMIT = 30
EVIDENCE_LIMIT = 10
EXPLANATION_LIMIT = 1000
ISSUE_TYPES = ("duplicate_invoice", "payment_allocation", "fx_conversion", "period_cutoff")
TABLES = ("ledger", "invoices", "payments", "fx")
ACTIONS = ("list_records", "get_record", "get_document", "calculator", "submit")

JSONDict = dict[str, Any]
StepResult = tuple[JSONDict, float, bool, bool, JSONDict]
GenerateEpisode = Callable[[int, JSONDict, str], None]
ScoreEpisode = Callable[[str, JSONDict, int], JSONDict]


class Environment(Protocol):
    def reset(self, seed: int, config: JSONDict) -> tuple[JSONDict, JSONDict]: ...

    def step(self, action: JSONDict) -> StepResult: ...


class ManagedEnvironment(Environment, Protocol):
    def truncate(self) -> StepResult:
        """Finish a timed-out episode with no findings. Callable only by the runner."""
        ...


def fx_evidence_id(record: JSONDict) -> str:
    """Preserve the business key when ordinary identifiers are remapped."""
    return ":".join(record[key] for key in ("date", "source_currency", "target_currency"))
