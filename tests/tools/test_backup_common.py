"""Tests for tools/backup_common.py — shared backup primitives."""

import importlib
import tarfile

import pytest

from tests.helpers import make_tar
from tools.backup_common import (
    backup_path_for_changelog,
    changelog_path_for,
    get_backups,
    iter_managed_artifacts,
    iter_tarball_file_members,
    parse_backup_filename,
)


class TestIterTarballFileMembers:
    def test_yields_regular_files_only(self, tmp_path):
        tar_path = make_tar(tmp_path, {"config/test.yaml": "content\n"})
        result = list(iter_tarball_file_members(tar_path))
        assert len(result) == 1
        name, _file = result[0]
        assert name == "config/test.yaml"

    def test_skips_directories(self, tmp_path):
        tar_path = make_tar(
            tmp_path,
            {"config/": None, "config/test.yaml": "content\n"},
        )
        result = list(iter_tarball_file_members(tar_path))
        assert len(result) == 1
        assert result[0][0] == "config/test.yaml"

    def test_normalizes_dot_slash_prefix(self, tmp_path):
        tar_path = make_tar(tmp_path, {"./config/test.yaml": "content\n"})
        result = list(iter_tarball_file_members(tar_path))
        assert result[0][0] == "config/test.yaml"

    def test_skips_symlinks(self, tmp_path):
        tar_path = make_tar(
            tmp_path,
            {"config/real.yaml": "content\n"},
            symlinks={"link.yaml": "config/test.yaml"},
        )
        result = list(iter_tarball_file_members(tar_path))
        assert len(result) == 1
        assert result[0][0] == "config/real.yaml"

    def test_content_readable_from_yielded_file(self, tmp_path):
        tar_path = make_tar(tmp_path, {"config/test.yaml": "hello world\n"})
        for _name, extracted in iter_tarball_file_members(tar_path):
            content = extracted.read().decode("utf-8")
            assert content == "hello world\n"

    def test_empty_tarball_yields_nothing(self, tmp_path):
        tar_path = make_tar(tmp_path, {}, name="empty.tar.gz")
        result = list(iter_tarball_file_members(tar_path))
        assert result == []

    def test_propagates_tar_error(self, tmp_path):
        bad = tmp_path / "bad.tar.gz"
        bad.write_text("not a tar file")
        with pytest.raises((tarfile.TarError, OSError)):
            list(iter_tarball_file_members(bad))


class TestManagedBackups:
    def test_artifact_kind_is_closed_literal(self):
        from typing import Literal, get_args, get_origin

        import tools.backup_common as backup_common

        assert get_origin(backup_common.ArtifactKind) is Literal
        assert get_args(backup_common.ArtifactKind) == ("backup", "changelog")

    def test_backup_dir_honors_environment_override(self, monkeypatch, tmp_path):
        import tools.backup_common as backup_common

        override = tmp_path / "custom-backups"
        monkeypatch.setenv("BACKUP_DIR", str(override))
        importlib.reload(backup_common)
        assert override == backup_common.BACKUP_DIR

        monkeypatch.delenv("BACKUP_DIR")
        importlib.reload(backup_common)

    def test_get_backups_only_returns_canonical_regular_files(
        self, tmp_path, monkeypatch
    ):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        valid = backup_dir / "ha_config_20260721_120000.tar.gz"
        valid.write_bytes(b"data")
        (backup_dir / "ha_config_20260721_120001.tar.gz").mkdir()
        (backup_dir / "ha_config_20260721_120002.tar.gz.bak").write_bytes(b"data")
        symlink = backup_dir / "ha_config_20260721_120003.tar.gz"
        symlink.symlink_to(valid)
        monkeypatch.setattr("tools.backup_common.BACKUP_DIR", backup_dir)

        assert [item["path"] for item in get_backups()] == [valid]

    def test_iter_managed_artifacts_filters_canonical_regular_files(
        self, tmp_path, monkeypatch
    ):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        valid = backup_dir / "ha_config_20260721_120000.changelog"
        valid.write_text("data")
        (backup_dir / "notes.changelog").write_text("skip")
        (backup_dir / "ha_config_20260721_120001.changelog").mkdir()
        symlink = backup_dir / "ha_config_20260721_120002.changelog"
        symlink.symlink_to(valid)
        monkeypatch.setattr("tools.backup_common.BACKUP_DIR", backup_dir)

        assert list(iter_managed_artifacts("changelog")) == [valid]

    def test_pairing_is_local_to_archive_parent(self, tmp_path):
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        archive = archive_dir / "ha_config_20260721_120000.tar.gz"
        changelog = archive_dir / "ha_config_20260721_120000.changelog"
        record = {
            "path": archive,
            "filename": archive.name,
            "timestamp": parse_backup_filename(archive.name),
        }

        assert changelog_path_for(record) == changelog
        assert backup_path_for_changelog(changelog) == archive


class TestFilterBackups:
    def test_filter_by_days(self):
        from datetime import datetime, timedelta
        from pathlib import Path

        from tests.helpers import make_backup_record
        from tools.backup_common import filter_backups

        now = datetime(2026, 8, 18, 20, 0, 0).astimezone()
        b1 = make_backup_record(
            Path("ha_config_20260818_120000.tar.gz"),
            "ha_config_20260818_120000.tar.gz",
            now - timedelta(hours=8),
        )
        b2 = make_backup_record(
            Path("ha_config_20260816_120000.tar.gz"),
            "ha_config_20260816_120000.tar.gz",
            now - timedelta(days=2),
        )
        b3 = make_backup_record(
            Path("ha_config_20260801_120000.tar.gz"),
            "ha_config_20260801_120000.tar.gz",
            now - timedelta(days=17),
        )

        backups = [b1, b2, b3]
        result = filter_backups(backups, days=7, now=now)
        assert result == [b1, b2]

    def test_filter_by_limit(self):
        from datetime import datetime
        from pathlib import Path

        from tests.helpers import make_backup_record
        from tools.backup_common import filter_backups

        b1 = make_backup_record(
            Path("b1.tar.gz"), "b1.tar.gz", datetime(2026, 8, 18).astimezone()
        )
        b2 = make_backup_record(
            Path("b2.tar.gz"), "b2.tar.gz", datetime(2026, 8, 17).astimezone()
        )
        b3 = make_backup_record(
            Path("b3.tar.gz"), "b3.tar.gz", datetime(2026, 8, 16).astimezone()
        )

        backups = [b1, b2, b3]
        assert filter_backups(backups, limit=2) == [b1, b2]

    def test_filter_combines_days_and_limit(self):
        from datetime import datetime, timedelta
        from pathlib import Path

        from tests.helpers import make_backup_record
        from tools.backup_common import filter_backups

        now = datetime(2026, 8, 18, 20, 0, 0).astimezone()
        b1 = make_backup_record(
            Path("b1.tar.gz"), "b1.tar.gz", now - timedelta(hours=1)
        )
        b2 = make_backup_record(
            Path("b2.tar.gz"), "b2.tar.gz", now - timedelta(hours=2)
        )
        b3 = make_backup_record(
            Path("b3.tar.gz"), "b3.tar.gz", now - timedelta(hours=3)
        )

        backups = [b1, b2, b3]
        assert filter_backups(backups, days=1, limit=2, now=now) == [b1, b2]
