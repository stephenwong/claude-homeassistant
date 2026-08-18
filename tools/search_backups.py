#!/usr/bin/env python3
"""
Search across all Home Assistant backup archives for a text pattern.

Usage:
    python search_backups.py 'media_player.play_media'
    python search_backups.py --all 'some_pattern'
    python search_backups.py --files-only 'pattern'
    python search_backups.py -C 2 'pattern'
"""

import argparse
import os
import re
import sys
import tarfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import IO, NotRequired, TypedDict

from tools.backup_common import (
    BackupRecord,
    changelog_path_for,
    filter_backups,
    get_backups,
    iter_tarball_file_members,
)
from tools.common import get_env_int, non_negative_int, positive_int


class _MatchResult(TypedDict):
    """Shape of a backup search match, including optional context."""

    file: str
    line_num: int
    line: str
    context_before: NotRequired[list[str]]
    context_after: NotRequired[list[str]]


_NESTED_QUANTIFIER_RES = (
    re.compile(r"\([^)]*[+*][^)]*\)[+*]"),  # e.g. (a+)+, (.*)+
    re.compile(r"\([^)]*\{[^}]+\}[^)]*\)[+*]"),  # e.g. (a{1,3})+
)


def is_likely_unsafe_regex(pattern: str) -> bool:
    """Heuristic check for patterns that MIGHT cause ReDoS.

    Only catches the classic ``(a+)+`` / ``(a*)*`` shapes. NOT a complete
    ReDoS detector — pair with a watchdog timeout at the call site.
    """
    return any(expr.search(pattern) for expr in _NESTED_QUANTIFIER_RES)


def _search_file(
    extracted: IO[bytes],
    display_name: str,
    pattern: re.Pattern[str],
    context_lines: int,
    matches: list[_MatchResult],
) -> None:
    """Search one decoded archive member and assemble optional context."""
    context_before: deque[str] = deque(maxlen=context_lines)
    pending_after: list[tuple[_MatchResult, int]] = []

    for line_num, raw_line in enumerate(extracted, start=1):
        line = raw_line.decode("utf-8").rstrip("\r\n")

        if pending_after:
            remaining_pairs: list[tuple[_MatchResult, int]] = []
            for pending_match, remaining in pending_after:
                pending_match.setdefault("context_after", []).append(line)
                if remaining - 1 > 0:
                    remaining_pairs.append((pending_match, remaining - 1))
            pending_after = remaining_pairs

        if pattern.search(line):
            match_entry: _MatchResult = {
                "file": display_name,
                "line_num": line_num,
                "line": line,
            }
            if context_lines > 0:
                match_entry["context_before"] = list(context_before)
                match_entry["context_after"] = []
                pending_after.append((match_entry, context_lines))

            matches.append(match_entry)

        if context_lines > 0:
            context_before.append(line)


def search_backup(
    backup: BackupRecord,
    pattern: re.Pattern[str],
    yaml_only: bool = True,
    context_lines: int = 0,
) -> tuple[list[_MatchResult], bool]:
    """Search a single backup archive for a pattern. Returns (matches, unreadable)."""
    matches: list[_MatchResult] = []
    unreadable = False
    try:
        for display_name, extracted in iter_tarball_file_members(backup["path"]):
            try:
                with extracted:
                    if yaml_only and not (
                        display_name.endswith(".yaml") or display_name.endswith(".yml")
                    ):
                        continue
                    _search_file(
                        extracted,
                        display_name,
                        pattern,
                        context_lines,
                        matches,
                    )
            except UnicodeDecodeError:
                if (
                    yaml_only
                    or display_name.endswith(".yaml")
                    or display_name.endswith(".yml")
                ):
                    unreadable = True

    except (tarfile.TarError, OSError) as e:
        print(f"  Warning: Could not read {backup['filename']}: {e}", file=sys.stderr)
        return [], True

    return matches, unreadable


def search_changelog(
    backup: BackupRecord,
    pattern: re.Pattern[str],
    context_lines: int = 0,
) -> tuple[list[_MatchResult], bool]:
    """Search changelog paired with a backup archive. Returns (matches, unreadable)."""
    changelog_path = changelog_path_for(backup)
    if not changelog_path.is_file():
        return [], False

    matches: list[_MatchResult] = []
    try:
        with open(changelog_path, "rb") as f:
            _search_file(
                f,
                changelog_path.name,
                pattern,
                context_lines,
                matches,
            )
    except OSError as e:
        print(f"  Warning: Could not read {changelog_path.name}: {e}", file=sys.stderr)
        return [], True

    return matches, False


def _search_backups(
    backups: list[BackupRecord],
    pattern: re.Pattern[str],
    *,
    yaml_only: bool,
    context_lines: int,
    max_workers: int,
    changelog_mode: bool = False,
) -> list[tuple[BackupRecord, tuple[list[_MatchResult], bool]]]:
    """Search backups concurrently while retaining newest-first ordering."""
    results: list[tuple[BackupRecord, tuple[list[_MatchResult], bool]]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        if changelog_mode:
            futures = [
                (
                    backup,
                    executor.submit(
                        search_changelog,
                        backup,
                        pattern,
                        context_lines=context_lines,
                    ),
                )
                for backup in backups
            ]
        else:
            futures = [
                (
                    backup,
                    executor.submit(
                        search_backup,
                        backup,
                        pattern,
                        yaml_only=yaml_only,
                        context_lines=context_lines,
                    ),
                )
                for backup in backups
            ]

        for backup, future in futures:
            results.append((backup, future.result()))
    return results


def _render_results(
    results: list[tuple[BackupRecord, tuple[list[_MatchResult], bool]]],
    *,
    files_only: bool,
    context_lines: int,
) -> tuple[int, int]:
    """Render search results and return (matches, unreadable archives) counts."""
    unreadable_count = sum(
        1 for _backup, (_matches, unreadable) in results if unreadable
    )
    match_count = sum(1 for _backup, (matches, _u) in results if matches)

    for backup, (matches, _u) in results:
        date_str = backup["timestamp"].strftime("%b %d")
        if matches:
            print(f"  MATCH  {backup['filename']} ({date_str})")
            if not files_only:
                for m in matches:
                    if context_lines > 0 and "context_before" in m:
                        for ctx_line in m["context_before"]:
                            print(f"           {m['file']}:     {ctx_line}")
                    print(f"         {m['file']}:{m['line_num']}:{m['line']}")
                    if context_lines > 0 and "context_after" in m:
                        for ctx_line in m["context_after"]:
                            print(f"           {m['file']}:     {ctx_line}")
                print()
        elif _u:
            print(f"  ????  {backup['filename']} ({date_str}) unreadable")
        else:
            print(f"  ----   {backup['filename']} ({date_str})")

    unreadable_suffix = f" ({unreadable_count} unreadable)" if unreadable_count else ""
    print(
        f"\nFound in {match_count} of {len(results)} backups{unreadable_suffix}",
        file=sys.stderr,
    )
    return match_count, unreadable_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search across Home Assistant backup archives"
    )
    parser.add_argument("pattern", help="Text pattern to search for (regex)")
    parser.add_argument(
        "--all", "-a", action="store_true", help="Search all files, not just YAML"
    )
    parser.add_argument(
        "--changelogs",
        "--diffs",
        action="store_true",
        dest="changelogs",
        help="Search diff changelogs instead of tarball contents",
    )
    parser.add_argument(
        "--days",
        "-d",
        type=positive_int,
        default=None,
        help="Search only backups from the last N days",
    )
    parser.add_argument(
        "--limit",
        "-n",
        type=positive_int,
        default=None,
        help="Limit search to the N most recent backups",
    )
    parser.add_argument(
        "--files-only",
        "-l",
        action="store_true",
        help="Only show backup filenames, not matching lines",
    )
    parser.add_argument(
        "--context",
        "-C",
        type=non_negative_int,
        default=0,
        help="Number of context lines around matches (>= 0)",
    )
    args = parser.parse_args()

    if is_likely_unsafe_regex(args.pattern):
        print(
            "Invalid regex pattern: pattern appears unsafe "
            "(nested quantifiers can cause catastrophic backtracking)",
            file=sys.stderr,
        )
        return 1

    try:
        pattern = re.compile(args.pattern)
    except re.error as e:
        print(f"Invalid regex pattern: {e}", file=sys.stderr)
        return 1

    backups = get_backups()
    if not backups:
        print("No backups found", file=sys.stderr)
        return 1

    # Search newest first
    backups = list(reversed(backups))

    backups = filter_backups(backups, days=args.days, limit=args.limit)
    if not backups:
        print(
            "No matching backups found within the specified time/limit filter",
            file=sys.stderr,
        )
        return 0

    yaml_only = not args.all
    target_type = (
        "changelogs" if args.changelogs else ("all files" if args.all else "YAML files")
    )
    filter_notes: list[str] = []
    if args.days:
        filter_notes.append(f"last {args.days}d")
    if args.limit:
        filter_notes.append(f"limit {args.limit}")
    filter_str = f" [{' '.join(filter_notes)}]" if filter_notes else ""
    print(
        f"Searching {len(backups)} backups for: {args.pattern} "
        f"({target_type}){filter_str}\n",
        file=sys.stderr,
    )

    default_workers = min(32, (os.cpu_count() or 1) + 4)
    max_workers, worker_warning = get_env_int(
        "BACKUP_SEARCH_MAX_WORKERS", default_workers
    )
    if worker_warning:
        print(f"Warning: {worker_warning}", file=sys.stderr)

    results = _search_backups(
        backups,
        pattern,
        yaml_only=yaml_only,
        context_lines=args.context,
        max_workers=max_workers,
        changelog_mode=args.changelogs,
    )
    match_count, unreadable_count = _render_results(
        results, files_only=args.files_only, context_lines=args.context
    )
    return 1 if unreadable_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
