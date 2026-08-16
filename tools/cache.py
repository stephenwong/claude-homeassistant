"""Validator result cache — skips re-validation when no relevant files changed."""

import hashlib
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from tools.common import atomic_write_text

CACHE_DIR_NAME = ".cache/validators"

CACHE_SCHEMA_VERSION = 1


def compute_hash_status(config_dir: Path, patterns: list[str]) -> tuple[str, bool]:
    """Compute a SHA256 hash over all files matching the given glob patterns.

    Files are sorted for deterministic ordering. Missing files/patterns are
    silently skipped (no error). The completeness flag is false when a
    matched file cannot be read, because the resulting digest is not safe for
    cache identity.
    """
    sha = hashlib.sha256()
    complete = True
    paths: list[Path] = []
    for pattern in patterns:
        for p in config_dir.glob(pattern):
            if p.is_file():
                paths.append(p)
    # Deduplicate in case patterns overlap
    seen: set[Path] = set()
    for p in sorted(paths):
        if p in seen:
            continue
        seen.add(p)
        sha.update(str(p.relative_to(config_dir)).encode())
        try:
            sha.update(p.read_bytes())
        except OSError as e:
            print(f"WARN: skipping {p} in hash: {e}", file=sys.stderr)
            complete = False
            continue
    return sha.hexdigest(), complete


def compute_hash(config_dir: Path, patterns: list[str]) -> str:
    """Compute the public digest for matching files."""
    digest, _complete = compute_hash_status(config_dir, patterns)
    return digest


def cache_path(config_dir: Path, name: str) -> Path:
    """Return the path to the cache file for a given validator name."""
    return config_dir / CACHE_DIR_NAME / f"{name}.json"


def _load_json(path: Path, *, retries: int = 0):
    """Read a JSON cache file, returning ``None`` when it is unusable."""
    if not path.is_file():
        return None

    for attempt in range(retries + 1):
        try:
            with path.open(encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            if attempt < retries:
                time.sleep(0.1)
                continue
            return None
        except OSError, ValueError:
            return None
    return None


def _write_json(path: Path, data) -> None:
    """Write JSON cache data through the shared atomic text boundary."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(
            f"WARN: failed to create cache directory for {path}: {e}", file=sys.stderr
        )
        return
    atomic_write_text(path, json.dumps(data))


def load_cache(config_dir: Path, name: str) -> dict | None:
    """Load a cached validator result. Returns None on any failure.

    Retries once after 100ms on transient JSON decode errors (per AGENTS.md
    convention for atomic-write safety). Does NOT create directories — only
    reads from existing cache files.
    """
    data = _load_json(cache_path(config_dir, name), retries=1)
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    if type(data.get("schema")) is not int or data["schema"] != CACHE_SCHEMA_VERSION:
        return None
    if not isinstance(data.get("hash"), str):
        return None
    if type(data.get("passed")) is not bool:
        return None

    if "duration" in data:
        duration = data["duration"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
        ):
            return None
    if "stderr" in data and not isinstance(data["stderr"], str):
        return None
    for metadata in ("validator", "timestamp"):
        if metadata in data and not isinstance(data[metadata], str):
            return None
    return data


def save_cache(
    config_dir: Path,
    name: str,
    validator_name: str,
    file_hash: str,
    passed: bool,
    duration: float,
    stderr: str = "",
) -> None:
    """Save a validator result to the cache atomically.

    Writes to a temp file then ``os.replace``s it into place, so a crash
    mid-write never leaves a truncated cache file.  Writes a warning to
    stderr on persistent failure.
    """
    data = {
        "schema": CACHE_SCHEMA_VERSION,
        "validator": validator_name,
        "hash": file_hash,
        "passed": passed,
        "timestamp": datetime.now(UTC).isoformat(),
        "duration": round(duration, 4),
        "stderr": stderr,
    }
    _write_json(cache_path(config_dir, name), data)
