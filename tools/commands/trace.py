"""``trace`` subcommand: fetch Home Assistant automation traces via WebSocket."""

import argparse
import sys
from datetime import UTC, datetime
from typing import cast

from tools.common import (
    _ENTITY_RE,
    HARequestError,
    add_output_shape_args,
    add_summary_args,
    fail_stderr,
    resolve_max_chars,
    resolve_summary,
)
from tools.ha.client import HAClient, HAWSClient
from tools.output_shape import (
    JSONValue,
    apply_output_shape,
    print_json,
    truncate_dict_by_key_size,
)


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Wire the ``trace`` subparser."""
    parser = subparsers.add_parser(
        "trace",
        help="Fetch Home Assistant automation traces.",
        description=(
            "Fetch automation trace data from Home Assistant via WebSocket. "
            "With an entity_id, shows the trace for that automation; "
            "without one, lists all available traces."
        ),
    )
    parser.add_argument(
        "entity_id",
        nargs="?",
        default=None,
        help="Automation entity ID (e.g. automation.morning_routine)",
    )
    add_output_shape_args(parser)
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output with indent=2 (default: compact)",
    )
    add_summary_args(parser)
    parser.set_defaults(func=run)


def _validate_args(args: argparse.Namespace, summary: bool) -> int | None:
    """Validate CLI args. Returns ``1`` on error, ``None`` otherwise."""
    if args.entity_id is not None and not _ENTITY_RE.fullmatch(args.entity_id):
        return fail_stderr(f"Invalid entity_id: {args.entity_id!r}")
    if args.first is not None and args.first < 1:
        return fail_stderr("--first must be >= 1")
    if args.entity_id and args.first is not None and not summary:
        print(
            "\u26a0\ufe0f  --first is ignored when fetching a single automation trace",
            file=sys.stderr,
        )
    return None


class _TraceNotFoundError(Exception):
    """Raised when an automation has no trace-list entries."""


def _trace_timestamp_key(value: object) -> float:
    """Return a timezone-normalized sort key for a trace timestamp."""
    if not isinstance(value, str):
        return float("-inf")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).timestamp()


def _trace_start(trace: dict) -> object:
    """Extract a trace start value from dict- or string-shaped timestamps."""
    timestamp = trace.get("timestamp")
    if isinstance(timestamp, dict):
        return timestamp.get("start")
    return timestamp


def _fetch_single_trace(ws_client: HAWSClient, entity_id: str) -> dict:
    """Resolve an automation entity and fetch its newest trace run."""
    entity_short = entity_id.split(".", 1)[1]
    try:
        with HAClient.from_env() as rest_client:
            state = rest_client.get_json(f"/api/states/{entity_id}")
        attributes = state.get("attributes") if isinstance(state, dict) else {}
        item_id = (
            attributes.get("id") if isinstance(attributes, dict) else None
        ) or entity_short
    except HARequestError:
        item_id = entity_short

    traces = ws_client.command("trace/list", domain="automation", item_id=item_id)
    if not isinstance(traces, list):
        raise HARequestError("Invalid trace/list response")
    traces = [trace for trace in traces if isinstance(trace, dict)]
    matching = [trace for trace in traces if trace.get("item_id") == item_id]
    if not matching:
        raise _TraceNotFoundError(entity_id)
    matching.sort(
        key=lambda trace: _trace_timestamp_key(_trace_start(trace)),
        reverse=True,
    )
    latest = matching[0]
    latest_item_id = latest.get("item_id")
    latest_run_id = latest.get("run_id")
    if not isinstance(latest_item_id, str) or not isinstance(latest_run_id, str):
        raise HARequestError("Invalid trace/list entry")
    data = ws_client.command(
        "trace/get",
        domain="automation",
        item_id=latest_item_id,
        run_id=latest_run_id,
    )
    if not isinstance(data, dict):
        raise HARequestError("Invalid trace/get response")
    return data


def _summarize_trace_list(traces: list[dict]) -> list[dict]:
    """Project, sort, deduplicate, and count a trace-list response."""
    compact = [
        {
            "item_id": trace.get("item_id"),
            "state": trace.get("state"),
            "trigger": trace.get("trigger"),
            "timestamp": _trace_start(trace),
        }
        for trace in traces
        if isinstance(trace, dict)
    ]
    compact.sort(
        key=lambda entry: _trace_timestamp_key(entry.get("timestamp")),
        reverse=True,
    )

    seen: dict[str, dict] = {}
    run_counts: dict[str, int] = {}
    for entry in compact:
        raw_item_id = entry.get("item_id")
        item_id = raw_item_id if isinstance(raw_item_id, str) else ""
        run_counts[item_id] = run_counts.get(item_id, 0) + 1
        if item_id not in seen:
            seen[item_id] = entry

    data = list(seen.values())
    for entry in data:
        raw_item_id = entry.get("item_id")
        item_id = raw_item_id if isinstance(raw_item_id, str) else ""
        runs = run_counts.get(item_id, 1)
        if runs > 1:
            entry["runs"] = runs
    return data


def _fetch_data(
    ws_client: HAWSClient,
    args: argparse.Namespace,
    summary: bool,
) -> tuple[dict | list | None, int | None]:
    """Fetch trace data via WebSocket (and optionally REST for single-entity).

    Returns ``(data, None)`` on success, ``(None, 1)`` on error.
    """
    try:
        if args.entity_id:
            try:
                data = _fetch_single_trace(ws_client, args.entity_id)
            except _TraceNotFoundError:
                return None, fail_stderr(f"No traces found for {args.entity_id}")
        else:
            data = ws_client.command("trace/list", domain="automation")
            if not isinstance(data, list):
                raise HARequestError("Invalid trace/list response")
            if summary and not args.pretty:
                data = _summarize_trace_list(data)
    except HARequestError as e:
        return None, fail_stderr(str(e))
    return data, None


def _shape_single_entity_data(
    data: dict,
    args: argparse.Namespace,
    summary: bool,
) -> dict:
    """Apply summary-mode stripping, max-chars cap, and pick to single-entity data."""
    if summary and not args.pretty:
        data = {
            k: v for k, v in data.items() if k not in ("config", "blueprint_inputs")
        }
        if isinstance(data.get("trace"), dict):
            data["trace"] = _prune_trace_entries(data["trace"])

    max_chars = resolve_max_chars(args, summary)
    if args.pick:
        projected = apply_output_shape(data, pick=args.pick)
        if isinstance(projected, dict):
            data = projected

    if max_chars is not None:
        data = _cap_trace_dict(data, max_chars)
        if data.get("_truncated") is True:
            dropped = len(cast(list[JSONValue], data["dropped_steps"]))
            kept = len(cast(list[JSONValue], data["kept_steps"]))
            total = dropped + kept
            print(
                f"# trace truncated to ~{max_chars} chars "
                f"(dropped {dropped}/{total} steps)",
                file=sys.stderr,
            )

    return cast(dict[str, JSONValue], data)


def _shape_list_data(
    data: list,
    args: argparse.Namespace,
    summary: bool,
) -> list:
    """Apply output shaping (--first, --pick, --max-chars) to list data."""
    shaped = apply_output_shape(
        data,
        first=args.first,
        pick=args.pick,
        max_chars=resolve_max_chars(args, summary),
    )
    return cast(list[JSONValue], shaped)


def run(args: argparse.Namespace) -> int:
    """Entry point for the ``trace`` subcommand. Returns exit code."""
    summary = resolve_summary(args)

    err = _validate_args(args, summary)
    if err is not None:
        return err

    try:
        ws_client = HAWSClient.from_env()
    except HARequestError as e:
        return fail_stderr(str(e))

    data, exit_code = _fetch_data(ws_client, args, summary)
    if exit_code is not None:
        return exit_code

    if args.entity_id and isinstance(data, dict):
        data = _shape_single_entity_data(data, args, summary)
    elif isinstance(data, list):
        data = _shape_list_data(data, args, summary)
    elif data is None:
        data = []

    print_json(data, pretty=args.pretty)
    return 0


def _prune_trace_entries(trace: dict) -> dict:
    """Drop ``.attributes`` from all entity-state dicts within changed_variables.

    Strips the ``attributes`` key from *every* dict value inside
    ``changed_variables`` (not just ``this``), keeping ``entity_id`` and
    ``state`` for debugging context.  These entity-state attributes blobs
    are the dominant contributor to trace size in complex automations.

    Defensive against malformed entries (non-list values, missing keys).
    Mutates nothing — returns a new dict.
    """
    pruned: dict = {}
    for step_key, entries in trace.items():
        if not isinstance(entries, list):
            pruned[step_key] = entries
            continue
        new_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                new_entries.append(entry)
                continue
            cv = entry.get("changed_variables")
            if isinstance(cv, dict):
                new_cv = {}
                for k, v in cv.items():
                    if isinstance(v, dict) and "attributes" in v:
                        new_cv[k] = {
                            kk: vv for kk, vv in v.items() if kk != "attributes"
                        }
                    else:
                        new_cv[k] = v
                entry = {**entry, "changed_variables": new_cv}
            new_entries.append(entry)
        pruned[step_key] = new_entries
    return pruned


def _cap_trace_dict(data: dict, max_chars: int) -> dict:
    """Drop largest trace step keys until serialized dict fits within *max_chars*.

    Thin wrapper over :func:`truncate_dict_by_key_size` configured for the
    trace use case: candidate keys live under ``data["trace"]``, top-level
    fields are preserved, the marker uses ``dropped_steps`` / ``kept_steps``
    field names, and at least one step is always kept.
    """
    return truncate_dict_by_key_size(
        data,
        max_chars,
        target_key="trace",
        dropped_key_name="dropped_steps",
        kept_key_name="kept_steps",
        preserve_min=1,
    )
