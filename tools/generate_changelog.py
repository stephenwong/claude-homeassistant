#!/usr/bin/env python3
"""
Generate a human-readable changelog showing what changed between consecutive backups.

Usage:
    python generate_changelog.py backups/ha_config_20260205_204808.tar.gz
    python generate_changelog.py --generate-all
"""

import argparse
import difflib
import functools
import sys
import tarfile
from pathlib import Path

from tools.backup_common import (
    BackupRecord,
    changelog_path_for,
    get_backups,
    iter_tarball_file_members,
)
from tools.common import atomic_write_text

# Files/directories to skip (noisy runtime state)
SKIP_PATTERNS = [
    ".storage/",
    "zigbee2mqtt/state.json",
    "zigbee2mqtt/log/",
    ".db",
    "__pycache__/",
    ".pyc",
]

# File extensions considered "interesting"
INTERESTING_EXTENSIONS = {
    ".yaml",
    ".yml",
    ".sh",
    ".py",
    ".json",
    ".conf",
    ".cfg",
    ".ini",
    ".txt",
}


def should_include(name: str) -> bool:
    """Check if a file should be included in the diff."""
    if Path(name).name == "secrets.yaml":
        return False
    for pattern in SKIP_PATTERNS:
        if pattern in name:
            return False
    # Include files with interesting extensions or no extension (likely config)
    ext = Path(name).suffix.lower()
    return ext in INTERESTING_EXTENSIONS or ext == ""


@functools.lru_cache(maxsize=2)
def extract_files(backup_path: Path) -> dict[str, str]:
    """Extract interesting text files from a backup archive. Returns {name: content}."""
    files = {}
    try:
        for name, extracted in iter_tarball_file_members(backup_path):
            if not should_include(name):
                continue
            try:
                with extracted:
                    files[name] = extracted.read().decode("utf-8")
            except UnicodeDecodeError:
                continue
    except (tarfile.TarError, OSError) as e:
        print(f"  Warning: Could not read {backup_path}: {e}", file=sys.stderr)
    return files


def _unified_diff(name: str, old: list[str], new: list[str]) -> list[str]:
    """Compute a unified diff list with HA-changelog-style a/ and b/ filenames."""
    return list(
        difflib.unified_diff(
            old,
            new,
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            lineterm="",
        )
    )


def _count_diff(lines: list[str]) -> tuple[int, int]:
    """Return (added, removed) line counts from a unified-diff list."""
    added = sum(
        1 for line in lines if line.startswith("+") and not line.startswith("+++ ")
    )
    removed = sum(
        1 for line in lines if line.startswith("-") and not line.startswith("--- ")
    )
    return added, removed


def _describe_file_change(
    name: str, previous: str | None, current: str | None
) -> tuple[str | None, str | None]:
    """Return a changed-file summary and its optional unified diff."""
    previous_lines = previous.splitlines() if previous is not None else []
    current_lines = current.splitlines() if current is not None else []

    if current is not None and previous is None:
        line_count = len(current_lines)
        diff = _unified_diff(name, previous_lines, current_lines)
        return f"  A {name} (+{line_count})", "\n".join(diff)

    if current is None and previous is not None:
        line_count = len(previous_lines)
        diff = _unified_diff(name, previous_lines, current_lines)
        return f"  D {name} (-{line_count})", "\n".join(diff)

    if current is not None and previous is not None and current_lines != previous_lines:
        diff_lines = _unified_diff(name, previous_lines, current_lines)
        if diff_lines:
            added, removed = _count_diff(diff_lines)
            return (
                f"  M {name} (+{added}, -{removed})",
                "\n".join(diff_lines),
            )

    return None, None


def _collect_file_changes(
    current_files: dict[str, str], previous_files: dict[str, str]
) -> list[tuple[str, str]]:
    """Return ordered ``(summary, diff)`` pairs for changed files."""
    changes = []
    all_names = sorted(set(current_files) | set(previous_files))
    for name in all_names:
        summary, diff = _describe_file_change(
            name, previous_files.get(name), current_files.get(name)
        )
        if summary is not None:
            changes.append((summary, diff or ""))
    return changes


def generate_changelog(
    backup: BackupRecord, previous_backup: BackupRecord | None
) -> str:
    """Generate changelog content comparing two backups."""
    lines = []
    lines.append(f"Backup: {backup['filename']}")

    if previous_backup:
        lines.append(f"Previous: {previous_backup['filename']}")
    else:
        lines.append("Previous: (none - initial backup)")

    lines.append(f"Date: {backup['timestamp'].strftime('%Y-%m-%d %H:%M:%S %z')}")
    lines.append("")

    current_files = extract_files(backup["path"])

    if not previous_backup:
        # First backup - list all files
        lines.append("Initial backup - all files:")
        for name in sorted(current_files.keys()):
            line_count = len(current_files[name].splitlines())
            lines.append(f"  A {name} ({line_count} lines)")
        return "\n".join(lines) + "\n"

    previous_files = extract_files(previous_backup["path"])

    changes = _collect_file_changes(current_files, previous_files)

    if not changes:
        lines.append("No changes detected.")
        return "\n".join(lines) + "\n"

    lines.append("Changed files:")
    lines.extend(summary for summary, _diff in changes)
    lines.append("")
    lines.append("---")

    for _summary, diff in changes:
        if diff:
            lines.append(diff)
            lines.append("")

    return "\n".join(lines) + "\n"


def _write_changelog(
    backup: BackupRecord, previous_backup: BackupRecord | None
) -> Path:
    """Write a changelog for *backup* against its already-selected predecessor."""
    changelog_file = changelog_path_for(backup)
    content = generate_changelog(backup, previous_backup)
    if not atomic_write_text(changelog_file, content):
        raise OSError(f"could not write changelog {changelog_file}")
    return changelog_file


def _select_predecessor(
    backup: BackupRecord, backups_list: list[BackupRecord]
) -> BackupRecord | None:
    """Return *backup*'s immediate predecessor in an oldest-first list."""
    for index, candidate in enumerate(backups_list):
        if candidate["filename"] == backup["filename"]:
            return backups_list[index - 1] if index else None
    raise ValueError(
        f"Backup {backup['filename']} not found in the backup list — "
        "cannot compute a predecessor"
    )


def generate_for_backup(backup: BackupRecord, backups_list: list[BackupRecord]) -> Path:
    """Generate changelog for a single backup, finding its predecessor."""
    previous = _select_predecessor(backup, backups_list)
    return _write_changelog(backup, previous)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate changelogs for Home Assistant backups"
    )
    parser.add_argument("backup", nargs="?", help="Path to a specific backup")
    parser.add_argument(
        "--generate-all",
        action="store_true",
        help="Generate changelogs for all backups missing one",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing changelog file",
    )
    args = parser.parse_args()

    if args.generate_all and args.backup:
        parser.error("--generate-all ignores a positional BACKUP; pick one")

    backups = get_backups()  # sorted oldest first
    if not backups:
        print("No backups found", file=sys.stderr)
        return 1

    if args.generate_all:
        generated = 0
        skipped = 0
        failed = 0
        for index, backup in enumerate(backups):
            cl_path = changelog_path_for(backup)
            if cl_path.exists() and not args.force:
                skipped += 1
                continue
            print(f"Generating changelog for {backup['filename']}...", file=sys.stderr)
            previous = backups[index - 1] if index else None
            try:
                _write_changelog(backup, previous)
            except OSError as e:
                print(f"Failed to generate changelog: {e}", file=sys.stderr)
                failed += 1
                continue
            generated += 1

        print(
            f"\nGenerated {generated} changelog(s), skipped {skipped} existing",
            file=sys.stderr,
        )
        return 1 if failed else 0

    if not args.backup:
        parser.error("Provide a backup path or use --generate-all")

    # Find the backup in our list
    backup_path = Path(args.backup)
    target = None
    for b in backups:
        if b["path"] == backup_path or b["filename"] == backup_path.name:
            target = b
            break

    if not target:
        print(f"Backup not found: {args.backup}", file=sys.stderr)
        return 1

    try:
        cl_path = generate_for_backup(target, backups)
    except OSError as e:
        print(f"Failed to generate changelog: {e}", file=sys.stderr)
        return 1
    print(f"Changelog written to {cl_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
