"""Tests for tools/generate_changelog.py - backup changelog generation."""

import io
import tarfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.helpers import make_backup_record, make_tar
from tools.backup_common import backup_path_for_changelog
from tools.generate_changelog import (
    changelog_path_for,
    extract_files,
    generate_changelog,
    generate_for_backup,
    should_include,
)


@pytest.fixture(autouse=True)
def clear_extract_files_cache():
    """Prevent archive extraction cache state from leaking between tests."""
    extract_files.cache_clear()
    yield
    extract_files.cache_clear()


@pytest.mark.parametrize(
    "path,expected",
    [
        ("config/automations.yaml", True),
        ("config/test.yml", True),
        ("scripts/debug.sh", True),
        ("tools/test.py", True),
        ("config/data.json", True),
        ("config/.storage/core.entity_registry", False),
        ("config/secrets.yaml", False),
        ("zigbee2mqtt/state.json", False),
        ("zigbee2mqtt/log/2026-01-01.log", False),
        ("config/home-assistant_v2.db", False),
        ("tools/__pycache__/test.pyc", False),
        ("tools/test.pyc", False),
        ("Makefile", True),
        ("image.png", False),
    ],
)
def test_should_include(path, expected):
    assert should_include(path) is expected


class TestExtractFiles:
    def test_extracts_yaml_files(self, tmp_path):
        tar_path = make_tar(
            tmp_path,
            {"config/automations.yaml": "- alias: Test\n"},
        )
        files = extract_files(tar_path)
        assert "config/automations.yaml" in files
        assert files["config/automations.yaml"] == "- alias: Test\n"

    def test_skips_storage_files(self, tmp_path):
        tar_path = make_tar(
            tmp_path,
            {
                "config/.storage/core.entity_registry": '{"data": {}}',
                "config/test.yaml": "key: value",
            },
        )
        files = extract_files(tar_path)
        assert "config/.storage/core.entity_registry" not in files
        assert "config/test.yaml" in files

    def test_handles_invalid_tar(self, tmp_path):
        bad_file = tmp_path / "bad.tar.gz"
        bad_file.write_text("not a tar file")
        with pytest.raises(OSError):
            extract_files(bad_file)

    def test_corrupt_tar_aborts_changelog_generation(self, tmp_path):
        bad_file = tmp_path / "bad.tar.gz"
        bad_file.write_text("not a tar file")
        bad_backup = make_backup_record(bad_file, bad_file.name, datetime(2026, 2, 2))
        good_file = make_tar(tmp_path, {"config/automations.yaml": "alias: test\n"})
        good_backup = make_backup_record(
            good_file, good_file.name, datetime(2026, 2, 1)
        )
        with pytest.raises(OSError):
            generate_changelog(bad_backup, good_backup)

    def test_skips_non_file_members(self, tmp_path):
        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            # Add directory
            info = tarfile.TarInfo(name="config/")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
            # Add file
            data = b"key: value\n"
            finfo = tarfile.TarInfo(name="config/test.yaml")
            finfo.size = len(data)
            tar.addfile(finfo, io.BytesIO(data))
        files = extract_files(tar_path)
        assert "config/test.yaml" in files
        assert "config/" not in files

    def test_skips_binary_content(self, tmp_path):
        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            data = b"\xff\xfe\x00\x01"
            info = tarfile.TarInfo(name="config/binary.yaml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        files = extract_files(tar_path)
        assert "config/binary.yaml" not in files

    def test_same_path_not_re_extracted(self, tmp_path):
        """extract_files caches results so the same archive is only opened once."""
        tar_path = make_tar(tmp_path, {"config/test.yaml": "content\n"})
        with patch(
            "tools.generate_changelog.tarfile.open", wraps=tarfile.open
        ) as mock_open:
            r1 = extract_files(tar_path)
            r2 = extract_files(tar_path)
        assert r1 == r2
        assert mock_open.call_count == 1

    def test_extract_files_mutation_isolation(self, tmp_path):
        tar_path = make_tar(tmp_path, {"config/test.yaml": "content\n"})
        r1 = extract_files(tar_path)
        r1.pop("config/test.yaml")
        r2 = extract_files(tar_path)
        assert "config/test.yaml" in r2


class TestUnifiedDiff:
    """W3.7: _unified_diff extracts canonical a/b-filename unified diffs."""

    def test_returns_diff_with_a_b_filenames(self):
        from tools.generate_changelog import _unified_diff

        result = _unified_diff("config/test.yaml", ["old"], ["new"])
        assert isinstance(result, list)
        assert any(
            line.startswith("---") and "a/config/test.yaml" in line for line in result
        )
        assert any(
            line.startswith("+++") and "b/config/test.yaml" in line for line in result
        )

    def test_empty_when_inputs_identical(self):
        from tools.generate_changelog import _unified_diff

        assert _unified_diff("x.yaml", ["same"], ["same"]) == []


class TestCountDiff:
    """W3.7: _count_diff counts + and - lines ignoring file headers."""

    def test_counts_additions_and_removals(self):
        from tools.generate_changelog import _count_diff

        diff = ["+++ b/x", "--- a/x", "+added", "-removed", "+kept"]
        assert _count_diff(diff) == (2, 1)

    def test_ignores_filename_headers(self):
        from tools.generate_changelog import _count_diff

        diff = ["+++ b/x", "--- a/x"]
        assert _count_diff(diff) == (0, 0)


class TestGenerateChangelog:
    @pytest.mark.parametrize(
        ("previous", "current", "expected_summary"),
        [
            (None, "new\n", "  A config/test.yaml (+1)"),
            ("old\n", None, "  D config/test.yaml (-1)"),
            ("old\n", "new\nmore\n", "  M config/test.yaml (+2, -1)"),
            ("same\n", "same\n", None),
        ],
    )
    def test_describe_change_normalizes_diff_inputs(
        self, previous, current, expected_summary
    ):
        from tools.generate_changelog import _describe_file_change

        summary, diff = _describe_file_change("config/test.yaml", previous, current)

        assert summary == expected_summary
        if expected_summary is None:
            assert diff is None
        else:
            assert "a/config/test.yaml" in diff
            assert "b/config/test.yaml" in diff

    def test_file_change_collection_keeps_summaries_paired_with_diffs(self):
        from tools.generate_changelog import _collect_file_changes

        changes = _collect_file_changes(
            {"config/b.yaml": "new\n", "config/a.yaml": "added\n"},
            {"config/b.yaml": "old\n"},
        )

        assert [summary for summary, _diff in changes] == [
            "  A config/a.yaml (+1)",
            "  M config/b.yaml (+1, -1)",
        ]
        for summary, diff in changes:
            name = summary.split()[1]
            assert f"a/{name}" in diff
            assert f"b/{name}" in diff

    def test_initial_backup(self, tmp_path):
        tar_path = make_tar(tmp_path, {"config/test.yaml": "key: value\n"})
        backup = make_backup_record(
            tar_path,
            "ha_config_20260201_120000.tar.gz",
            datetime(2026, 2, 1, 12, 0, 0),
        )
        content = generate_changelog(backup, None)
        assert "Initial backup" in content
        assert "config/test.yaml" in content

    def test_no_changes(self, tmp_path):
        sub1 = tmp_path / "d1"
        sub1.mkdir()
        sub2 = tmp_path / "d2"
        sub2.mkdir()
        tar_path_1 = make_tar(sub1, {"config/test.yaml": "same"})
        tar_path_2 = make_tar(sub2, {"config/test.yaml": "same"})

        prev = make_backup_record(tar_path_1, "prev.tar.gz", datetime(2026, 2, 1, 12))
        curr = make_backup_record(tar_path_2, "curr.tar.gz", datetime(2026, 2, 2, 12))
        content = generate_changelog(curr, prev)
        assert "No changes detected" in content

    def test_modified_file(self, tmp_path):
        tar_path_1 = make_tar(
            tmp_path, {"config/test.yaml": "old content\n"}, name="b1.tar.gz"
        )
        tar_path_2 = make_tar(
            tmp_path,
            {"config/test.yaml": "new content\nmore lines\n"},
            name="b2.tar.gz",
        )

        prev = make_backup_record(tar_path_1, "prev.tar.gz", datetime(2026, 2, 1, 12))
        curr = make_backup_record(tar_path_2, "curr.tar.gz", datetime(2026, 2, 2, 12))
        content = generate_changelog(curr, prev)
        assert "M config/test.yaml" in content

    def test_added_file(self, tmp_path):
        tar_path_1 = make_tar(tmp_path, {}, name="b1.tar.gz")
        tar_path_2 = make_tar(
            tmp_path, {"config/new.yaml": "new file\n"}, name="b2.tar.gz"
        )

        prev = make_backup_record(tar_path_1, "prev.tar.gz", datetime(2026, 2, 1, 12))
        curr = make_backup_record(tar_path_2, "curr.tar.gz", datetime(2026, 2, 2, 12))
        content = generate_changelog(curr, prev)
        assert "A config/new.yaml" in content

    def test_deleted_file(self, tmp_path):
        tar_path_1 = make_tar(
            tmp_path, {"config/old.yaml": "old file\n"}, name="b1.tar.gz"
        )
        tar_path_2 = make_tar(tmp_path, {}, name="b2.tar.gz")

        prev = make_backup_record(tar_path_1, "prev.tar.gz", datetime(2026, 2, 1, 12))
        curr = make_backup_record(tar_path_2, "curr.tar.gz", datetime(2026, 2, 2, 12))
        content = generate_changelog(curr, prev)
        assert "D config/old.yaml" in content


class TestChangelogPathFor:
    def test_generates_changelog_path(self):
        backup = {
            "path": Path("/tmp/archive/ha_config_20260201_120000.tar.gz"),
            "filename": "ha_config_20260201_120000.tar.gz",
        }
        with patch("tools.backup_common.BACKUP_DIR", Path("/tmp/backups")):
            result = changelog_path_for(backup)
            assert result == Path("/tmp/archive/ha_config_20260201_120000.changelog")

    def test_generates_paired_backup_path(self):
        changelog = Path("/tmp/backups/ha_config_20260201_120000.changelog")
        with patch("tools.backup_common.BACKUP_DIR", Path("/tmp/backups")):
            result = backup_path_for_changelog(changelog)
            assert result == Path("/tmp/backups/ha_config_20260201_120000.tar.gz")

    def test_pairing_does_not_use_discovery_root(self, tmp_path):
        archive_dir = tmp_path / "archive"
        discovery_dir = tmp_path / "discovery"
        archive_dir.mkdir()
        discovery_dir.mkdir()
        archive = archive_dir / "ha_config_20260201_120000.tar.gz"
        backup = {"path": archive, "filename": archive.name}
        with patch("tools.backup_common.BACKUP_DIR", discovery_dir):
            assert changelog_path_for(backup).parent == archive_dir


class TestGenerateForBackup:
    def test_generates_for_single_backup(self, tmp_path):
        tar_path = make_tar(tmp_path, {"config/test.yaml": "key: value\n"})
        backup = make_backup_record(
            tar_path,
            "ha_config_20260201_120000.tar.gz",
            datetime(2026, 2, 1, 12, 0, 0),
        )
        backups_list = [backup]

        with patch("tools.backup_common.BACKUP_DIR", tmp_path):
            result = generate_for_backup(backup, backups_list)
            assert result.exists()
            content = result.read_text()
            assert "Initial backup" in content

    def test_generates_with_predecessor(self, tmp_path):
        tar1 = make_tar(tmp_path, {"config/test.yaml": "old\n"})
        # Need a second tar at different path
        sub = tmp_path / "sub"
        sub.mkdir()
        tar2 = make_tar(sub, {"config/test.yaml": "new\n"})

        prev = make_backup_record(
            tar1,
            "ha_config_20260201_120000.tar.gz",
            datetime(2026, 2, 1, 12, 0, 0),
        )
        curr = make_backup_record(
            tar2,
            "ha_config_20260202_120000.tar.gz",
            datetime(2026, 2, 2, 12, 0, 0),
        )
        backups_list = [prev, curr]

        with patch("tools.backup_common.BACKUP_DIR", tmp_path):
            result = generate_for_backup(curr, backups_list)
            assert result.exists()
            content = result.read_text()
            assert "Previous:" in content

    def test_predecessor_helper_returns_adjacent_oldest_first_backup(self):
        from tools.generate_changelog import _select_predecessor

        older = {"filename": "older"}
        newer = {"filename": "newer"}
        assert _select_predecessor(newer, [older, newer]) is older
        assert _select_predecessor(older, [older, newer]) is None

    def test_predecessor_helper_rejects_absent_backup(self):
        from tools.generate_changelog import _select_predecessor

        with pytest.raises(ValueError, match="not found in the backup list"):
            _select_predecessor({"filename": "missing"}, [{"filename": "other"}])

    def test_write_failure_is_reported(self, tmp_path, monkeypatch):
        from tools.generate_changelog import _write_changelog

        tar_path = make_tar(tmp_path, {"config/test.yaml": "key: value\n"})
        backup = make_backup_record(
            tar_path,
            "ha_config_20260201_120000.tar.gz",
            datetime(2026, 2, 1, 12, 0, 0),
        )
        monkeypatch.setattr(
            "tools.generate_changelog.atomic_write_text", lambda *_: False
        )
        with pytest.raises(OSError, match="could not write changelog"):
            _write_changelog(backup, None)


class TestMain:
    def test_no_backups(self, monkeypatch):
        from tools.generate_changelog import main

        monkeypatch.setattr("sys.argv", ["generate_changelog"])
        with patch("tools.generate_changelog.get_backups", return_value=[]):
            result = main()
            assert result == 1

    def test_generate_all(self, tmp_path, monkeypatch, capsys):
        from tools.generate_changelog import main

        tar1 = make_tar(tmp_path, {"config/test.yaml": "content1\n"})
        sub = tmp_path / "sub"
        sub.mkdir()
        tar2 = make_tar(sub, {"config/test.yaml": "content2\n"})

        backups = [
            make_backup_record(
                tar1,
                "ha_config_20260201_120000.tar.gz",
                datetime(2026, 2, 1, 12, 0, 0),
            ),
            make_backup_record(
                tar2,
                "ha_config_20260202_120000.tar.gz",
                datetime(2026, 2, 2, 12, 0, 0),
            ),
        ]

        monkeypatch.setattr("sys.argv", ["generate_changelog", "--generate-all"])
        with (
            patch("tools.generate_changelog.get_backups", return_value=backups),
            patch("tools.backup_common.BACKUP_DIR", tmp_path),
        ):
            result = main()
            assert result == 0
            captured = capsys.readouterr()
            assert "Generated 2" in captured.err

    def test_generate_all_uses_adjacent_predecessors(self, tmp_path, monkeypatch):
        from tools.generate_changelog import main

        backups = []
        for index in range(4):
            archive_dir = tmp_path / f"backup-{index}"
            archive_dir.mkdir()
            filename = f"ha_config_2026020{index + 1}_120000.tar.gz"
            archive = make_tar(
                archive_dir,
                {"config/test.yaml": f"content {index}\n"},
                name=filename,
            )
            backups.append(
                make_backup_record(archive, filename, datetime(2026, 2, index + 1, 12))
            )

        monkeypatch.setattr("sys.argv", ["generate_changelog", "--generate-all"])
        with (
            patch("tools.generate_changelog.get_backups", return_value=backups),
            patch(
                "tools.generate_changelog._select_predecessor",
                side_effect=AssertionError("bulk generation must use the index"),
            ),
        ):
            assert main() == 0

        for index, backup in enumerate(backups):
            changelog = backup["path"].with_suffix("").with_suffix(".changelog")
            content = changelog.read_text()
            expected_previous = "(none - initial backup)"
            if index:
                expected_previous = backups[index - 1]["filename"]
            assert f"Previous: {expected_previous}" in content

    def test_generate_all_skips_existing(self, tmp_path, monkeypatch, capsys):
        from tools.generate_changelog import main

        tar1 = make_tar(tmp_path, {"config/test.yaml": "content1\n"})
        backups = [
            make_backup_record(
                tar1,
                "ha_config_20260201_120000.tar.gz",
                datetime(2026, 2, 1, 12, 0, 0),
            ),
        ]
        # Pre-create changelog
        (tmp_path / "ha_config_20260201_120000.changelog").write_text("existing")

        monkeypatch.setattr("sys.argv", ["generate_changelog", "--generate-all"])
        with (
            patch("tools.generate_changelog.get_backups", return_value=backups),
            patch("tools.backup_common.BACKUP_DIR", tmp_path),
        ):
            result = main()
            assert result == 0
            captured = capsys.readouterr()
            assert "skipped 1" in captured.err

    def test_specific_backup(self, tmp_path, monkeypatch, capsys):
        from tools.generate_changelog import main

        tar1 = make_tar(tmp_path, {"config/test.yaml": "content\n"})
        backups = [
            make_backup_record(
                tar1,
                "ha_config_20260201_120000.tar.gz",
                datetime(2026, 2, 1, 12, 0, 0),
            ),
        ]

        monkeypatch.setattr(
            "sys.argv",
            ["generate_changelog", "ha_config_20260201_120000.tar.gz"],
        )
        with (
            patch("tools.generate_changelog.get_backups", return_value=backups),
            patch("tools.backup_common.BACKUP_DIR", tmp_path),
        ):
            result = main()
            assert result == 0
            captured = capsys.readouterr()
            assert "Changelog written" in captured.err

    def test_backup_not_found(self, tmp_path, monkeypatch, capsys):
        from tools.generate_changelog import main

        backups = [
            make_backup_record(
                tmp_path / "other.tar.gz",
                "other.tar.gz",
                datetime(2026, 2, 1, 12, 0, 0),
            ),
        ]

        monkeypatch.setattr(
            "sys.argv",
            ["generate_changelog", "nonexistent.tar.gz"],
        )
        with patch("tools.generate_changelog.get_backups", return_value=backups):
            result = main()
            assert result == 1
            captured = capsys.readouterr()
            assert "not found" in captured.err

    def test_generate_all_with_positional_arg_errors(self, monkeypatch):
        """L61: --generate-all + a positional must error, not silently ignore."""
        from tools.generate_changelog import main

        monkeypatch.setattr(
            "sys.argv",
            ["generate_changelog", "--generate-all", "some_backup.tar.gz"],
        )
        with (
            pytest.raises(SystemExit) as exc,
            patch("tools.generate_changelog.get_backups", return_value=[]),
        ):
            main()
        assert exc.value.code == 2

    def test_force_overwrites_existing_changelog(self, tmp_path, monkeypatch, capsys):
        """L61: --force overwrites an existing .changelog."""
        from tools.generate_changelog import main

        tar_path = make_tar(tmp_path, {"config/test.yaml": "content1\n"})
        backups = [
            make_backup_record(
                tar_path,
                "ha_config_20260201_120000.tar.gz",
                datetime(2026, 2, 1, 12, 0, 0),
            ),
        ]
        cl_path = tmp_path / "ha_config_20260201_120000.changelog"
        cl_path.write_text("old content")

        monkeypatch.setattr(
            "sys.argv",
            ["generate_changelog", "--generate-all", "--force"],
        )
        with (
            patch("tools.generate_changelog.get_backups", return_value=backups),
            patch("tools.backup_common.BACKUP_DIR", tmp_path),
        ):
            result = main()
            assert result == 0
            assert cl_path.read_text() != "old content"


class TestL59AtomicWrite:
    """L59: changelog write is atomic — no partial .changelog on failure."""

    def test_changelog_write_is_atomic_on_failure(self, tmp_path, monkeypatch):
        """L59: if write fails, no partial .changelog sticks (corruption-on-stick)."""
        from tools.generate_changelog import generate_for_backup

        tar_path = make_tar(tmp_path, {"config/test.yaml": "key: value\n"})
        backup = make_backup_record(
            tar_path,
            "ha_config_20260201_120000.tar.gz",
            datetime(2026, 2, 1, 12, 0, 0),
        )

        # Pre-create a changelog to verify it survives a failure
        cl_path = tmp_path / "ha_config_20260201_120000.changelog"
        cl_path.write_text("original content")

        import tools.common as tcommon

        orig_replace = tcommon.os.replace

        def fail_on_replace(*a, **kw):
            if a[0].suffix == ".tmp":
                raise OSError("mock failure")
            return orig_replace(*a, **kw)

        monkeypatch.setattr(tcommon.os, "replace", fail_on_replace)
        with (
            patch("tools.backup_common.BACKUP_DIR", tmp_path),
            pytest.raises(OSError, match="could not write changelog"),
        ):
            generate_for_backup(backup, [backup])
        # Original must survive intact
        assert cl_path.read_text() == "original content"
        # No .tmp file left behind
        assert not (tmp_path / "ha_config_20260201_120000.changelog.tmp").exists()


class TestL60ValueError:
    """L60: unknown backup raises ValueError, not silent 'Initial backup'."""

    def test_generate_for_unknown_backup_raises(self, tmp_path):
        """L60: passing an unknown backup must raise, not silently print 'Initial'."""
        from tools.generate_changelog import generate_for_backup

        tar_path = make_tar(tmp_path, {"config/test.yaml": "key: value\n"})
        backup = make_backup_record(
            tar_path,
            "ha_config_20260201_120000.tar.gz",
            datetime(2026, 2, 1, 12, 0, 0),
        )
        other_backup = make_backup_record(
            tar_path,
            "nonexistent.tar.gz",
            datetime(2026, 2, 1, 12, 0, 0),
        )
        with (
            patch("tools.backup_common.BACKUP_DIR", tmp_path),
            pytest.raises(ValueError, match="not found in the backup list"),
        ):
            generate_for_backup(other_backup, [backup])


class TestL62Format:
    """L62: Date header preserves tz offset, removesuffix anchors correctly."""

    def test_date_header_with_timezone_offset_parsed(self, tmp_path):
        """L62: a Date: header with %z offset must preserve tz on the datetime."""
        from datetime import timedelta, timezone

        from tools.generate_changelog import generate_changelog

        tar_path = make_tar(tmp_path, {"config/test.yaml": "key: value\n"})
        tz_aware = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))
        backup = make_backup_record(
            tar_path, "ha_config_20260201_120000.tar.gz", tz_aware
        )
        content = generate_changelog(backup, None)
        assert "+1100" in content or "+11:00" in content

    def test_removesuffix_handles_double_extension(self):
        """L62: removesuffix anchors to the suffix exactly."""
        from tools.generate_changelog import changelog_path_for

        backup = {
            "path": Path("/tmp/ha_config_20260201_120000.tar.gz"),
            "filename": "ha_config_20260201_120000.tar.gz",
        }
        result = changelog_path_for(backup)
        assert result.name == "ha_config_20260201_120000.changelog"


class TestL63Gaps:
    """L63: test coverage gaps for generate_changelog."""

    def test_write_encoding_round_trip_utf8(self, tmp_path):
        """L63: write→read of the changelog preserves UTF-8."""
        from tools.generate_changelog import generate_for_backup

        content = "key: value\nemoji: 🔥\n"
        tar_path = make_tar(tmp_path, {"config/test.yaml": content})
        backup = make_backup_record(
            tar_path,
            "ha_config_20260201_120000.tar.gz",
            datetime(2026, 2, 1, 12, 0, 0),
        )
        with patch("tools.backup_common.BACKUP_DIR", tmp_path):
            cl_path = generate_for_backup(backup, [backup])
        text = cl_path.read_text(encoding="utf-8")
        assert "config/test.yaml" in text
        assert "2 lines" in text

    def test_extract_files_returns_none_on_missing_member(self, tmp_path):
        """L63: extractfile() returning None must not crash extract_files."""
        from tools.generate_changelog import extract_files

        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            info = tarfile.TarInfo(name="some_file.yaml")
            info.size = 0
            # Simulate a member that exists in the tar index but has no data
            tar.addfile(info)

        result = extract_files(tar_path)
        assert isinstance(result, dict)

    def test_idempotency_of_generated_content(self, tmp_path):
        """L63: running generate twice on the same backup produces identical content."""
        from tools.generate_changelog import generate_changelog

        tar_path = make_tar(tmp_path, {"config/test.yaml": "content\n"})
        backup = make_backup_record(
            tar_path,
            "ha_config_20260201_120000.tar.gz",
            datetime(2026, 2, 1, 12, 0, 0),
        )
        prev = make_backup_record(
            tar_path,
            "ha_config_20260101_120000.tar.gz",
            datetime(2026, 1, 1, 12, 0, 0),
        )
        c1 = generate_changelog(backup, prev)
        c2 = generate_changelog(backup, prev)
        assert c1 == c2

    def test_predecessor_ordering(self, tmp_path):
        """L63: predecessor selection must use timestamp ordering."""
        from tools.generate_changelog import generate_for_backup

        sub = tmp_path / "sub"
        sub.mkdir()
        tar1 = make_tar(sub, {"config/test.yaml": "old\n"})
        tar2 = make_tar(tmp_path, {"config/test.yaml": "new\n"})

        older = make_backup_record(
            tar1,
            "ha_config_20260101_120000.tar.gz",
            datetime(2026, 1, 1, 12, 0, 0),
        )
        newer = make_backup_record(
            tar2,
            "ha_config_20260201_120000.tar.gz",
            datetime(2026, 2, 1, 12, 0, 0),
        )
        backups = [older, newer]

        with patch("tools.backup_common.BACKUP_DIR", tmp_path):
            generate_for_backup(older, backups)
            cl_path = sub / "ha_config_20260101_120000.changelog"
            content = cl_path.read_text()
            assert "Initial backup" in content

            generate_for_backup(newer, backups)
            cl_path2 = tmp_path / "ha_config_20260201_120000.changelog"
            content2 = cl_path2.read_text()
            assert "Previous:" in content2
            assert "20260101" in content2

    def test_make_tar_uses_custom_name(self, tmp_path):
        """The shared tar helper accepts a custom archive name."""
        path = make_tar(tmp_path, {"a.yaml": "x"}, name="custom.tar.gz")
        assert path.name == "custom.tar.gz"
