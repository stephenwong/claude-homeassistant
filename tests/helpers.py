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


def make_tar(tmp_path, files, name="test.tar.gz"):
    """Create a gzipped tar archive containing UTF-8 text files."""
    tar_path = tmp_path / name
    with tarfile.open(tar_path, "w:gz") as tar:
        for filename, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=filename)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return tar_path


def make_backup_record(path: Path, filename: str, timestamp: datetime) -> BackupRecord:
    """Build complete metadata for one backup archive."""
    return {"path": path, "filename": filename, "timestamp": timestamp}


def write_yaml(config_dir: Path, data: Any, filename: str = "automations.yaml") -> Path:
    """Write a YAML fixture and return its path."""
    path = config_dir / filename
    with path.open("w") as file:
        yaml.dump(data, file)
    return path


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
    result: Any = None, *, side_effect: Exception | None = None
) -> MagicMock:
    """Create an HA client mock for a JSON endpoint."""
    from tools.ha.client import HAClient

    client = MagicMock(spec=HAClient)
    client.close = MagicMock()
    if side_effect is None:
        client.get_json.return_value = result
    else:
        client.get_json.side_effect = side_effect
    return client


def assert_diagnostic(validator: Any, severity: str, text: str) -> None:
    """Assert that a validator emitted a diagnostic containing ``text``."""
    diagnostics = getattr(validator, severity)
    assert any(text.lower() in diagnostic.lower() for diagnostic in diagnostics), (
        f"No {severity} diagnostic contained {text!r}: {diagnostics!r}"
    )


def assert_no_diagnostic(
    validator: Any, severity: str, text: str | None = None
) -> None:
    """Assert that a validator emitted no diagnostics, optionally matching text."""
    diagnostics = getattr(validator, severity)
    if text is None:
        assert not diagnostics
    else:
        assert not any(text.lower() in diagnostic.lower() for diagnostic in diagnostics)


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
