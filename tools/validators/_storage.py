"""Shared helpers for reading HA ``.storage/`` registry JSON files."""

import json
from pathlib import Path

type JSONValue = (
    dict[str, JSONValue] | list[JSONValue] | str | int | float | bool | None
)
type JSONObject = dict[str, JSONValue]


def _load_json(storage_path: Path) -> JSONObject:
    """Open and parse a storage JSON object.

    Storage files use different ``data`` shapes, so callers own validation
    below the common top-level object envelope.
    """
    with open(storage_path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{storage_path}: JSON root must be an object")
    return data


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
    data = _load_json(storage_path)
    if "data" not in data:
        raise ValueError(f"{storage_path}: missing 'data' object")
    envelope = data["data"]
    if not isinstance(envelope, dict):
        raise ValueError(f"{storage_path}: 'data' must be an object")

    items = envelope.get(list_key, [])
    if not isinstance(items, list):
        raise ValueError(f"{storage_path}: 'data.{list_key}' must be a list")

    result: dict[str, JSONObject] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{storage_path}: items must be objects")
        key = item.get(key_field)
        if not isinstance(key, str):
            raise ValueError(f"{storage_path}: item missing string field '{key_field}'")
        result[key] = item
    return result
