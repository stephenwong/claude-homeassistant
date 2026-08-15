"""Shared test helpers for HA config test suite."""

import io
import json
import subprocess
import tarfile
from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import requests
import yaml

from tools.backup_common import BackupRecord


def make_tar(
    tmp_path: Path,
    files: dict[str, Any],
    name: str = "test.tar.gz",
    *,
    symlinks: dict[str, str] | None = None,
) -> Path:
    """Create a gzipped tar archive with text, binary, dirs, or symlinks."""
    tar_path = tmp_path / name
    with tarfile.open(tar_path, "w:gz") as tar:
        for filename, content in files.items():
            if content is None:
                info = tarfile.TarInfo(name=filename)
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif isinstance(content, bytes):
                info = tarfile.TarInfo(name=filename)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            else:
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name=filename)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        if symlinks:
            for linkname, target in symlinks.items():
                info = tarfile.TarInfo(name=linkname)
                info.type = tarfile.SYMTYPE
                info.linkname = target
                tar.addfile(info)
    return tar_path


def make_backup_record(path: Path, filename: str, timestamp: datetime) -> BackupRecord:
    """Build complete metadata for one backup archive."""
    return {"path": path, "filename": filename, "timestamp": timestamp}


def create_test_backup(
    backup_dir: Path,
    name: str,
    files: dict[str, Any],
    timestamp: datetime | None = None,
    *,
    symlinks: dict[str, str] | None = None,
) -> tuple[Path, BackupRecord]:
    """Create a backup tar archive and corresponding BackupRecord."""
    tar_path = make_tar(backup_dir, files, name=name, symlinks=symlinks)
    ts = timestamp or datetime.fromtimestamp(tar_path.stat().st_mtime)
    record = make_backup_record(tar_path, name, ts)
    return tar_path, record


def write_yaml(config_dir: Path, data: Any, filename: str = "automations.yaml") -> Path:
    """Write a YAML fixture and return its path."""
    path = config_dir / filename
    with path.open("w") as file:
        yaml.dump(data, file)
    return path


def write_storage_registry(
    config_dir: Path,
    filename: str,
    list_key: str,
    entries: list[dict[str, Any]],
) -> Path:
    """Write an HA .storage JSON registry fixture and return its path."""
    storage_dir = config_dir / ".storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    path = storage_dir / filename
    payload = {
        "version": 1,
        "minor_version": 1,
        "key": filename,
        "data": {list_key: entries},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def write_storage_registries(
    config_dir: Path,
    *,
    entities: list[dict[str, Any]] | None = None,
    devices: list[dict[str, Any]] | None = None,
    areas: list[dict[str, Any]] | None = None,
    entity_entries: list[dict[str, Any]] | None = None,
) -> None:
    """Write standard HA .storage registries (entity, device, area) to config_dir."""
    ent_list = entity_entries if entity_entries is not None else entities
    if ent_list is not None:
        write_storage_registry(config_dir, "core.entity_registry", "entities", ent_list)
    if devices is not None:
        write_storage_registry(config_dir, "core.device_registry", "devices", devices)
    if areas is not None:
        write_storage_registry(config_dir, "core.area_registry", "areas", areas)


_MISSING = object()


def make_response(
    data: Any = _MISSING,
    *,
    status: int = 200,
    headers: Any = None,
    content_type: str | None = None,
    ct: str | None = None,
    text: str | None = None,
    json_data: Any = _MISSING,
) -> requests.Response:
    """Build a real HTTP response for client and command tests."""
    if ct is not None:
        content_type = ct
    if data is not _MISSING and json_data is not _MISSING:
        raise ValueError("provide either data or json_data, not both")
    if data is not _MISSING and (
        content_type is None or "json" in content_type.lower()
    ):
        json_data = data
    if json_data is not _MISSING:
        body = json.dumps(json_data)
        content_type = content_type or "application/json"
    elif data is not _MISSING:
        body = str(data)
    else:
        body = "" if text is None else text
    response = requests.Response()
    response.status_code = status
    response._content = body.encode("utf-8")
    response.headers.update(
        headers or ({} if content_type is None else {"Content-Type": content_type})
    )
    response.json = MagicMock(side_effect=ValueError("invalid JSON"))
    if json_data is not _MISSING:
        response.json = MagicMock(return_value=json_data)
    return response


def make_completed_process(
    args: Any = "git",
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Build a real subprocess result for command-boundary tests."""
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def mock_json_client(
    result: Any = None,
    *,
    side_effect: Exception | None = None,
    post_response: Any = None,
) -> MagicMock:
    """Create an HA client mock for a JSON or POST endpoint."""
    from tools.ha.client import HAClient

    client = MagicMock(spec=HAClient)
    client.close = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.timeout = 30
    if side_effect is None:
        client.get_json.return_value = result
    else:
        client.get_json.side_effect = side_effect
    client.post.return_value = (
        post_response if post_response is not None else make_response()
    )
    return client


def mock_offline_client(error_msg: str = "offline") -> MagicMock:
    """Create an HA client mock that fails requests with HARequestError."""
    from tools.ha.client import HARequestError

    return mock_json_client(side_effect=HARequestError(error_msg))


def _has_diagnostic(validator: Any, severity: str, text: str) -> bool:
    """Check if validator has a diagnostic in severity list matching text."""
    diagnostics = getattr(validator, severity, [])
    return any(text.lower() in str(d).lower() for d in diagnostics)


def assert_diagnostic(validator: Any, severity: str, text: str) -> None:
    """Assert that a validator emitted a diagnostic containing ``text``."""
    diagnostics = getattr(validator, severity, [])
    assert _has_diagnostic(validator, severity, text), (
        f"No {severity} diagnostic contained {text!r}: {diagnostics!r}"
    )


def assert_no_diagnostic(
    validator: Any, severity: str, text: str | None = None
) -> None:
    """Assert that a validator emitted no diagnostics, optionally matching text."""
    diagnostics = getattr(validator, severity, [])
    if text is None:
        assert not diagnostics, (
            f"Expected no {severity} diagnostics, got: {diagnostics!r}"
        )
    else:
        assert not _has_diagnostic(validator, severity, text), (
            f"Expected no {severity} diagnostic with {text!r}, got: {diagnostics!r}"
        )


def make_parser() -> tuple[ArgumentParser, Any]:
    """Create a parser with a subparser group.

    Returns ``(parser, subparsers)`` so callers can register a subcommand
    and then invoke ``parser.parse_args``::

        parser, subparsers = make_parser()
        from tools.commands.edit import add_parser
        add_parser(subparsers)
        args = parser.parse_args(["edit", "automations"])
    """
    parser = ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    return parser, subparsers


def parse_command_args(command: str, add_parser: Any, argv: list[str]) -> Any:
    """Parse a command's arguments through its production parser factory."""
    parser, subparsers = make_parser()
    add_parser(subparsers)
    return parser.parse_args([command, *argv])


def make_command_args(
    command: str,
    add_parser: Any,
    argv: list[str] | None = None,
    **overrides: Any,
) -> Any:
    """Parse a command's arguments with optional argv and keyword overrides."""
    args = parse_command_args(command, add_parser, argv or [])
    for name, value in overrides.items():
        setattr(args, name, value)
    return args
