"""Shared primitives for backup CLIs (prune, changelog, search).

Holds the backup-directory location, filename parsing, listing, and
tarball iteration so the three backup CLIs don't reach into each
other's modules for shared types.
"""

import os
import re
import tarfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import IO, Literal, TypedDict

BACKUP_DIR = Path(
    os.environ.get("BACKUP_DIR", Path(__file__).parent.parent / "backups")
)
_BACKUP_RE = re.compile(r"^ha_config_(\d{8})_(\d{6})\.tar\.gz$")
_CHANGELOG_RE = re.compile(r"^ha_config_\d{8}_\d{6}\.changelog$")


class BackupRecord(TypedDict):
    """Metadata describing one parsed backup archive."""

    path: Path
    filename: str
    timestamp: datetime


ArtifactKind = Literal["backup", "changelog"]

_ARTIFACT_GLOB: dict[ArtifactKind, str] = {
    "backup": "*.tar.gz",
    "changelog": "*.changelog",
}


def changelog_path_for(backup: BackupRecord) -> Path:
    """Return the changelog path paired with *backup* (sibling of the tarball)."""
    return backup["path"].parent / (
        backup["filename"].removesuffix(".tar.gz") + ".changelog"
    )


def backup_path_for_changelog(changelog: Path) -> Path:
    """Return the backup path paired with a changelog file."""
    return changelog.parent / (changelog.name.removesuffix(".changelog") + ".tar.gz")


def parse_backup_filename(filename: str) -> datetime | None:
    """Parse backup filename and return datetime object."""
    match = _BACKUP_RE.match(filename)
    if not match:
        return None

    date_str = match.group(1)  # YYYYMMDD
    time_str = match.group(2)  # HHMMSS

    try:
        return datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S").astimezone()
    except ValueError:
        return None


def _is_managed_artifact(path: Path, kind: ArtifactKind) -> bool:
    """Return whether *path* is a canonical, non-symlink managed artifact."""
    if path.is_symlink() or not path.is_file():
        return False
    if kind == "backup":
        return parse_backup_filename(path.name) is not None
    if kind == "changelog":
        if _CHANGELOG_RE.fullmatch(path.name) is None:
            return False
        backup_name = path.name.removesuffix(".changelog") + ".tar.gz"
        return parse_backup_filename(backup_name) is not None
    return False


def iter_managed_artifacts(kind: ArtifactKind) -> Iterator[Path]:
    """Yield canonical, non-symlink managed artifacts of *kind*."""
    if not BACKUP_DIR.exists():
        return

    for path in BACKUP_DIR.glob(_ARTIFACT_GLOB[kind]):
        if _is_managed_artifact(path, kind):
            yield path


def get_backups() -> list[BackupRecord]:
    """Get all backup files with their timestamps."""
    backups: list[BackupRecord] = []
    for file in iter_managed_artifacts("backup"):
        timestamp = parse_backup_filename(file.name)
        if timestamp is not None:
            backups.append(
                {"path": file, "filename": file.name, "timestamp": timestamp}
            )

    return sorted(backups, key=lambda x: (x["timestamp"], x["filename"]))


def iter_tarball_file_members(path: Path) -> Iterator[tuple[str, IO[bytes]]]:
    """Yield ``(normalized_name, file_obj)`` for each regular file in a gzipped tarball.

    Skips non-file members (directories, symlinks) and members where
    ``extractfile()`` returns None or raises KeyError. Member names are
    normalized by stripping a leading ``./`` prefix.

    Raises ``tarfile.TarError`` or ``OSError`` if the archive cannot be
    opened. Callers should catch these and apply their own error-reporting
    policy (the two callers have different return-value contracts on error).

    The caller MUST consume each yielded file_obj before advancing the
    iterator — the file_obj is only valid while the underlying tarfile
    handle is open, and it is closed when the iterator advances or exits.
    """
    with tarfile.open(path, "r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            name = member.name
            while name.startswith("./"):
                name = name[2:]
            try:
                extracted = tar.extractfile(member)
            except KeyError:
                continue
            if extracted is None:
                continue
            yield name, extracted
