"""Shared JSON output-shaping helpers for token-efficient CLI output.

Provides ``apply_output_shape()`` — a single transform applying (in order)
first-slice → pick (field projection) → max-chars (char-length truncation).
Used by curl and other JSON-emitting subcommands to cap token
output for agent consumption.
"""

import json

type JSONValue = (
    None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
)


def _compact_dumps(data: JSONValue) -> str:
    """Serialize *data* as compact JSON (no whitespace, UTF-8 passthrough)."""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def apply_output_shape(
    data: JSONValue,
    *,
    first: int | None = None,
    pick: str | None = None,
    max_chars: int | None = None,
) -> JSONValue:
    """Apply token-reduction transforms to JSON data.

    Order of operations: first → pick → max_chars.

    Args:
        data: Parsed JSON (list, dict, or scalar).
        first: Keep only first N items (must be >= 1; raises ValueError otherwise).
        pick: Comma-separated field names to retain (per-item projection).
        max_chars: Drop trailing list items until COMPACT JSON fits within N
            characters.
            Note: the print layer may render with --pretty (indent=2), whose size
            exceeds the compact size — so pretty output can overshoot max_chars.
            This is intentional (compact is the token-cost proxy).
    """
    if first is not None:
        if first < 1:
            raise ValueError(f"first must be >= 1, got {first}")
        data = _first(data, first)
    if pick and pick.strip():
        fields = [f.strip() for f in pick.split(",") if f.strip()]
        if fields:
            data = _pick_fields(data, fields)
    if max_chars is not None and max_chars > 0:
        data = _truncate_by_chars(data, max_chars)
    return data


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _first(data: JSONValue, n: int) -> JSONValue:
    """Keep first *n* items from a list/dict.  Scalars become ``[data]``."""
    if isinstance(data, list):
        return data[:n]
    if isinstance(data, dict):
        return dict(list(data.items())[:n])
    return [data]


def _pick_fields(data: JSONValue, fields: list[str]) -> JSONValue:
    """Keep only the specified *fields* from each dict in *data*."""
    if isinstance(data, list):
        return [_pick_item(item, fields) for item in data]
    if isinstance(data, dict):
        return _pick_item(data, fields)
    return data


def _pick_item(item: JSONValue, fields: list[str]) -> JSONValue:
    if not isinstance(item, dict):
        return item
    return {k: item[k] for k in fields if k in item}


def print_json(data: JSONValue, *, pretty: bool = False) -> None:
    """Print JSON to stdout with configurable indentation.

    Compact output (no whitespace) by default; pretty-print with indent=2
    when *pretty* is True.
    """
    if pretty:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(_compact_dumps(data))


def _truncate_by_chars(data: JSONValue, max_chars: int) -> JSONValue:
    """Drop trailing list items (or largest dict keys) until compact JSON fits.

    Appends a ``{"_truncated": True, ...}`` marker describing what was dropped.
    Scalars that still exceed *max_chars* are passed through unchanged.
    If even the empty-marker exceeds *max_chars* (tiny limit), the marker is
    returned anyway (same degradation as the list path — preferable to silently
    dropping the whole response).
    """
    serialized = _compact_dumps(data)
    if len(serialized) <= max_chars:
        return data
    if isinstance(data, list):
        return _truncate_list(data, max_chars)
    if isinstance(data, dict):
        return truncate_dict_by_key_size(data, max_chars)
    return data


def _truncate_list(data: list[JSONValue], max_chars: int) -> list[JSONValue]:
    """Drop trailing list items until the (compact) serialization fits.

    Uses a prefix-sum + binary search over per-item serialized lengths so
    the fit check is O(N log N) rather than O(N²).
    """
    original_len = len(data)
    if original_len == 0:
        return [{"_truncated": True, "shown": 0, "total": 0}]

    # Per-item compact serialized length
    item_lens = [len(_compact_dumps(item)) for item in data]
    # prefix[n] = size of JSON array data[:n] (brackets + commas between items).
    prefix = [2]  # "[" + "]"
    for i, ln in enumerate(item_lens):
        prefix.append(prefix[-1] + (1 if i > 0 else 0) + ln)

    # Precompute marker sizes for each possible n.
    marker_lens = {}
    for n in range(0, original_len + 1):
        item_marker: dict[str, JSONValue] = {
            "_truncated": True,
            "shown": n,
            "total": original_len,
        }
        marker_str = _compact_dumps(item_marker)
        marker_lens[n] = (1 if n > 0 else 0) + len(marker_str)

    # Binary-search: largest n in [0, original_len] such that total fits.
    lo, hi, best = 0, original_len, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if prefix[mid] + marker_lens[mid] <= max_chars:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    if best == 0 and prefix[0] + marker_lens[0] > max_chars:
        return [{"_truncated": True, "shown": 0, "total": original_len}]

    marker: dict[str, JSONValue] = {
        "_truncated": True,
        "shown": best,
        "total": original_len,
    }
    return data[:best] + [marker]


def truncate_dict_by_key_size(
    data: dict[str, JSONValue],
    max_chars: int,
    *,
    target_key: str | None = None,
    dropped_key_name: str = "dropped_keys",
    kept_key_name: str = "kept_keys",
    preserve_min: int = 0,
) -> dict[str, JSONValue]:
    """Drop largest-value keys from a (sub)dict until the compact serialization fits.

    Greedy-fit: ranks candidate keys by serialized value length (largest first),
    drops them one at a time, and returns as soon as the candidate fits within
    *max_chars*. Always attaches a marker dict describing what was removed.

    Args:
        data: The dict to fit. When *target_key* is None, candidates are the
            top-level keys of *data* and the marker is attached at the top level.
            When *target_key* is set (e.g. ``"trace"``), candidates are the keys
            of ``data[target_key]``; the result preserves all other top-level
            fields and replaces ``data[target_key]`` with the trimmed sub-dict.
        max_chars: Maximum compact-JSON char budget.
        target_key: Optional sub-dict key whose entries are the drop candidates.
        dropped_key_name: Marker field name listing dropped candidate keys.
        kept_key_name: Marker field name listing surviving candidate keys.
        preserve_min: Always keep at least this many candidates (default 0).

    Returns:
        A new dict (does not mutate *data*). If *data* already fits, it is
        returned unchanged. If even the empty-candidate marker exceeds
        *max_chars*, the marker is returned anyway (same degradation as the
        list path — preferable to silently dropping the whole response).
    """
    serialized = _compact_dumps(data)
    if len(serialized) <= max_chars:
        return data

    if target_key is None:
        target = data
    else:
        target = data.get(target_key)
        if not isinstance(target, dict) or len(target) <= preserve_min:
            return data
    if len(target) <= preserve_min:
        return data

    keys_by_size = sorted(
        target.keys(),
        key=lambda k: len(_compact_dumps(target[k])),
        reverse=True,
    )

    remaining: dict[str, JSONValue] = dict(target)
    dropped: list[str] = []

    def build_candidate() -> dict[str, JSONValue]:
        marker: dict[str, JSONValue] = {
            "_truncated": True,
            dropped_key_name: list(dropped),
            kept_key_name: list(remaining.keys()),
        }
        if target_key is None:
            return {**remaining, **marker}
        return {**data, target_key: remaining, **marker}

    for k in keys_by_size:
        if len(remaining) <= preserve_min:
            break
        dropped.append(k)
        del remaining[k]
        candidate = build_candidate()
        if len(_compact_dumps(candidate)) <= max_chars:
            return candidate

    return build_candidate()
