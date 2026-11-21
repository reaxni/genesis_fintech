"""Permute public tables and remap IDs without changing business keys."""

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from random import Random

from genesis_audit.contracts.v1 import TABLES
from genesis_audit.data.export import read_episode, write_episode

SINGULAR_IDS = {
    "episode_id", "row_id", "entity_id", "invoice_id", "payment_id",
    "posting_group_id", "doc_id", "issue_id",
}
LIST_IDS = {"invoice_refs", "affected_ids", "evidence_ids", "evidence_sets"}


def _ids(value):
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in SINGULAR_IDS and isinstance(item, str):
                yield item
            else:
                yield from _ids(item)
    elif isinstance(value, list):
        for item in value:
            yield from _ids(item)


def _replace(value, mapping, is_reference=False):
    if isinstance(value, str) and is_reference:
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_replace(item, mapping, is_reference) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace(item, mapping, key in SINGULAR_IDS | LIST_IDS)
            for key, item in value.items()
        }
    return value


def transform_episode(source, target, seed, *, shuffle=False, rename_ids=False):
    """Write the transformed episode and return new-ID -> old-ID mapping."""
    if Path(source).resolve() == Path(target).resolve():
        raise ValueError("source and target must differ")
    if type(seed) is not int:
        raise TypeError("transform seed must be an integer")
    episode = read_episode(source)
    mapping = {}
    if rename_ids:
        old_ids = set(_ids(episode))
        minted = set(old_ids)
        for old in sorted(old_ids):
            nonce = 0
            while True:
                new = sha256(f"{seed}:{old}:{nonce}".encode()).hexdigest()[:20]
                if new not in minted:
                    break
                nonce += 1
            mapping[old] = new
            minted.add(new)
        episode = _replace(episode, mapping)
    if shuffle:
        random = Random(seed)
        for table in TABLES:
            records = episode[table]
            original = records[:]
            random.shuffle(records)
            if records == original and len(records) > 1:
                records.append(records.pop(0))
    write_episode(target, episode)
    return {new: old for old, new in mapping.items()}
