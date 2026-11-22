"""Generate a deterministic synthetic Genesis episode in G0 v1.0 format.

Config keys:
  scenario: D01 through D20, or omit for a normal company.
  errors: mapping of contract issue types to integer counts, or a list with
      one entry per desired error. The total is at most 12. Use either
      errors or scenario. Scenarios D16–D20 cover errors sharing one event.
  invoice_count: number of ordinary background invoices, 50 through 80.
  timezone: IANA reporting timezone, default Asia/Almaty.
  profile: omit for the original fixtures, or "stress" with a D01–D20 scenario.
      Stress adds clean controls and variations of the same G0 issue types.
"""

from hashlib import sha256
import json
from zoneinfo import ZoneInfoNotFoundError

from genesis_audit.contracts.v1 import ISSUE_TYPES

from .export import write_episode
from .history import History
from .mutations import Mutations
from .scenarios import SCENARIOS, faults, scenario, stress_scenario

__all__ = ["generate_episode"]
VERSION = "1.0.0"
STRESS_VERSION = "1.1.0"


def _settings(config):
    if not isinstance(config, dict):
        raise TypeError("generator config must be a dict")
    unknown = config.keys() - {"scenario", "errors", "invoice_count", "timezone", "profile"}
    if unknown:
        raise ValueError(f"unknown generator config keys: {sorted(unknown)}")
    count = config.get("invoice_count", 50)
    if type(count) is not int or not 50 <= count <= 80:
        raise ValueError("invoice_count must be an integer between 50 and 80")
    name = config.get("scenario")
    if name is not None and (name not in SCENARIOS or "errors" in config):
        raise ValueError("scenario must be D01 through D20 and excludes errors")
    if "profile" in config and (config["profile"] != "stress" or name is None):
        raise ValueError('profile must be "stress" and requires a scenario')
    timezone = config.get("timezone", "Asia/Almaty")
    if not isinstance(timezone, str):
        raise ValueError("timezone must be an IANA timezone name")
    errors = config.get("errors", {})
    if isinstance(errors, list) and all(isinstance(kind, str) and kind in ISSUE_TYPES for kind in errors):
        errors = {kind: errors.count(kind) for kind in ISSUE_TYPES}
    if not isinstance(errors, dict) or any(
        kind not in ISSUE_TYPES or type(number) is not int or number < 0
        for kind, number in errors.items()
    ) or sum(errors.values()) > 12:
        raise ValueError("errors must use contract issue types and contain at most 12 faults")
    counts = {kind: errors[kind] for kind in ISSUE_TYPES if errors.get(kind, 0)}
    settings = {"invoice_count": count, "timezone": timezone, "scenario": name, "errors": counts}
    if "profile" in config:
        settings["profile"] = config["profile"]
    return settings


def generate_episode(seed: int, config: dict, output_dir: str) -> None:
    if type(seed) is not int:
        raise TypeError("seed must be an integer")
    settings = _settings(config)
    stress = settings.get("profile") == "stress"
    version = STRESS_VERSION if stress else VERSION
    identity = json.dumps([version, seed, settings], sort_keys=True).encode()
    episode_id = sha256(identity).hexdigest()[:20]
    try:
        history = History(seed, settings["timezone"], episode_id)
    except ZoneInfoNotFoundError as error:
        raise ValueError("unknown IANA timezone") from error
    history.background(settings["invoice_count"], detailed=stress)
    changes = Mutations(history)
    if stress:
        stress_scenario(history, changes, settings["scenario"])
    elif settings["scenario"]:
        scenario(history, changes, settings["scenario"])
    else:
        faults(history, changes, settings["errors"])
    changes.commit()
    records = history.records(changes.issues, changes.changes, version)
    records["provenance"]["config"] = settings
    write_episode(output_dir, records)
