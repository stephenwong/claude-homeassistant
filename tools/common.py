"""Shared utilities for HA configuration tools."""

import argparse
import contextlib
import os
import re
import stat
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TextIO
from urllib.parse import urlparse

from tools.validators.base import (  # noqa: F401 — re-exported
    HAYamlLoader,
    ValidatorBase,
    format_diagnostics,
)

DEFAULT_HA_URL = "http://homeassistant.local:8123"
DEFAULT_HA_TIMEOUT = 10

_ENTITY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
DEFAULT_SUMMARY_MAX_CHARS = 8000


def load_env_file(path: Path | None = None) -> None:
    """Load environment variables from .env file.

    Does NOT override variables already present in ``os.environ`` (standard
    python-dotenv convention) — shell-set values win, so a freshly rotated
    HA_TOKEN isn't silently overwritten by a stale .env entry.

    Args:
        path: Optional explicit path to the .env file. Defaults to
            ``<project_root>/.env``.
    """
    env_file = path or (Path(__file__).parent.parent / ".env")
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = value.strip().strip('"').strip("'")


def get_env_int(name: str, default: int, *, minimum: int = 1) -> tuple[int, str | None]:
    """Read an integer env var with validation and fallback.

    Returns:
        A tuple of (value, warning). warning is None when the parsed value is valid.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default, None

    try:
        value = int(raw)
    except ValueError:
        return default, f"{name} must be an integer, got {raw!r}; using {default}"

    if value < minimum:
        return (
            default,
            f"{name} must be >= {minimum}, got {value}; using {default}",
        )

    return value, None


def get_ha_config(*, warning_stream: TextIO | None = None) -> tuple[str, str, int]:
    """Load shared Home Assistant URL, token, and request timeout settings.

    Environment variables take precedence over values loaded from ``.env``.
    When provided, *warning_stream* receives the existing timeout warning text.
    """
    load_env_file()
    url = os.getenv("HA_URL", DEFAULT_HA_URL)
    token = os.getenv("HA_TOKEN", "")
    timeout, warning = get_env_int("HA_REQUEST_TIMEOUT", DEFAULT_HA_TIMEOUT)
    if warning and warning_stream is not None:
        print(f"⚠️  {warning}", file=warning_stream)
    return url, token, timeout


def validate_ha_url(ha_url: str) -> str | None:
    """Validate HA URL format and return an error message when invalid."""
    try:
        parsed = urlparse(ha_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "HA_URL must include a valid hostname and port"
    if parsed.scheme not in {"http", "https"}:
        return "HA_URL must start with http:// or https://"
    if not hostname:
        return "HA_URL must include a hostname"
    if port is not None and not 0 < port <= 65535:
        return "HA_URL must include a valid hostname and port"
    return None


def _is_tty() -> bool:
    """Check if stdout is connected to a terminal.

    Returns False when stdout is piped, redirected to a file, or absent
    (e.g. when called from an agent or CI runner). Never raises.
    """
    try:
        return sys.stdout is not None and sys.stdout.isatty()
    except AttributeError, OSError, TypeError, ValueError:
        return False


def _int_at_least(value: str, minimum: int, message: str) -> int:
    """Parse an integer and reject values below *minimum*."""
    n = int(value)
    if n < minimum:
        raise argparse.ArgumentTypeError(message)
    return n


def positive_int(value: str) -> int:
    """Argparse type: reject values < 1."""
    return _int_at_least(value, 1, "value must be >= 1")


def non_negative_int(value: str) -> int:
    """Argparse type: reject values < 0."""
    return _int_at_least(value, 0, "value must be >= 0")


def positive_float(value: str) -> float:
    """Argparse type: reject values <= 0."""
    f = float(value)
    if f <= 0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return f


def resolve_summary(args: argparse.Namespace) -> bool:
    """Resolve summary/verbose mode from argparse --summary/--no-summary flags.

    Logic:
        --summary       → True
        --no-summary    → False
        both            → warn on stderr, treat as --summary
        neither         → True when non-TTY, False when TTY

    Returns:
        True for compact/summary output, False for verbose.
    """
    explicit_summary = bool(getattr(args, "summary", False))
    explicit_no_summary = bool(getattr(args, "no_summary", False))
    if explicit_summary and explicit_no_summary:
        print(
            "WARN: conflicting --summary / --no-summary; using --summary",
            file=sys.stderr,
        )
    if explicit_summary:
        return True
    if explicit_no_summary:
        return False
    return not _is_tty()


def resolve_max_chars(args: argparse.Namespace, summary: bool) -> int | None:
    """Resolve effective --max-chars: explicit flag > HA_CLI_MAX_CHARS env > default.

    Returns None when no cap should apply. ``--max-chars 0`` always disables.
    """
    explicit = getattr(args, "max_chars", None)
    if explicit is not None:
        return explicit if explicit > 0 else None

    env_val, _warning = get_env_int("HA_CLI_MAX_CHARS", 0)
    if env_val > 0:
        return env_val

    if summary:
        return DEFAULT_SUMMARY_MAX_CHARS
    return None


def add_output_shape_args(
    parser: argparse.ArgumentParser,
    *,
    first: bool = True,
    pick: bool = True,
    max_chars: bool = True,
) -> None:
    """Attach the standard token-reduction flags to *parser*."""
    if first:
        parser.add_argument(
            "--first",
            metavar="N",
            type=positive_int,
            help="Keep only the first N items",
        )
    if pick:
        parser.add_argument(
            "--pick",
            metavar="FIELDS",
            help="Keep only specified JSON keys (comma-separated)",
        )
    if max_chars:
        parser.add_argument(
            "--max-chars",
            metavar="N",
            type=non_negative_int,
            help=(
                "Truncate compact JSON above N characters "
                f"(0 disables; default {DEFAULT_SUMMARY_MAX_CHARS} in summary)"
            ),
        )


def add_summary_args(parser: argparse.ArgumentParser) -> None:
    """Attach the standard --summary / --no-summary mutually-independent flags.

    Both flags use ``store_true``; ``resolve_summary()`` handles the
    both-set conflict at resolution time (warns + treats as --summary).
    """
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Compact output; auto-detected when stdout is not a TTY",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Force verbose output even when stdout is piped",
    )


def add_config_dir_arg(parser: argparse.ArgumentParser, *, help: str) -> None:
    """Attach the shared ``--config``/``-c`` directory option to a command."""
    parser.add_argument(
        "--config",
        "-c",
        default="config",
        help=help,
    )


def _atomic_replace(
    path: Path,
    write_temp: Callable[[Path], None],
    *,
    validate: Callable[[Path], None] | None = None,
    temp_path: Path | None = None,
    temp_prefix: str = "tmp",
    temp_suffix: str = "",
) -> None:
    """Write a sibling temporary file, optionally validate it, then replace *path*.

    The callbacks deliberately own serialization and validation so their exception
    types and return contracts remain with the caller. This helper owns only the
    shared temporary-file, permission, replacement, and cleanup lifecycle.
    """
    tmp_path = temp_path
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    try:
        if tmp_path is None:
            with tempfile.NamedTemporaryFile(
                dir=path.parent,
                prefix=temp_prefix,
                suffix=temp_suffix,
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
        write_temp(tmp_path)
        if mode is not None:
            os.chmod(tmp_path, mode)
        if validate is not None:
            validate(tmp_path)
        os.replace(tmp_path, path)
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)


def atomic_write_text(path: Path, content: str) -> bool:
    """Write *content* to *path* atomically via temp file + ``os.replace``.

    Writes to a sibling temp file (``<path>.tmp``), flushes, fsyncs, then
    atomically renames into place. Returns ``True`` on success. On ``OSError``,
    warns to stderr, cleans up the temp file, and returns ``False``.

    The temp file is named ``path.with_suffix(path.suffix + ".tmp")``, which
    preserves the extension (e.g. ``foo.json`` → ``foo.json.tmp``).
    """
    tmp = path.with_suffix(path.suffix + ".tmp")

    def write_temp(temp_path: Path) -> None:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

    try:
        _atomic_replace(
            path,
            write_temp,
            temp_path=tmp,
        )
        return True
    except OSError as e:
        print(f"WARN: failed to write {path}: {e}", file=sys.stderr)
        return False


class HARequestError(Exception):
    """Raised when a Home Assistant REST API request fails."""


class MissingTokenError(HARequestError):
    """Raised when ``HA_TOKEN`` is not set in the environment or ``.env``.

    Subclasses :class:`HARequestError` so existing ``except HARequestError``
    catchers continue to work — only callers that want to distinguish the
    token-missing case need the narrower type.
    """


def fail_stderr(msg: str) -> int:
    """Print ``\u274c <msg>`` to stderr and return exit code 1."""
    print(f"\u274c {msg}", file=sys.stderr)
    return 1
