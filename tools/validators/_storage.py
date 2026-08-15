"""Shared helpers for reading HA ``.storage/`` registry JSON files."""

import json
import time
from pathlib import Path

type JSONValue = (
    dict[str, JSONValue] | list[JSONValue] | str | int | float | bool | None
)
type JSONObject = dict[str, JSONValue]


def _reject_duplicate_keys(pairs: list[tuple[str, JSONValue]]) -> JSONObject:
    """Build a JSON object while rejecting duplicate keys."""
    result: JSONObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key '{key}'")
        result[key] = value
    return result


def _load_storage_data(storage_path: Path) -> JSONObject | list[JSONValue]:
    """Load the validated root envelope and return its ``data`` value.

    Storage files use different ``data`` shapes, so callers own validation
    below the common top-level envelope. Retries once on transient
    JSONDecodeError caused by concurrent atomic writes.
    """
    data: JSONObject | list[JSONValue] | None = None
    for attempt in range(2):
        try:
            with open(storage_path, encoding="utf-8") as f:
                data = json.load(f, object_pairs_hook=_reject_duplicate_keys)
            break
        except json.JSONDecodeError:
            if attempt == 0:
                time.sleep(0.1)
                continue
            raise
    if not isinstance(data, dict):
        raise ValueError(f"{storage_path}: JSON root must be an object")
    if "data" not in data:
        raise ValueError(f"{storage_path}: missing 'data' object")
    storage_data = data["data"]
    if not isinstance(storage_data, (dict, list)):
        raise ValueError(f"{storage_path}: 'data' must be an object or list")
    return storage_data


def load_storage_registry(
    storage_path: Path,
    *,
    list_key: str,
    key_field: str,
) -> dict[str, JSONObject]:
    """Read a HA ``.storage/`` registry JSON and index items by *key_field*.

    Performs the parse-and-index step common to every registry loader in the
    validators package (entity, device, area). Callers own their own
    missing-file and parse-failure policy — this helper does one thing: open,
    parse, index.

    Args:
        storage_path: Path to the registry file (e.g.
            ``<config_dir>/.storage/core.entity_registry``).
        list_key: Key under ``data["data"][list_key]`` holding the item list
            (e.g. ``"entities"``, ``"devices"``, ``"areas"``).
        key_field: Item field to use as the result-dict key
            (e.g. ``"entity_id"``, ``"id"``).

    Returns:
        Dict mapping ``item[key_field]`` → item dict. Empty dict when the
        list is absent or empty (does **not** distinguish "missing key" from
        "empty list" — both yield ``{}``).

    Raises:
        OSError: ``storage_path`` does not exist or is unreadable
            (``FileNotFoundError``, ``PermissionError``, etc.).
        json.JSONDecodeError: File contents are not valid JSON.
        ValueError: The storage envelope, item list, or item mapping is
            malformed.
    """
    envelope = _load_storage_data(storage_path)
    if not isinstance(envelope, dict):
        raise ValueError(f"{storage_path}: 'data' must be an object")

    if list_key not in envelope:
        raise ValueError(f"{storage_path}: missing 'data.{list_key}' list")
    items = envelope[list_key]
    if not isinstance(items, list):
        raise ValueError(f"{storage_path}: 'data.{list_key}' must be a list")

    result: dict[str, JSONObject] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{storage_path}: items must be objects")
        key = item.get(key_field)
        if not isinstance(key, str):
            raise ValueError(f"{storage_path}: item missing string field '{key_field}'")
        if key in result:
            raise ValueError(f"{storage_path}: duplicate registry key '{key}'")
        result[key] = item
    return result


def is_entity_disabled(entry: dict[str, JSONValue] | JSONObject) -> bool:
    """Return True if the entity registry entry is marked as disabled."""
    return entry.get("disabled_by") is not None


def is_entity_hidden(entry: dict[str, JSONValue] | JSONObject) -> bool:
    """Return True if the entity registry entry is marked as hidden."""
    return entry.get("hidden_by") is not None
