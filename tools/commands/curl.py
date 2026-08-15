"""``curl`` subcommand: pure-Python HA REST API client.

Replaces the earlier bash/curl/jq pipeline with HAClient (pure Python).
Compact JSON by default; use ``--pretty`` for human-readable output.

Token-efficiency flags for agents:
  ``--count``    — print item count instead of full payload
  ``--keys``     — print key names only (no values)
  ``--first N``  — print first N items
  ``--pick F``   — keep only specified JSON keys (per-item projection)
  ``--entity ID`` — fetch a single entity by id (server-side)
  ``--domain D``  — filter list response by domain (client-side)
  ``--max-chars N`` — truncate compact JSON output when it exceeds N characters
"""

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, cast

import requests

from tools.common import (
    _ENTITY_RE,
    HARequestError,
    add_output_shape_args,
    add_summary_args,
    fail_stderr,
    positive_int,
    resolve_max_chars,
    resolve_summary,
)
from tools.ha.client import HAClient
from tools.output_shape import JSONValue, apply_output_shape, print_json

_HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")
_BODY_METHODS = frozenset(("POST", "PUT", "PATCH"))
_COLLECTION_OUTPUT_FLAGS = ("count", "keys", "raw")
_GUARDRAIL_TRUTHY_FLAGS = (
    *_COLLECTION_OUTPUT_FLAGS,
    "pick",
    "entity",
    "domain",
)
_GUARDRAIL_SET_FLAGS = ("first", "max_chars")


def _has_guardrail_bypass_flags(args: argparse.Namespace) -> bool:
    """Check if any flag bypasses the bare endpoint guardrail."""
    return bool(
        any(getattr(args, flag) for flag in _GUARDRAIL_TRUTHY_FLAGS)
        or any(getattr(args, flag) is not None for flag in _GUARDRAIL_SET_FLAGS)
    )


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Wire the ``curl`` subparser."""
    parser = subparsers.add_parser(
        "curl",
        help="Call HA REST API via HAClient (pure Python).",
        description=(
            "Pure-Python HA API client using HAClient. "
            "Compact JSON by default; use --pretty for human-readable output. "
            "Agent-friendly flags: --count, --keys, --first N."
        ),
    )

    # ---- endpoint positional ----
    def _validate_endpoint(value: str) -> str:
        if not value.startswith("/"):
            raise argparse.ArgumentTypeError("endpoint must start with /")
        return value

    parser.add_argument(
        "endpoint",
        type=_validate_endpoint,
        nargs="?",
        default=None,
        help="API endpoint (optional when --entity is used)",
    )

    # ---- method ----
    method_group = parser.add_mutually_exclusive_group()
    method_group.add_argument(
        "--post",
        "-X",
        dest="method",
        action="store_const",
        const="POST",
        default="GET",
        help="Use POST (backward compat; prefer --method POST)",
    )
    method_group.add_argument(
        "--method",
        "-M",
        choices=_HTTP_METHODS,
        help="HTTP method (default: GET)",
    )

    parser.add_argument("--data", "-d", help="JSON request body")

    # ---- domain filter / entity filter ----
    parser.add_argument(
        "--domain",
        help="Filter response items by domain (entity_id prefix, e.g. light)",
    )
    # ---- entity filter (single-entity fetch) ----
    parser.add_argument(
        "--entity",
        help="Fetch a single entity by entity_id (e.g. sensor.temperature)",
    )

    # ---- output processing (mutually exclusive) ----
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--count",
        action="store_true",
        help="Print number of items (list items or top-level dict keys)",
    )
    output_group.add_argument(
        "--keys",
        action="store_true",
        help="Print all unique JSON key names (no values)",
    )
    output_group.add_argument(
        "--first",
        metavar="N",
        type=positive_int,
        help="Print first N items only",
    )
    output_group.add_argument(
        "--raw",
        action="store_true",
        help="Print raw response body (skip JSON processing entirely)",
    )

    # ---- bare-endpoint guardrail ----
    parser.add_argument(
        "--no-guard",
        action="store_true",
        help="Disable guardrail AND default max-chars cap (dump all entities)",
    )

    # ---- shared token-reduction flags (--pick, --max-chars) ----
    add_output_shape_args(parser, first=False)

    # ---- output formatting ----
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON with indent=2 (default: compact)",
    )

    add_summary_args(parser)

    parser.set_defaults(func=run)


# ====================================================================
# Helper functions
# ====================================================================


class _CurlError(Exception):
    """Expected curl failure translated to a CLI exit code by run."""


@dataclass(frozen=True)
class _CurlRequest:
    """Normalized request data shared by execution and output rendering."""

    method: str
    endpoint: str


def _has_collection_output_flags(args: argparse.Namespace) -> bool:
    """Check whether a collection-oriented output flag is active."""
    return any(getattr(args, flag) for flag in _COLLECTION_OUTPUT_FLAGS)


def _validate_args(args: argparse.Namespace, summary: bool) -> _CurlRequest:
    """Validate CLI args for curl: conflict checks, entity/domain, endpoint."""
    method = args.method
    endpoint = args.endpoint

    if args.raw and args.pretty:
        raise _CurlError("Cannot combine --raw with --pretty")

    if args.pick and _has_collection_output_flags(args):
        raise _CurlError("Cannot combine --pick with --count/--keys/--raw")

    if args.entity:
        if not _ENTITY_RE.match(args.entity):
            raise _CurlError(f"Invalid entity_id: {args.entity!r}")
        if endpoint and endpoint.rstrip("/") != "/api/states":
            raise _CurlError(
                "--entity requires endpoint /api/states (or omit endpoint)"
            )
        if _has_collection_output_flags(args):
            raise _CurlError("Cannot combine --entity with --count/--keys/--raw")
        if method != "GET" and not summary:
            print(
                "\u26a0\ufe0f  --entity forces GET method (ignoring --method)",
                file=sys.stderr,
            )
        endpoint = f"/api/states/{args.entity}"
        method = "GET"

    if args.domain:
        if args.entity:
            raise _CurlError("Cannot combine --domain with --entity")
        if _has_collection_output_flags(args):
            raise _CurlError("Cannot combine --domain with --count/--keys/--raw")

    if not endpoint:
        raise _CurlError("endpoint path is required (use --entity to fetch by id)")

    return _CurlRequest(method=method, endpoint=endpoint)


def _build_client() -> HAClient:
    """Build an HAClient from env, raising _CurlError on failure."""
    try:
        return HAClient.from_env()
    except HARequestError as e:
        raise _CurlError(str(e)) from e


def _parse_json_body(method: str, args: argparse.Namespace, summary: bool) -> Any:
    """Parse --data JSON for body-applicable methods."""
    json_data = None
    if method in _BODY_METHODS:
        if args.data is not None:
            try:
                json_data = json.loads(args.data)
            except (json.JSONDecodeError, TypeError) as e:
                raise _CurlError(f"Invalid JSON in --data: {e}") from e
    elif args.data is not None and not summary:
        print(f"\u26a0\ufe0f  --data ignored for {method} requests", file=sys.stderr)
    return json_data


def _execute_request(
    client: HAClient,
    method: str,
    endpoint: str,
    json_data: Any,
) -> requests.Response:
    """Dispatch the HTTP method to the matching HAClient method.

    Raises _CurlError for an unknown method or request failure.
    """
    if method not in _HTTP_METHODS:
        raise _CurlError(f"Unknown HTTP method: {method}")
    try:
        handler = getattr(client, method.lower())
        if method == "GET":
            return handler(endpoint)
        return handler(endpoint, json=json_data)
    except HARequestError as e:
        raise _CurlError(str(e)) from e


def _emit_output(
    args: argparse.Namespace,
    request: _CurlRequest,
    data: Any,
    raw_text: str,
    json_parsed: bool,
    summary: bool,
) -> int:
    """Dispatch output processing based on CLI flags. Returns exit code."""
    # Keep this sequence explicit: these are intentionally ordered side effects.
    guardrail_result = _handle_guardrail(args, request, data, json_parsed, summary)
    if guardrail_result is not None:
        return guardrail_result

    _validate_json_output_flags(args, data, json_parsed)
    _emit_output_warnings(args, summary)

    effective_max_chars = _effective_max_chars(args, summary)
    early_result = _handle_early_output(
        args, data, raw_text, summary, effective_max_chars
    )
    if early_result is not None:
        return early_result

    data = _filter_domain(data, args.domain, summary)

    if args.first is not None and not summary:
        _warn_first_overcount(data, args.first)

    data = _shape_output(data, args, effective_max_chars)
    _render_output(data, raw_text, args.pretty, json_parsed)
    return 0


def _handle_guardrail(
    args: argparse.Namespace,
    request: _CurlRequest,
    data: Any,
    json_parsed: bool,
    summary: bool,
) -> int | None:
    """Handle the compact-mode bare ``/api/states`` guardrail."""
    if (
        request.method == "GET"
        and request.endpoint.rstrip("/") == "/api/states"
        and not _has_guardrail_bypass_flags(args)
        and not args.pretty
        and summary
        and not args.no_guard
    ):
        if not json_parsed:
            print("Error: /api/states response was not valid JSON", file=sys.stderr)
            return 1
        count_result = _handle_count(data)
        print(
            "Hint: use --first/--pick/--entity/--domain to narrow, "
            "or --no-guard to dump all",
            file=sys.stderr,
        )
        return count_result
    return None


def _validate_json_output_flags(
    args: argparse.Namespace, data: Any, json_parsed: bool
) -> None:
    """Reject JSON-only output transforms for a non-JSON response."""
    requires_json = args.keys or (args.first is not None) or bool(args.pick)
    if requires_json and data is None and not json_parsed:
        flag = "keys" if args.keys else ("first" if args.first is not None else "pick")
        raise _CurlError(
            f"Cannot use --{flag} on non-JSON response (Content-Type: unknown)"
        )


def _emit_output_warnings(args: argparse.Namespace, summary: bool) -> None:
    """Emit output-mode warnings that are suppressed in summary mode."""
    if args.pretty and not summary and args.keys:
        print(
            "\u26a0\ufe0f  --pretty has no effect with --keys",
            file=sys.stderr,
        )


def _effective_max_chars(args: argparse.Namespace, summary: bool) -> int | None:
    """Resolve the output cap, including the ``--no-guard`` override."""
    if args.no_guard and args.max_chars is None:
        return None
    return resolve_max_chars(args, summary)


def _handle_early_output(
    args: argparse.Namespace,
    data: Any,
    raw_text: str,
    summary: bool,
    max_chars: int | None,
) -> int | None:
    """Handle output flags that intentionally bypass filtering and shaping."""
    if args.count:
        return _handle_count(data)
    if args.keys:
        return _handle_keys(data, summary=summary, max_chars=max_chars)
    if args.raw:
        print(raw_text, end="")
        return 0
    return None


def _filter_domain(data: Any, domain: str | None, summary: bool) -> Any:
    """Filter list responses by the requested entity domain."""
    if not domain or not isinstance(data, list):
        return data
    prefix = f"{domain}."
    filtered = [
        item
        for item in data
        if isinstance(item, dict)
        and isinstance(item.get("entity_id"), str)
        and item["entity_id"].startswith(prefix)
    ]
    if not summary and len(filtered) < len(data):
        print(
            f"# domain {domain!r}: {len(filtered)}/{len(data)} items matched",
            file=sys.stderr,
        )
    return filtered


def _shape_output(data: Any, args: argparse.Namespace, max_chars: int | None) -> Any:
    """Apply the shared first → pick → max-chars output shape."""
    return apply_output_shape(
        data,
        first=args.first,
        pick=args.pick,
        max_chars=max_chars,
    )


def _render_output(data: Any, raw_text: str, pretty: bool, json_parsed: bool) -> None:
    """Render parsed JSON, falling back to the original response body."""
    if json_parsed:
        print_json(data, pretty=pretty)
    else:
        print(raw_text, end="")


# ====================================================================
# run()
# ====================================================================


def run(args: argparse.Namespace) -> int:
    """Execute a curl request.  Returns exit code (0 success, 1 error)."""
    try:
        summary = resolve_summary(args)
        request = _validate_args(args, summary)
        client = _build_client()
        # Keep the original object in use so test doubles that do not return
        # themselves from __enter__ still exercise the configured request path.
        with client:
            json_data = _parse_json_body(request.method, args, summary)
            resp = _execute_request(client, request.method, request.endpoint, json_data)

            if resp.status_code < 200 or resp.status_code >= 300:
                raise _CurlError(f"HTTP {resp.status_code}: {resp.text[:200]}")

            content_type = resp.headers.get("content-type", "")
            raw_text = resp.text
            looks_like_json = "application/json" in content_type or (
                raw_text.strip().startswith(("{", "["))
            )
            data = None
            json_parsed = False
            if looks_like_json:
                try:  # noqa: SIM105 - preserve a narrow, explicit parser boundary
                    data = resp.json()
                    json_parsed = True
                except ValueError:
                    # Keep response classification separate from parse success:
                    # an invalid JSON body still falls back to its raw text.
                    pass

            return _emit_output(args, request, data, raw_text, json_parsed, summary)
    except _CurlError as e:
        return fail_stderr(str(e))


# ====================================================================
# Output helper functions
# ====================================================================


def _handle_count(data: Any) -> int:
    """Print the length of a JSON collection; 0 otherwise."""
    if isinstance(data, (list, dict)):
        print(len(data))
    else:
        print(0)
    return 0


def _collect_key_names(data: Any) -> tuple[list[str] | None, str]:
    """Normalize list/dict key collection and return its diagnostic kind."""
    if isinstance(data, list):
        if not data:
            return [], "empty"
        all_keys: set[str] = set()
        for item in data:
            if isinstance(item, dict):
                all_keys.update(item.keys())
        if not all_keys:
            return [], "non-dict"
        return sorted(all_keys), "list"
    if isinstance(data, dict):
        return list(data.keys()), "dict"
    return None, "scalar"


def _print_key_names(keys: list[str], max_chars: int | None) -> None:
    """Shape and print normalized key names as compact JSON."""
    shaped = apply_output_shape(cast(JSONValue, keys), max_chars=max_chars)
    print(json.dumps(shaped, separators=(",", ":"), ensure_ascii=False))


def _handle_keys(data, summary: bool = False, max_chars: int | None = None) -> int:
    """Print unique JSON key names (metadata to stderr, keys to stdout)."""
    keys, kind = _collect_key_names(data)
    if kind == "empty":
        print("# empty list", file=sys.stderr)
        _print_key_names([], max_chars)
    elif kind == "non-dict":
        print(
            f"# {len(data)} items (non-dict, no keys available)",
            file=sys.stderr,
        )
        _print_key_names([], max_chars)
    elif kind == "scalar":
        print("# not a JSON object or list", file=sys.stderr)
        printable = (
            json.dumps(data, separators=(",", ":"), ensure_ascii=False)
            if data is not None
            else "null"
        )
        print(printable)
    elif kind == "list":
        count = len(data)
        assert keys is not None
        if summary:
            print(f"# {count} items, {len(keys)} keys", file=sys.stderr)
        else:
            print(
                f"# {count} items, {len(keys)} unique keys: {', '.join(keys)}",
                file=sys.stderr,
            )
        _print_key_names(keys, max_chars)
    else:
        assert keys is not None
        print(f"# {len(keys)} keys", file=sys.stderr)
        _print_key_names(keys, max_chars)
    return 0


def _warn_first_overcount(data, n: int):
    """Emit a warning to stderr when ``--first`` exceeds data length (verbose)."""
    if isinstance(data, list) and n > len(data):
        print(
            f"# requested {n}, only {len(data)} items available",
            file=sys.stderr,
        )
    elif isinstance(data, dict) and n > len(data):
        print(
            f"# requested {n}, only {len(data)} keys available",
            file=sys.stderr,
        )
