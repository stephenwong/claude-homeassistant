"""Tests for the atomic YAML formatter used by ``make format-yaml``."""

import pytest

from tools import format_yaml


def test_formats_yaml_and_preserves_permissions(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("root:\n    value: 'quoted'\n")
    path.chmod(0o640)
    monkeypatch.setattr("sys.argv", ["format_yaml", str(path)])

    assert format_yaml.main() == 0
    assert "root:" in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o640


def test_empty_yaml_is_left_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "empty.yaml"
    path.write_text("# comment only\n")
    before = path.read_text()
    monkeypatch.setattr("sys.argv", ["format_yaml", str(path)])

    assert format_yaml.main() == 0
    assert path.read_text() == before


def test_dump_failure_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("root: value\n")
    before = path.read_text()

    class FailingYaml:
        preserve_quotes = False

        def load(self, _text):
            return {"root": "value"}

        def dump(self, _data, _stream):
            raise OSError("disk full")

    monkeypatch.setattr(format_yaml, "YAML", FailingYaml)
    monkeypatch.setattr("sys.argv", ["format_yaml", str(path)])
    with pytest.raises(OSError, match="disk full"):
        format_yaml.main()

    assert path.read_text() == before
    assert not any(p.name.startswith(".tmp") for p in tmp_path.iterdir())
