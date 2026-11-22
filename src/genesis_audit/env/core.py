"""AuditEnv: reset/step over one episode with the public tool contract.

Trusted side only. The policy never imports this module; it talks to it
through the runner's JSONL broker and sees only observations returned by
`reset` and `step`.

Contract (G0 v1.0 + check_definitions):
  reset(seed, config) -> (observation, info)
      config = {"mode": "closed"|"train", "episode_dir": str?, "team_config": {}}
      first observation = {"task", "manifest", "policy", "remaining_steps": 100}
  step(action) -> (observation, reward, terminated, truncated, info)
      action = {"name": <one of ACTIONS>, "args": {...}}
      observation = {"result": value|null, "error": null|{"code","message"},
                     "remaining_steps": int}
  truncate() -> same tuple; runner-only timeout hook.

Budget rules:
  * every step() call, erroneous ones included, uses one of 100 actions;
  * submit is allowed as action 100 and is not an inspection step;
  * 100 inspections without submit -> truncated, empty findings;
  * 60 s since reset (policy time included) -> truncated, empty findings;
  * after the episode ends the outcome is frozen.
Reward: always 0 before the end; in closed mode always numeric 0; in train
mode the terminal step carries score_episode(...)["return"].
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from genesis_audit.contracts.v1 import (
    ACTION_LIMIT,
    ACTIONS,
    PAGE_LIMIT,
    TABLES,
    TIME_LIMIT_SECONDS,
    GenerateEpisode,
    JSONDict,
    ScoreEpisode,
    StepResult,
)

from .calculator import try_evaluate
from .loader import EpisodeIndex, load_episode
from .submit import structure_error

MODES = ("closed", "train")

TASK = (
    "Audit the bookkeeping of one synthetic company for one reporting period. "
    "Find duplicate invoices, missing or partial payment allocations, wrong FX "
    "rate or date, and wrong reporting period. Use list_records, get_record, "
    "get_document and calculator to collect evidence, then call submit once "
    "with findings {episode_id, findings:[{type, affected_ids, "
    "claimed_amount_minor, currency, evidence_ids, explanation}]}. "
    "Issue types: duplicate_invoice, payment_allocation, fx_conversion, "
    "period_cutoff. Budget: 100 actions and 60 seconds; each action, including "
    "a failed one, uses a step."
)


class ToolError(Exception):
    """A controlled, public error. The step is still consumed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)
        self.code = code
        self.message = message


def _public(value: Any) -> Any:
    """Deep copy through strict JSON so the policy never shares our objects."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _empty_findings(episode_id: str) -> JSONDict:
    return {"episode_id": episode_id, "findings": []}


class AuditEnv:
    """Gymnasium-style environment over one Genesis episode."""

    def __init__(
        self,
        *,
        generate_episode: Optional[GenerateEpisode] = None,
        score_episode: Optional[ScoreEpisode] = None,
        clock: Callable[[], float] = time.monotonic,
        time_limit: float = TIME_LIMIT_SECONDS,
    ) -> None:
        self._generate = generate_episode
        self._score = score_episode
        self._clock = clock
        self._time_limit = time_limit
        self._temp_dir: Optional[str] = None
        self._clear_state()

    # ------------------------------------------------------------------ state

    def _clear_state(self) -> None:
        self._episode: Optional[EpisodeIndex] = None
        self._episode_dir: Optional[str] = None
        self._mode = "closed"
        self._started = 0.0
        self._steps = 0
        self._inspections = 0
        self._done = False
        self._terminated = False
        self._truncated = False
        self._final_findings: Optional[JSONDict] = None
        self._final_observation: Optional[JSONDict] = None
        self._score_cache: Optional[JSONDict] = None

    def _drop_temp_dir(self) -> None:
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None

    def close(self) -> None:
        self._drop_temp_dir()

    def __del__(self) -> None:  # pragma: no cover - best effort cleanup
        try:
            self._drop_temp_dir()
        except Exception:
            pass

    # ------------------------------------------------------------------ reset

    def reset(self, seed: int = 0, config: Optional[JSONDict] = None) -> Tuple[JSONDict, JSONDict]:
        config = dict(config or {})
        mode = config.get("mode", "closed")
        if mode not in MODES:
            raise ValueError("config.mode must be 'closed' or 'train'")
        episode_dir = config.get("episode_dir")

        self._drop_temp_dir()
        self._clear_state()

        if episode_dir is None:
            if mode == "closed":
                raise ValueError("closed mode requires config.episode_dir")
            if self._generate is None:
                raise ValueError("train mode without episode_dir needs generate_episode")
            self._temp_dir = tempfile.mkdtemp(prefix="genesis-episode-")
            self._generate(seed, config.get("team_config") or {}, self._temp_dir)
            episode_dir = self._temp_dir

        # closed: the given directory as is - no regeneration, no new IDs
        self._episode = load_episode(episode_dir)
        self._episode_dir = str(Path(episode_dir))
        self._mode = mode
        self._started = self._clock()

        observation = {
            "task": TASK,
            "manifest": _public(self._episode.manifest),
            "policy": _public(self._episode.policy),
            "remaining_steps": ACTION_LIMIT,
        }
        return observation, {}

    # ------------------------------------------------------------------- step

    @property
    def remaining_steps(self) -> int:
        return ACTION_LIMIT - self._steps

    def _observation(self, result: Any, error: Optional[ToolError]) -> JSONDict:
        return {
            "result": _public(result) if result is not None else None,
            "error": None if error is None else {"code": error.code, "message": error.message},
            "remaining_steps": self.remaining_steps,
        }

    def _require_episode(self) -> EpisodeIndex:
        if self._episode is None:
            raise RuntimeError("reset() must be called before step()")
        return self._episode

    def _after_end(self) -> StepResult:
        observation = dict(self._final_observation or {})
        observation["result"] = None
        observation["error"] = {"code": "episode_finished", "message": "the episode has already ended"}
        return observation, 0, self._terminated, self._truncated, {}

    def _finish(self, *, terminated: bool, findings: JSONDict, observation: JSONDict) -> StepResult:
        self._done = True
        self._terminated = terminated
        self._truncated = not terminated
        self._final_findings = findings
        self._final_observation = observation
        reward: float = 0
        if self._mode == "train":
            reward = self._true_return()
        return observation, reward, self._terminated, self._truncated, {}

    def _timed_out(self) -> bool:
        return self._clock() - self._started >= self._time_limit

    def step(self, action: Any) -> StepResult:
        episode = self._require_episode()
        if self._done:
            return self._after_end()
        if self._timed_out():
            return self.truncate()

        self._steps += 1
        name = action.get("name") if isinstance(action, dict) else None

        if name == "submit":
            return self._submit(episode, action.get("args"))

        self._inspections += 1
        try:
            result = self._dispatch(episode, action)
            error = None
        except ToolError as exc:
            result, error = None, exc
        observation = self._observation(result, error)

        if self._steps >= ACTION_LIMIT:
            return self._finish(
                terminated=False,
                findings=_empty_findings(episode.episode_id),
                observation=observation,
            )
        return observation, 0, False, False, {}

    def truncate(self) -> StepResult:
        """Runner-only: end a timed-out episode with empty findings."""
        episode = self._require_episode()
        if self._done:
            return self._after_end()
        observation = self._observation(
            None, ToolError("timeout", f"time limit of {int(self._time_limit)} seconds reached")
        )
        return self._finish(
            terminated=False,
            findings=_empty_findings(episode.episode_id),
            observation=observation,
        )

    # ----------------------------------------------------------------- submit

    def _submit(self, episode: EpisodeIndex, args: Any) -> StepResult:
        payload = args.get("findings") if isinstance(args, dict) else None
        try:
            # Direct reset/step callers can bypass IPC. Enforce strict JSON here too.
            findings = _public(payload) if payload is not None else None
        except (TypeError, ValueError, RecursionError):
            findings = None
            reason = "findings must be strict JSON (no NaN or Infinity)"
        else:
            reason = structure_error(findings, episode.episode_id)
        if reason is None:
            observation = self._observation({"accepted": True}, None)
        else:
            observation = self._observation(None, ToolError("invalid_submit", reason))
        # Submit is final either way: an invalid structure is scored as -1.
        return self._finish(terminated=True, findings=findings, observation=observation)

    # ------------------------------------------------------------ trusted API

    def _true_return(self) -> float:
        score = self.final_score()
        value = score.get("return") if score else None
        return float(value) if isinstance(value, (int, float)) else 0.0

    def final_score(self) -> Optional[JSONDict]:
        """Trusted only: score of the frozen outcome via the scorer callback."""
        if not self._done or self._score is None or self._episode_dir is None:
            return None
        if self._score_cache is None:
            self._score_cache = self._score(
                self._episode_dir, self._final_findings, self._inspections
            )
        return dict(self._score_cache)

    def outcome(self) -> Optional[JSONDict]:
        """Trusted only: frozen findings and counters after the episode ended."""
        if not self._done:
            return None
        return {
            "findings": _public(self._final_findings) if self._final_findings is not None else None,
            "inspection_steps": self._inspections,
            "steps": self._steps,
            "terminated": self._terminated,
            "truncated": self._truncated,
        }

    # ------------------------------------------------------------------ tools

    def _dispatch(self, episode: EpisodeIndex, action: Any) -> Any:
        if not isinstance(action, dict):
            raise ToolError("invalid_action", "action must be an object with name and args")
        name = action.get("name")
        if not isinstance(name, str) or name not in ACTIONS:
            raise ToolError("unknown_action", f"supported actions: {', '.join(ACTIONS)}")
        args = action.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ToolError("invalid_args", "args must be an object")
        if name == "list_records":
            return self._list_records(episode, args)
        if name == "get_record":
            return self._get_record(episode, args)
        if name == "get_document":
            return self._get_document(episode, args)
        if name == "calculator":
            return self._calculator(args)
        raise ToolError("unknown_action", "unsupported action")  # pragma: no cover

    @staticmethod
    def _table(args: JSONDict) -> str:
        table = args.get("table")
        if not isinstance(table, str) or table not in TABLES:
            raise ToolError("unknown_table", f"table must be one of: {', '.join(TABLES)}")
        return table

    def _list_records(self, episode: EpisodeIndex, args: JSONDict) -> JSONDict:
        table = self._table(args)
        limit = args.get("limit", PAGE_LIMIT)
        if limit is None:
            limit = PAGE_LIMIT
        if type(limit) is not int or limit < 1:
            raise ToolError("invalid_args", f"limit must be an integer from 1 to {PAGE_LIMIT}")
        limit = min(limit, PAGE_LIMIT)

        records = episode.tables[table]
        offset = _parse_cursor(args.get("cursor"), table, len(records))
        page = records[offset:offset + limit]
        end = offset + len(page)
        next_cursor = _make_cursor(table, end) if end < len(records) else None
        return {"records": list(page), "next_cursor": next_cursor}

    def _get_record(self, episode: EpisodeIndex, args: JSONDict) -> JSONDict:
        table = self._table(args)
        record_id = args.get("id")
        if not isinstance(record_id, str):
            raise ToolError("invalid_args", "id must be a string")
        record = episode.by_id[table].get(record_id)
        if record is None:
            raise ToolError("unknown_id", f"no record with this id in table {table}")
        return record

    def _get_document(self, episode: EpisodeIndex, args: JSONDict) -> JSONDict:
        doc_id = args.get("doc_id")
        if not isinstance(doc_id, str):
            raise ToolError("invalid_args", "doc_id must be a string")
        document = episode.documents.get(doc_id)
        if document is None:
            raise ToolError("unknown_document", "no document with this doc_id")
        return document

    @staticmethod
    def _calculator(args: JSONDict) -> str:
        result, error = try_evaluate(args.get("expression"))
        if error is not None:
            raise ToolError(error["code"], error["message"])
        return result


# Cursors are opaque to the policy but deterministic: "<table>:<offset>".
# The data is immutable within an episode, so the same cursor always returns
# the same page. They carry no path, seed or truth.


def _make_cursor(table: str, offset: int) -> str:
    return f"{table}:{offset}"


def _parse_cursor(cursor: Any, table: str, size: int) -> int:
    if cursor is None:
        return 0
    if not isinstance(cursor, str):
        raise ToolError("invalid_cursor", "cursor must be null or a string from next_cursor")
    prefix, sep, raw = cursor.partition(":")
    if sep != ":" or prefix != table or not raw.isascii() or not raw.isdigit():
        raise ToolError("invalid_cursor", "cursor does not belong to this table")
    if len(raw) > 9:  # before int(): huge digit strings hit int_max_str_digits
        raise ToolError("invalid_cursor", "cursor is out of range")
    offset = int(raw)
    if offset > size:
        raise ToolError("invalid_cursor", "cursor is out of range")
    return offset
