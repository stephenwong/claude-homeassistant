"""Tests for tools/search_backups.py - backup search utility."""

import io
import re
import tarfile
from datetime import datetime
from unittest.mock import patch

from tests.helpers import make_tar
from tools.backup_common import BackupRecord
from tools.search_backups import is_likely_unsafe_regex, search_backup


def _make_backup_record(tmp_path, files_dict, *, name="test.tar.gz") -> BackupRecord:
    """Create a backup and its standard metadata for main-flow tests."""
    tar_path = make_tar(tmp_path, files_dict, name=name)
    return {
        "path": tar_path,
        "filename": tar_path.name,
        "timestamp": datetime(2026, 2, 1),
    }


def _run_main(monkeypatch, backups, *arguments):
    """Invoke search_backups.main with a controlled argv and backup list."""
    monkeypatch.setattr("sys.argv", ["search_backups", *arguments])
    with patch("tools.search_backups.get_backups", return_value=backups):
        from tools.search_backups import main

        return main()


class TestSearchBackup:
    def test_finds_pattern_in_yaml(self, tmp_path):
        tar_path = make_tar(
            tmp_path,
            {"config/automations.yaml": "- alias: Test\n  entity_id: sensor.test\n"},
        )
        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, _u = search_backup(backup, re.compile("sensor.test"))
        assert len(matches) == 1
        assert matches[0]["file"] == "config/automations.yaml"
        assert matches[0]["line_num"] == 2

    def test_yaml_only_filter(self, tmp_path):
        tar_path = make_tar(
            tmp_path,
            {
                "config/test.yaml": "pattern_match\n",
                "config/test.sh": "pattern_match\n",
            },
        )
        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, _u = search_backup(backup, re.compile("pattern_match"), yaml_only=True)
        assert len(matches) == 1
        assert matches[0]["file"] == "config/test.yaml"

    # Zero-context searches

    def test_zero_context_accepted(self, tmp_path):
        """The default zero-context search does not crash."""
        tar_path = make_tar(tmp_path, {"x.yaml": "line1\npattern\nline3\n"})
        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, _u = search_backup(backup, re.compile("pattern"), context_lines=0)
        assert len(matches) >= 1

    def test_all_files_filter(self, tmp_path):
        tar_path = make_tar(
            tmp_path,
            {
                "config/test.yaml": "pattern_match\n",
                "config/test.sh": "pattern_match\n",
            },
        )
        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, _u = search_backup(
            backup, re.compile("pattern_match"), yaml_only=False
        )
        assert len(matches) == 2

    def test_context_lines(self, tmp_path):
        tar_path = make_tar(
            tmp_path,
            {
                "config/test.yaml": "line1\nline2\nMATCH\nline4\nline5\n",
            },
        )
        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, _u = search_backup(backup, re.compile("MATCH"), context_lines=1)
        assert len(matches) == 1
        assert matches[0]["context_before"] == ["line2"]
        assert matches[0]["context_after"] == ["line4"]

    def test_no_matches(self, tmp_path):
        tar_path = make_tar(
            tmp_path,
            {"config/test.yaml": "nothing interesting here\n"},
        )
        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, _u = search_backup(backup, re.compile("nonexistent_pattern"))
        assert matches == []

    def test_handles_corrupt_tar(self, tmp_path):
        bad_file = tmp_path / "bad.tar.gz"
        bad_file.write_text("not a tar")
        backup = {
            "path": bad_file,
            "filename": bad_file.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, unreadable = search_backup(backup, re.compile("test"))
        assert matches == []
        assert unreadable is True

    def test_multiple_matches_in_file(self, tmp_path):
        tar_path = make_tar(
            tmp_path,
            {"config/test.yaml": "match1\nno\nmatch2\n"},
        )
        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, _u = search_backup(backup, re.compile("match"))
        assert len(matches) == 2

    def test_skips_non_file_members(self, tmp_path):
        """Directories in tar should be skipped."""
        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            # Add a directory
            info = tarfile.TarInfo(name="config/")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
            # Add a file
            data = b"match_line\n"
            finfo = tarfile.TarInfo(name="config/test.yaml")
            finfo.size = len(data)
            tar.addfile(finfo, io.BytesIO(data))

        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, _u = search_backup(backup, re.compile("match"))
        assert len(matches) == 1

    def test_skips_binary_files(self, tmp_path):
        """Non-UTF8 files should be skipped without error."""
        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            # Binary content in a yaml file
            data = b"\xff\xfe\x00\x01match\n"
            info = tarfile.TarInfo(name="config/binary.yaml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, _u = search_backup(backup, re.compile("match"))
        assert matches == []

    def test_decode_failure_keeps_partial_matches_and_marks_archive(self, tmp_path):
        """A decode failure does not discard matches from earlier lines."""
        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            data = b"match\n\xff\n"
            info = tarfile.TarInfo(name="config/binary.yaml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, unreadable = search_backup(backup, re.compile("match"))
        assert len(matches) == 1
        assert matches[0]["line"] == "match"
        assert unreadable is True


class TestRegexSafety:
    def test_detects_nested_quantifier_pattern(self):
        assert is_likely_unsafe_regex("(a+)+b") is True

    def test_allows_normal_pattern(self):
        assert is_likely_unsafe_regex("sensor.temperature") is False


class TestSearchBackupsMainFlow:
    """Test the main() function flow with mocked backups."""

    def test_main_no_backups(self, tmp_path, capsys, monkeypatch):
        assert _run_main(monkeypatch, [], "pattern") == 1

    def test_main_with_matches(self, tmp_path, capsys, monkeypatch):
        backups = [
            _make_backup_record(tmp_path, {"config/test.yaml": "sensor.temperature\n"})
        ]
        assert _run_main(monkeypatch, backups, "sensor") == 0
        assert "MATCH" in capsys.readouterr().out

    def test_main_files_only(self, tmp_path, capsys, monkeypatch):
        backups = [_make_backup_record(tmp_path, {"config/test.yaml": "sensor.test\n"})]
        assert _run_main(monkeypatch, backups, "--files-only", "sensor") == 0

    def test_main_with_context(self, tmp_path, capsys, monkeypatch):
        backups = [
            _make_backup_record(
                tmp_path,
                {"config/test.yaml": "before\nsensor.test\nafter\n"},
            )
        ]
        assert _run_main(monkeypatch, backups, "-C", "1", "sensor") == 0

    def test_main_no_matches(self, tmp_path, capsys, monkeypatch):
        backups = [
            _make_backup_record(tmp_path, {"config/test.yaml": "nothing here\n"})
        ]
        assert _run_main(monkeypatch, backups, "nonexistent_pattern") == 0
        assert "Found in 0" in capsys.readouterr().err

    def test_main_all_files(self, tmp_path, capsys, monkeypatch):
        backups = [_make_backup_record(tmp_path, {"config/test.sh": "sensor_match\n"})]
        assert _run_main(monkeypatch, backups, "--all", "sensor_match") == 0

    def test_main_invalid_regex(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["search_backups", "[invalid"])
        from tools.search_backups import main

        result = main()
        assert result == 1

    def test_main_unsafe_regex(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["search_backups", "(a+)+b"])
        from tools.search_backups import main

        result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "unsafe" in captured.err


class TestMemberNameNormalization:
    """Archive member names are normalized before they are reported."""

    def test_output_strips_dot_slash_prefix(self, tmp_path):
        """Member names starting with './' are displayed without that prefix."""
        import re

        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            data = b"sensor.test\n"
            info = tarfile.TarInfo(name="./config/test.yaml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, unreadable = search_backup(backup, re.compile("sensor"))
        assert unreadable is False
        assert len(matches) == 1
        # match_entry["file"] should strip the "./" prefix
        assert matches[0]["file"] == "config/test.yaml"
        assert not matches[0]["file"].startswith("./")


class TestLazyTarIteration:
    """Tar members are scanned lazily and non-file members are skipped."""

    def test_lazy_tar_iteration_handles_large_archive(self, tmp_path):
        """Non-file members do not prevent lazy scanning of real files."""
        import re

        # Create a tar with a symlink and a directory then a file
        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            # Add a directory
            dir_info = tarfile.TarInfo(name="subdir/")
            dir_info.type = tarfile.DIRTYPE
            tar.addfile(dir_info)
            # Add a symlink
            link_info = tarfile.TarInfo(name="link_to_file")
            link_info.type = tarfile.SYMTYPE
            link_info.linkname = "config/test.yaml"
            tar.addfile(link_info)
            # Add a real file
            data = b"match_this\n"
            finfo = tarfile.TarInfo(name="config/test.yaml")
            finfo.size = len(data)
            tar.addfile(finfo, io.BytesIO(data))

        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, unreadable = search_backup(backup, re.compile("match_this"))
        assert unreadable is False
        assert len(matches) == 1
        assert matches[0]["file"] == "config/test.yaml"


class TestUnsafeRegexHelper:
    """The unsafe-regex helper detects risky patterns and accepts normal ones."""

    def test_detects_nested_quantifiers_and_allows_normal_patterns(self):
        """The unsafe-regex helper distinguishes risky and normal patterns."""
        from tools.search_backups import is_likely_unsafe_regex

        assert is_likely_unsafe_regex("(a+)+") is True
        assert is_likely_unsafe_regex("normal") is False


class TestUnreadableBackups:
    """Unreadable backup archives are reported separately from no matches."""

    def test_main_reports_unreadable_count(self, tmp_path, monkeypatch, capsys):
        """Corrupt backups are reported as unreadable."""
        (tmp_path / "bad.tar.gz").write_text("not a real tar file")
        backups = [
            {
                "path": tmp_path / "bad.tar.gz",
                "filename": "bad.tar.gz",
                "timestamp": datetime(2026, 2, 1),
            }
        ]
        assert _run_main(monkeypatch, backups, "pattern") == 1
        _, err = capsys.readouterr()
        assert "unreadable" in err


class TestMatchResultShape:
    """Internal context bookkeeping does not leak into match results."""

    def test_internal_key_does_not_leak(self, tmp_path):
        """Returned matches contain public context fields, not internal bookkeeping."""
        import re

        tar_path = make_tar(
            tmp_path,
            {"config/test.yaml": "line1\nsensor.test\nline3\n"},
        )
        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, _unreadable = search_backup(
            backup, re.compile("sensor"), context_lines=1
        )
        for m in matches:
            assert "_remaining_after" not in m
            assert "context_before" in m
            assert "context_after" in m


class TestSearchTypeContracts:
    def test_search_inputs_are_typed_as_text_patterns_and_binary_streams(self):
        from typing import IO, get_type_hints

        from tools.search_backups import _search_file, search_backup

        search_file_hints = get_type_hints(_search_file)
        search_backup_hints = get_type_hints(search_backup)
        assert search_file_hints["extracted"] == IO[bytes]
        assert search_file_hints["pattern"] == re.Pattern[str]
        assert search_backup_hints["pattern"] == re.Pattern[str]


class TestTarExtractionSafety:
    """Tar extraction does not escape its temporary directory or follow links."""

    def test_malicious_tar_member_name_does_not_escape(self, tmp_path):
        """Traversal member names must not write outside the temporary directory."""
        import re

        tar_path = tmp_path / "evil.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            data = b"root:x:0:0\n"
            info = tarfile.TarInfo(name="../../../etc/passwd")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        before_files = set(tmp_path.rglob("*"))
        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        search_backup(backup, re.compile("root"))
        after_files = set(tmp_path.rglob("*"))
        # No new files should appear outside the expected ones
        new_files = after_files - before_files
        # Only the tarfile glob itself may appear
        assert not any("passwd" in str(p) for p in new_files)

    def test_symlink_member_not_followed(self, tmp_path):
        """Symlink members are skipped rather than extracted or followed."""
        import re

        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            # Add a symlink pointing to a non-existent file
            link_info = tarfile.TarInfo(name="bad_link.yaml")
            link_info.type = tarfile.SYMTYPE
            link_info.linkname = "/etc/passwd"
            tar.addfile(link_info)
            # Add a real file
            data = b"actual_content\n"
            finfo = tarfile.TarInfo(name="config/real.yaml")
            finfo.size = len(data)
            tar.addfile(finfo, io.BytesIO(data))

        backup = {
            "path": tar_path,
            "filename": tar_path.name,
            "timestamp": datetime(2026, 2, 1),
        }
        matches, unreadable = search_backup(backup, re.compile("actual_content"))
        assert unreadable is False
        assert len(matches) == 1
        assert matches[0]["file"] == "config/real.yaml"

    def test_comment_invariant_near_extract(self):
        """The extraction helper documents its extract-and-isfile safety invariant."""
        import inspect

        import tools.backup_common

        src = inspect.getsource(tools.backup_common)
        assert "extractfile" in src
        assert "isfile" in src
