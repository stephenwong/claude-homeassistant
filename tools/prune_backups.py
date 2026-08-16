#!/usr/bin/env python3
"""
Prune Home Assistant configuration backups with smart retention.

Retention rules:
- Keep all backups from the recent retention window
- Keep one backup per day through the daily retention boundary (latest each day)
- Keep one backup per week from the weekly retention boundary onward (latest each week)
"""

import argparse
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from tools import backup_common
from tools.backup_common import BackupRecord, changelog_path_for, get_backups
from tools.common import non_negative_int

RECENT_MAX_AGE_DAYS = 7
DAILY_MAX_AGE_DAYS = 30
WEEKLY_MIN_AGE_DAYS = DAILY_MAX_AGE_DAYS + 1


class RetentionGroups(TypedDict):
    """Backups grouped according to the retention policy."""

    keep_all: list[BackupRecord]
    daily: defaultdict[str, list[BackupRecord]]
    weekly: defaultdict[str, list[BackupRecord]]


def group_by_retention_period(
    backups: list[BackupRecord], now: datetime
) -> RetentionGroups:
    """Group backups into retention periods."""
    groups: RetentionGroups = {
        "keep_all": [],  # Through RECENT_MAX_AGE_DAYS days old
        "daily": defaultdict(list),  # Through DAILY_MAX_AGE_DAYS days old
        "weekly": defaultdict(list),  # From WEEKLY_MIN_AGE_DAYS days old
    }

    for backup in backups:
        age = now - backup["timestamp"]

        if age.days <= RECENT_MAX_AGE_DAYS:
            # Keep all from the recent retention window.
            groups["keep_all"].append(backup)
        elif age.days <= DAILY_MAX_AGE_DAYS:
            # Group by day (YYYY-MM-DD)
            day_key = backup["timestamp"].strftime("%Y-%m-%d")
            groups["daily"][day_key].append(backup)
        elif age.days >= WEEKLY_MIN_AGE_DAYS:
            # Group by ISO week (%G = ISO year, %V = ISO week 01-53)
            week_key = backup["timestamp"].strftime("%G-W%V")
            groups["weekly"][week_key].append(backup)

    return groups


def _keep_latest_per_group(
    grouped: Mapping[str, list[BackupRecord]],
    to_keep: list[BackupRecord],
    to_delete: list[BackupRecord],
) -> None:
    """Keep the latest entry from each group; rest go to delete list."""
    for items in grouped.values():
        if not items:
            continue
        ordered = sorted(items, key=lambda x: x["timestamp"], reverse=True)
        to_keep.append(ordered[0])
        to_delete.extend(ordered[1:])


def apply_retention(
    groups: RetentionGroups,
) -> tuple[list[BackupRecord], list[BackupRecord]]:
    """Apply retention rules and return lists of files to keep/delete."""
    to_keep: list[BackupRecord] = []
    to_delete: list[BackupRecord] = []

    # Keep all recent backups through RECENT_MAX_AGE_DAYS.
    to_keep.extend(groups["keep_all"])

    # Keep one per day through DAILY_MAX_AGE_DAYS, one per week afterward.
    _keep_latest_per_group(groups["daily"], to_keep, to_delete)
    _keep_latest_per_group(groups["weekly"], to_keep, to_delete)

    return to_keep, to_delete


def format_size(size_bytes: int | float) -> str:
    """Format bytes as human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f}TB"


def _backup_size_and_age(backup: BackupRecord, now: datetime) -> tuple[int, int]:
    """Return a backup's best-effort size in bytes and age in days."""
    try:
        size = backup["path"].stat().st_size
    except OSError:
        size = 0
    return size, (now - backup["timestamp"]).days


def _find_orphaned_changelogs() -> list[Path]:
    """Return managed changelogs that have no matching backup archive."""
    backup_paths = set(backup_common.iter_managed_artifacts("backup"))
    orphans = []
    for changelog in backup_common.iter_managed_artifacts("changelog"):
        tar_path = backup_common.backup_path_for_changelog(changelog)
        if tar_path not in backup_paths:
            orphans.append(changelog)
    return orphans


def _clean_orphaned_changelogs(dry_run: bool = False) -> list[Path]:
    """Remove orphaned changelogs and return the paths initially discovered."""
    orphans = _find_orphaned_changelogs()
    if orphans:
        print(f"\nOrphaned changelogs: {len(orphans)}", file=sys.stderr)
        for orphan in orphans:
            if dry_run:
                print(f"  Would delete: {orphan.name}", file=sys.stderr)
            else:
                try:
                    orphan.unlink()
                except OSError as e:
                    print(
                        f"⚠️ failed to delete orphaned changelog {orphan.name}: {e}",
                        file=sys.stderr,
                    )
                else:
                    print(f"  Deleted: {orphan.name}", file=sys.stderr)
    return orphans


def clean_orphaned_changelogs(dry_run: bool = False) -> int:
    """Remove changelog files that have no matching backup tar.gz."""
    return len(_clean_orphaned_changelogs(dry_run=dry_run))


def _clean_orphans_and_check(dry_run: bool) -> bool:
    """Clean orphaned changelogs and report whether apply mode completed."""
    found = _clean_orphaned_changelogs(dry_run=dry_run)
    if dry_run or not found:
        return True

    remaining = [path for path in found if path.exists()]
    if remaining:
        print(
            f"\n✗ Failed to remove {len(remaining)} orphaned changelog(s)",
            file=sys.stderr,
        )
        return False
    return True


def _format_backup_line(backup: BackupRecord, size: int, age_label: str) -> str:
    """Format a backup's common display fields with a caller-provided age label."""
    return f"  - {backup['filename']} ({format_size(size)}, {age_label})"


def _format_delete_line(
    backup: BackupRecord,
    now: datetime,
    stats: tuple[int, int] | None = None,
) -> str:
    """Format a to-be-deleted backup for display."""
    size, age_days = _backup_size_and_age(backup, now) if stats is None else stats
    return _format_backup_line(backup, size, f"{age_days} days old")


def _format_keep_line(
    backup: BackupRecord,
    now: datetime,
    stats: tuple[int, int] | None = None,
) -> str:
    """Format a retained backup for display."""
    size, age_days = _backup_size_and_age(backup, now) if stats is None else stats
    if age_days == 0:
        age_str = "today"
    elif age_days == 1:
        age_str = "yesterday"
    else:
        age_str = f"{age_days} days ago"
    return _format_backup_line(backup, size, age_str)


def _validate_deletion_safety(
    backups: list[BackupRecord], to_delete: list[BackupRecord], min_keep: int
) -> str | None:
    """Return an error message if the deletion plan is unsafe, else None."""
    if len(to_delete) >= len(backups):
        return (
            "❌ Refusing to delete: would remove all backups "
            f"(len(to_delete)={len(to_delete)} >= len(backups)={len(backups)}). "
            "Check retention settings."
        )
    remaining = len(backups) - len(to_delete)
    if remaining < min_keep:
        return (
            f"❌ Refusing to delete: would leave {remaining} "
            f"backup(s), below --min-keep {min_keep}."
        )
    return None


def _delete_backups(to_delete: list[BackupRecord]) -> int:
    """Delete each backup and its changelog sibling; return error count."""
    errors = 0
    for backup in to_delete:
        try:
            backup["path"].unlink()
        except OSError as e:
            print(f"⚠️ failed to delete {backup['filename']}: {e}", file=sys.stderr)
            errors += 1
            continue
        try:
            changelog_path = changelog_path_for(backup)
            changelog_path.unlink(missing_ok=True)
        except OSError as e:
            print(
                f"⚠️ failed to delete changelog for {backup['filename']}: {e}",
                file=sys.stderr,
            )
            errors += 1
        print(f"Deleted: {backup['filename']}")
    return errors


def _print_retention_summary(
    to_keep: list[BackupRecord], to_delete: list[BackupRecord]
) -> None:
    """Print the high-level keep/delete counts."""
    print("\nRetention Summary:", file=sys.stderr)
    print(f"  - Keeping {len(to_keep)} backup(s)", file=sys.stderr)
    print(f"  - Deleting {len(to_delete)} backup(s)", file=sys.stderr)


def _print_backup_list(
    backups: list[BackupRecord],
    now: datetime,
    header: str,
    line_formatter: Callable[[BackupRecord, datetime, tuple[int, int]], str],
    *,
    reverse: bool = False,
    file=sys.stdout,
) -> int:
    """Print formatted backup entries with a header and return total size in bytes."""
    print(header, file=sys.stderr)
    total_size = 0
    for backup in sorted(backups, key=lambda x: x["timestamp"], reverse=reverse):
        stats = _backup_size_and_age(backup, now)
        total_size += stats[0]
        print(line_formatter(backup, now, stats), file=file)
    return total_size


def _print_delete_preview(to_delete: list[BackupRecord], now: datetime) -> None:
    """Print the delete preview and total space freed."""
    total_size = _print_backup_list(
        to_delete,
        now,
        "\nBackups to delete:",
        _format_delete_line,
        reverse=False,
        file=sys.stdout,
    )
    print(f"\nTotal space to free: {format_size(total_size)}")


def _print_keep_summary(to_keep: list[BackupRecord], now: datetime) -> None:
    """Print the retained backup summary."""
    _print_backup_list(
        to_keep,
        now,
        "\nRetained backups:",
        _format_keep_line,
        reverse=True,
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prune Home Assistant backups")
    parser.add_argument(
        "--apply",
        "--execute",
        dest="apply",
        action="store_true",
        help="Actually delete files (default: dry-run — no files are removed).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run (default behaviour; accepted for clarity).",
    )
    parser.add_argument(
        "--min-keep",
        type=non_negative_int,
        default=3,
        help="Refuse to delete if fewer than N backups would remain (default: 3).",
    )
    args = parser.parse_args(argv)
    apply_deletes = args.apply and not args.dry_run

    print("Home Assistant Backup Retention Pruner", file=sys.stderr)
    if not apply_deletes:
        print(
            "(DRY RUN — no files will be deleted; pass --apply to delete)",
            file=sys.stderr,
        )
    print("=" * 50, file=sys.stderr)

    backups = get_backups()

    if not backups:
        print("(no backups found — nothing to prune)", file=sys.stderr)
        return int(not _clean_orphans_and_check(dry_run=not apply_deletes))

    print(f"\nFound {len(backups)} backup(s)", file=sys.stderr)

    now = datetime.now().astimezone()
    groups = group_by_retention_period(backups, now)
    to_keep, to_delete = apply_retention(groups)

    _print_retention_summary(to_keep, to_delete)

    if to_delete:
        _print_delete_preview(to_delete, now)

        # Defense-in-depth: never empty the directory.
        if apply_deletes:
            safety_error = _validate_deletion_safety(backups, to_delete, args.min_keep)
            if safety_error:
                print(safety_error, file=sys.stderr)
                return 1

        if apply_deletes:
            errors = _delete_backups(to_delete)

            if errors:
                print(
                    f"\n✗ Deleted {len(to_delete) - errors}, failed {errors}",
                    file=sys.stderr,
                )
                _clean_orphans_and_check(dry_run=not apply_deletes)
                return 1

            print(f"\n✓ Successfully deleted {len(to_delete)} backup(s)")
    else:
        print("\n✓ No backups need to be deleted")

    if to_keep:
        _print_keep_summary(to_keep, now)

    return int(not _clean_orphans_and_check(dry_run=not apply_deletes))


if __name__ == "__main__":
    raise SystemExit(main())
