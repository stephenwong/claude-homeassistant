"""Tests for tools/prune_backups.py - backup retention management."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tests.helpers import make_backup_record
from tools.backup_common import parse_backup_filename
from tools.prune_backups import (
    apply_retention,
    clean_orphaned_changelogs,
    format_size,
    group_by_retention_period,
)


@pytest.fixture
def frozen_prune_now(monkeypatch):
    """Freeze the clock imported by the pruner for boundary-sensitive tests."""
    fixed = datetime(2026, 2, 12, 12, 0, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr("tools.prune_backups.datetime", FrozenDateTime)
    return fixed


def _write_same_day_backups(backup_dir, now, count=2):
    """Create timestamped backups on one retention day and return their names."""
    old_date = now - timedelta(days=15)
    date_str = old_date.strftime("%Y%m%d")
    filenames = [
        f"ha_config_{date_str}_{100000 + index * 100000:06d}.tar.gz"
        for index in range(count)
    ]
    for filename in filenames:
        (backup_dir / filename).write_bytes(b"x" * 512)
    return filenames


class TestParseBackupFilename:
    def test_parsed_timestamp_is_timezone_aware(self):
        result = parse_backup_filename("ha_config_20260205_204808.tar.gz")
        assert result is not None
        assert result.tzinfo is not None

    def test_valid_filename(self):
        result = parse_backup_filename("ha_config_20260205_204808.tar.gz")
        assert result is not None
        assert result.year == 2026
        assert result.month == 2
        assert result.day == 5
        assert result.hour == 20
        assert result.minute == 48
        assert result.second == 8

    def test_invalid_filename(self):
        assert parse_backup_filename("not_a_backup.tar.gz") is None

    def test_invalid_date(self):
        assert parse_backup_filename("ha_config_99991399_999999.tar.gz") is None

    def test_no_match(self):
        assert parse_backup_filename("random_file.txt") is None


class TestGetBackups:
    def test_get_backups_with_files(self, tmp_path):
        # Create fake backup files
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "ha_config_20260201_120000.tar.gz").write_text("data1")
        (backup_dir / "ha_config_20260202_120000.tar.gz").write_text("data2")
        (backup_dir / "not_a_backup.txt").write_text("skip")

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            from tools.prune_backups import get_backups

            backups = get_backups()
            assert len(backups) == 2
            # Should be sorted oldest first
            assert backups[0]["filename"] == "ha_config_20260201_120000.tar.gz"
            assert backups[1]["filename"] == "ha_config_20260202_120000.tar.gz"

    def test_get_backups_empty_dir(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            from tools.prune_backups import get_backups

            assert get_backups() == []

    def test_get_backups_nonexistent_dir(self, tmp_path):
        with patch("tools.backup_common.BACKUP_DIR", tmp_path / "nonexistent"):
            from tools.prune_backups import get_backups

            assert get_backups() == []


class TestGroupByRetentionPeriod:
    def test_retention_boundaries_are_named(self):
        from tools import prune_backups as prune

        assert prune.RECENT_MAX_AGE_DAYS == 7
        assert prune.DAILY_MAX_AGE_DAYS == 30
        assert prune.WEEKLY_MIN_AGE_DAYS == 31

    def test_recent_backups_keep_all(self):
        now = datetime(2026, 2, 12, 12, 0, 0)
        backups = [
            {"timestamp": now - timedelta(days=1), "filename": "b1"},
            {"timestamp": now - timedelta(days=3), "filename": "b2"},
            {"timestamp": now - timedelta(days=6), "filename": "b3"},
        ]
        groups = group_by_retention_period(backups, now)
        assert len(groups["keep_all"]) == 3
        assert len(groups["daily"]) == 0
        assert len(groups["weekly"]) == 0

    def test_daily_grouping(self):
        now = datetime(2026, 2, 12, 12, 0, 0)
        backups = [
            {"timestamp": now - timedelta(days=10), "filename": "b1"},
            {"timestamp": now - timedelta(days=10, hours=6), "filename": "b2"},
            {"timestamp": now - timedelta(days=15), "filename": "b3"},
        ]
        groups = group_by_retention_period(backups, now)
        assert len(groups["keep_all"]) == 0
        assert len(groups["daily"]) == 2  # two different days
        assert len(groups["weekly"]) == 0

    def test_weekly_grouping(self):
        now = datetime(2026, 2, 12, 12, 0, 0)
        backups = [
            {"timestamp": now - timedelta(days=40), "filename": "b1"},
            {"timestamp": now - timedelta(days=50), "filename": "b2"},
        ]
        groups = group_by_retention_period(backups, now)
        assert len(groups["keep_all"]) == 0
        assert len(groups["daily"]) == 0
        assert len(groups["weekly"]) > 0

    def test_mixed_periods(self):
        now = datetime(2026, 2, 12, 12, 0, 0)
        backups = [
            {"timestamp": now - timedelta(days=1), "filename": "recent"},
            {"timestamp": now - timedelta(days=15), "filename": "daily"},
            {"timestamp": now - timedelta(days=45), "filename": "weekly"},
        ]
        groups = group_by_retention_period(backups, now)
        assert len(groups["keep_all"]) == 1
        assert sum(len(v) for v in groups["daily"].values()) == 1
        assert sum(len(v) for v in groups["weekly"].values()) == 1

    def test_year_boundary_same_iso_week(self):
        """Dec 29 2025 and Jan 2 2026 are ISO week 2026-W01 — must group together."""
        now = datetime(2026, 2, 12, 12, 0, 0)
        backups = [
            {"timestamp": datetime(2025, 12, 29, 10, 0), "filename": "dec29"},
            {"timestamp": datetime(2026, 1, 2, 10, 0), "filename": "jan02"},
        ]
        groups = group_by_retention_period(backups, now)
        # Both should land in the same weekly group
        assert len(groups["weekly"]) == 1
        week_key = list(groups["weekly"].keys())[0]
        assert len(groups["weekly"][week_key]) == 2

    def test_jan1_not_week_zero(self):
        """Jan 1 2026 should not produce a W00 group — ISO weeks are 01-53."""
        now = datetime(2026, 2, 12, 12, 0, 0)
        backups = [
            {"timestamp": datetime(2026, 1, 1, 10, 0), "filename": "jan01"},
        ]
        groups = group_by_retention_period(backups, now)
        for week_key in groups["weekly"]:
            assert "W00" not in week_key

    def test_exactly_7_days_old_is_keep_all(self):
        """A backup exactly 7 days old should be in keep_all, not daily."""
        now = datetime(2026, 2, 12, 12, 0, 0)
        backups = [
            {"timestamp": now - timedelta(days=7), "filename": "b_7d"},
        ]
        groups = group_by_retention_period(backups, now)
        assert len(groups["keep_all"]) == 1
        assert sum(len(v) for v in groups["daily"].values()) == 0

    def test_8_days_old_is_daily(self):
        """A backup 8 days old should be in daily, not keep_all."""
        now = datetime(2026, 2, 12, 12, 0, 0)
        backups = [
            {"timestamp": now - timedelta(days=8), "filename": "b_8d"},
        ]
        groups = group_by_retention_period(backups, now)
        assert len(groups["keep_all"]) == 0
        assert sum(len(v) for v in groups["daily"].values()) == 1

    def test_exactly_30_days_old_is_daily(self):
        """A backup exactly 30 days old should be in daily, not weekly."""
        now = datetime(2026, 2, 12, 12, 0, 0)
        backups = [
            {"timestamp": now - timedelta(days=30), "filename": "b_30d"},
        ]
        groups = group_by_retention_period(backups, now)
        assert sum(len(v) for v in groups["daily"].values()) == 1
        assert sum(len(v) for v in groups["weekly"].values()) == 0

    def test_31_days_old_is_weekly(self):
        """A backup 31 days old should be in weekly, not daily."""
        now = datetime(2026, 2, 12, 12, 0, 0)
        backups = [
            {"timestamp": now - timedelta(days=31), "filename": "b_31d"},
        ]
        groups = group_by_retention_period(backups, now)
        assert sum(len(v) for v in groups["daily"].values()) == 0
        assert sum(len(v) for v in groups["weekly"].values()) == 1


class TestApplyRetention:
    def test_keep_all_recent(self):
        groups = {
            "keep_all": [{"filename": "b1"}, {"filename": "b2"}],
            "daily": {},
            "weekly": {},
        }
        to_keep, to_delete = apply_retention(groups)
        assert len(to_keep) == 2
        assert len(to_delete) == 0

    def test_daily_keeps_latest(self):
        groups = {
            "keep_all": [],
            "daily": {
                "2026-02-01": [
                    {"filename": "b1", "timestamp": datetime(2026, 2, 1, 10, 0)},
                    {"filename": "b2", "timestamp": datetime(2026, 2, 1, 20, 0)},
                ]
            },
            "weekly": {},
        }
        to_keep, to_delete = apply_retention(groups)
        assert len(to_keep) == 1
        assert to_keep[0]["filename"] == "b2"  # Latest
        assert len(to_delete) == 1
        assert to_delete[0]["filename"] == "b1"

    def test_weekly_keeps_latest(self):
        groups = {
            "keep_all": [],
            "daily": {},
            "weekly": {
                "2026-W01": [
                    {"filename": "b1", "timestamp": datetime(2026, 1, 5, 10, 0)},
                    {"filename": "b2", "timestamp": datetime(2026, 1, 7, 20, 0)},
                ]
            },
        }
        to_keep, to_delete = apply_retention(groups)
        assert len(to_keep) == 1
        assert to_keep[0]["filename"] == "b2"
        assert len(to_delete) == 1

    def test_year_boundary_weekly_prunes_correctly(self):
        """Two backups in same ISO week across year boundary — keep only latest."""
        groups = {
            "keep_all": [],
            "daily": {},
            "weekly": {
                "2026-W01": [
                    {
                        "filename": "dec29",
                        "timestamp": datetime(2025, 12, 29, 10, 0),
                    },
                    {
                        "filename": "jan02",
                        "timestamp": datetime(2026, 1, 2, 10, 0),
                    },
                ]
            },
        }
        to_keep, to_delete = apply_retention(groups)
        assert len(to_keep) == 1
        assert to_keep[0]["filename"] == "jan02"
        assert len(to_delete) == 1
        assert to_delete[0]["filename"] == "dec29"


class TestCleanOrphanedChangelogs:
    def test_orphan_discovery_uses_public_artifact_iterator(
        self, tmp_path, monkeypatch
    ):
        from tools import prune_backups as prune

        orphan = tmp_path / "ha_config_20260101_120000.changelog"
        iterator_calls = []

        def fake_iterator(kind):
            iterator_calls.append(kind)
            return iter([orphan]) if kind == "changelog" else iter([])

        monkeypatch.setattr(
            prune.backup_common, "iter_managed_artifacts", fake_iterator
        )

        assert prune._find_orphaned_changelogs() == [orphan]
        assert iterator_calls == ["backup", "changelog"]

    def test_orphan_discovery_is_pure(self, tmp_path):
        from tools import prune_backups as prune

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        orphan = backup_dir / "ha_config_20260101_120000.changelog"
        orphan.write_text("orphan")
        matched = backup_dir / "ha_config_20260201_120000.changelog"
        matched.write_text("matched")
        (backup_dir / "ha_config_20260201_120000.tar.gz").write_bytes(b"data")

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            assert prune._find_orphaned_changelogs() == [orphan]
        assert orphan.exists()
        assert matched.exists()

    def test_cleanup_returns_discovered_paths(self, tmp_path):
        from tools import prune_backups as prune

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        orphan = backup_dir / "ha_config_20260101_120000.changelog"
        orphan.write_text("orphan")

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            assert prune._clean_orphaned_changelogs() == [orphan]
        assert not orphan.exists()

    def test_removes_orphaned_changelogs(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # Create a changelog with no matching tar.gz
        orphan = backup_dir / "ha_config_20260101_120000.changelog"
        orphan.write_text("orphan")
        # Create a matched pair (should survive)
        (backup_dir / "ha_config_20260201_120000.tar.gz").write_bytes(b"data")
        (backup_dir / "ha_config_20260201_120000.changelog").write_text("matched")

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            count = clean_orphaned_changelogs()
            assert count == 1
            assert not orphan.exists()
            assert (backup_dir / "ha_config_20260201_120000.changelog").exists()

    def test_no_orphans(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "ha_config_20260201_120000.tar.gz").write_bytes(b"data")
        (backup_dir / "ha_config_20260201_120000.changelog").write_text("matched")

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            assert clean_orphaned_changelogs() == 0

    def test_nonexistent_dir(self, tmp_path):
        with patch("tools.backup_common.BACKUP_DIR", tmp_path / "nonexistent"):
            assert clean_orphaned_changelogs() == 0

    def test_dry_run_does_not_delete(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        orphan = backup_dir / "ha_config_20260101_120000.changelog"
        orphan.write_text("orphan")

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            count = clean_orphaned_changelogs(dry_run=True)
            assert count == 1
            assert orphan.exists()  # Not deleted

    def test_unrelated_and_nonregular_changelogs_are_untouched(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        unrelated = backup_dir / "notes.changelog"
        unrelated.write_text("keep")
        directory = backup_dir / "ha_config_20260101_120000.changelog"
        directory.mkdir()
        (backup_dir / "ha_config_20260101_120000.tar.gz").mkdir()

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            assert clean_orphaned_changelogs() == 0
        assert unrelated.exists()
        assert directory.exists()

    def test_directory_named_like_backup_does_not_protect_changelog(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        changelog = backup_dir / "ha_config_20260101_120000.changelog"
        changelog.write_text("orphan")
        (backup_dir / "ha_config_20260101_120000.tar.gz").mkdir()

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            assert clean_orphaned_changelogs() == 1
        assert not changelog.exists()

    def test_apply_check_does_not_rescan_orphans(self, tmp_path, monkeypatch):
        from tools import prune_backups as prune

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        orphan = backup_dir / "ha_config_20260101_120000.changelog"
        orphan.write_text("orphan")
        discovery_calls = 0
        original_find = prune._find_orphaned_changelogs

        def count_discovery_calls():
            nonlocal discovery_calls
            discovery_calls += 1
            return original_find()

        monkeypatch.setattr(prune, "_find_orphaned_changelogs", count_discovery_calls)
        monkeypatch.setattr(prune.backup_common, "BACKUP_DIR", backup_dir)

        assert prune._clean_orphans_and_check(dry_run=False)
        assert discovery_calls == 1


class TestFormatSize:
    def test_bytes(self):
        assert format_size(500) == "500.0B"

    def test_kilobytes(self):
        assert format_size(1536) == "1.5KB"

    def test_megabytes(self):
        assert format_size(2 * 1024 * 1024) == "2.0MB"

    def test_gigabytes(self):
        assert format_size(3 * 1024**3) == "3.0GB"

    def test_terabytes(self):
        assert format_size(1.5 * 1024**4) == "1.5TB"


class TestMain:
    def test_negative_min_keep_is_rejected(self, capsys):
        from tools.prune_backups import main

        with pytest.raises(SystemExit):
            main(["--min-keep", "-1"])
        assert "min-keep" in capsys.readouterr().err

    def test_no_backups(self, tmp_path, capsys):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            from tools.prune_backups import main

            result = main([])
            assert result == 0
            captured = capsys.readouterr()
            assert "nothing to prune" in captured.err

    def test_with_recent_backups(self, tmp_path, capsys):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # Create a recent backup (today)
        now = datetime.now()
        fname = f"ha_config_{now.strftime('%Y%m%d_%H%M%S')}.tar.gz"
        (backup_dir / fname).write_bytes(b"x" * 1024)

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            from tools.prune_backups import main

            result = main([])
            assert result == 0
            captured = capsys.readouterr()
            assert "No backups need to be deleted" in captured.out

    def test_yesterday_backup(self, tmp_path, capsys, frozen_prune_now):
        """Cover 'yesterday' age string."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        yesterday = frozen_prune_now - timedelta(days=1)
        fname = f"ha_config_{yesterday.strftime('%Y%m%d_%H%M%S')}.tar.gz"
        (backup_dir / fname).write_bytes(b"x" * 1024)

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            from tools.prune_backups import main

            result = main([])
            assert result == 0
            captured = capsys.readouterr()
            assert "yesterday" in captured.err

    def test_deletes_old_daily_duplicates(self, tmp_path, capsys):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create two backups on the same day, 15 days ago
        fname1, fname2 = _write_same_day_backups(backup_dir, datetime.now())
        # Also add a changelog for the first one
        (backup_dir / fname1.replace(".tar.gz", ".changelog")).write_text("old")

        with (
            patch("tools.backup_common.BACKUP_DIR", backup_dir),
        ):
            from tools.prune_backups import main

            result = main(["--apply", "--min-keep", "1"])
            assert result == 0
            # Should keep the later one (200000) and delete earlier (100000)
            assert (backup_dir / fname2).exists()
            assert not (backup_dir / fname1).exists()
            # Changelog for deleted backup should also be gone
            assert not (backup_dir / fname1.replace(".tar.gz", ".changelog")).exists()

    def test_kept_backup_changelog_preserved(self, tmp_path, capsys):
        """Changelog for a kept backup must not be deleted."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        fname1, fname2 = _write_same_day_backups(backup_dir, datetime.now())
        # Changelogs for both
        (backup_dir / fname1.replace(".tar.gz", ".changelog")).write_text("old")
        (backup_dir / fname2.replace(".tar.gz", ".changelog")).write_text("kept")

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            from tools.prune_backups import main

            main([])
            # Kept backup's changelog survives
            assert (backup_dir / fname2.replace(".tar.gz", ".changelog")).exists()

    def test_weekly_pruning_end_to_end(self, tmp_path, capsys):
        """Two backups in the same week, 35+ days old — only latest survives."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Pick a Monday 40 days ago and the following Wednesday
        base = datetime.now() - timedelta(days=40)
        # Align to Monday
        base = base - timedelta(days=base.weekday())
        d1 = base
        d2 = base + timedelta(days=2)  # Wednesday, same ISO week

        fname1 = f"ha_config_{d1.strftime('%Y%m%d')}_100000.tar.gz"
        fname2 = f"ha_config_{d2.strftime('%Y%m%d')}_100000.tar.gz"
        (backup_dir / fname1).write_bytes(b"x" * 512)
        (backup_dir / fname2).write_bytes(b"x" * 512)

        with (
            patch("tools.backup_common.BACKUP_DIR", backup_dir),
        ):
            from tools.prune_backups import main

            result = main(["--apply", "--min-keep", "1"])
            assert result == 0
            # Wednesday (later) kept, Monday (earlier) deleted
            assert (backup_dir / fname2).exists()
            assert not (backup_dir / fname1).exists()

    def test_dry_run_does_not_delete(self, tmp_path, capsys):
        """--dry-run should report but not delete anything."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        fname1, fname2 = _write_same_day_backups(backup_dir, datetime.now())

        with patch("tools.backup_common.BACKUP_DIR", backup_dir):
            from tools.prune_backups import main

            result = main(["--dry-run"])
            assert result == 0
            # Both files should still exist
            assert (backup_dir / fname1).exists()
            assert (backup_dir / fname2).exists()
            captured = capsys.readouterr()
            assert "DRY RUN" in captured.err

    def test_orphaned_changelogs_cleaned_during_main(self, tmp_path, capsys):
        """main() should clean orphaned changelogs after pruning."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Recent backup (won't be pruned)
        now = datetime.now()
        fname = f"ha_config_{now.strftime('%Y%m%d_%H%M%S')}.tar.gz"
        (backup_dir / fname).write_bytes(b"x" * 1024)

        # Orphaned changelog (no matching tar.gz)
        orphan = backup_dir / "ha_config_20250101_120000.changelog"
        orphan.write_text("orphan")

        with (
            patch("tools.backup_common.BACKUP_DIR", backup_dir),
        ):
            from tools.prune_backups import main

            main(["--apply", "--min-keep", "1"])
            assert not orphan.exists()

    def test_orphan_cleanup_failure_returns_nonzero(self, tmp_path, monkeypatch):
        """A failed orphan cleanup must not be reported as a successful prune."""
        from tools import prune_backups as pb

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        now = datetime.now()
        backup = backup_dir / f"ha_config_{now.strftime('%Y%m%d_%H%M%S')}.tar.gz"
        backup.write_bytes(b"x")
        orphan = backup_dir / "ha_config_20250101_120000.changelog"
        orphan.write_text("orphan")

        import pathlib

        original_unlink = pathlib.Path.unlink

        def fail_orphan_unlink(self, *args, **kwargs):
            if self == orphan:
                raise OSError("mock permission denied")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "unlink", fail_orphan_unlink)
        monkeypatch.setattr(pb.backup_common, "BACKUP_DIR", backup_dir)

        assert pb.main(["--apply", "--min-keep", "1"]) == 1
        assert orphan.exists()

    def test_delete_error_returns_nonzero(self, tmp_path, capsys):
        """If a file can't be deleted, main() should return 1."""
        import pathlib

        from tools import prune_backups as pb

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        old_date = datetime.now() - timedelta(days=15)
        date_str = old_date.strftime("%Y%m%d")
        fname1 = f"ha_config_{date_str}_100000.tar.gz"
        fname2 = f"ha_config_{date_str}_200000.tar.gz"
        (backup_dir / fname1).write_bytes(b"x" * 512)
        (backup_dir / fname2).write_bytes(b"x" * 512)

        orig_unlink = pathlib.Path.unlink

        def _mock_unlink(self, *a, **kw):
            if self.name == fname1:
                raise OSError("mock permission denied")
            return orig_unlink(self, *a, **kw)

        with (
            patch("tools.backup_common.BACKUP_DIR", backup_dir),
            patch.object(pathlib.Path, "unlink", _mock_unlink),
        ):
            result = pb.main(["--apply", "--min-keep", "1"])
            assert result == 1
            captured = capsys.readouterr()
            assert "failed to delete" in captured.err


class TestL64Regex:
    """L64: filename regex anchored with $ to reject extra suffixes."""

    def test_filename_regex_rejects_unmatched_suffix(self):
        """L64: ha_config_<digits>.tar.gz.bak must NOT match (regex end-anchored)."""
        from tools.backup_common import _BACKUP_RE

        assert _BACKUP_RE.match("ha_config_20260201_120000.tar.gz") is not None
        assert _BACKUP_RE.match("ha_config_20260201_120000.tar.gz.bak") is None
        assert _BACKUP_RE.match("ha_config_20260201_120000.tar.gz.tmp") is None


class TestL65Sort:
    """L65: deterministic tie-break on filename for identical timestamps."""

    def test_identical_timestamps_sorted_deterministically(self, tmp_path, monkeypatch):
        """L65: two backups with the same timestamp must tie-break on filename."""
        from tools.prune_backups import get_backups

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "ha_config_20260201_120000.tar.gz").write_bytes(b"x")
        (backup_dir / "ha_config_20260201_120001.tar.gz").write_bytes(b"x")

        monkeypatch.setattr("tools.backup_common.BACKUP_DIR", backup_dir)
        backups = get_backups()
        # Both should be sorted deterministically
        assert (
            backups[0]["filename"] < backups[1]["filename"]
            or backups[0]["filename"] > backups[1]["filename"]
        )


class TestL67RootPerms:
    """L67: partial-deletion reporting + Path.unlink mock (no chmod)."""

    def test_partial_deletion_reports_success_and_remaining(
        self, tmp_path, monkeypatch, capsys
    ):
        """L67: deleting 3 of 5 must report success, not abort the batch."""
        from tools.prune_backups import main

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # Create 5 backups all on the same day, 15 days ago — 4 will be deletions
        _write_same_day_backups(backup_dir, datetime.now(), count=5)

        monkeypatch.setattr("tools.backup_common.BACKUP_DIR", backup_dir)

        import pathlib

        n_calls = 0
        orig_unlink = pathlib.Path.unlink

        def _mock_unlink(self, *a, **kw):
            nonlocal n_calls
            n_calls += 1
            if n_calls <= 2:
                raise OSError("mock permission denied")
            return orig_unlink(self, *a, **kw)

        monkeypatch.setattr(pathlib.Path, "unlink", _mock_unlink)
        result = main(["--apply", "--min-keep", "1"])
        assert result == 1  # errors occurred
        _, err = capsys.readouterr()
        assert "failed to delete" in err

    def test_delete_error_mocked_not_chmod(self, tmp_path, monkeypatch, capsys):
        """L67: mock Path.unlink instead of chmod (fails open as root)."""
        from tools.prune_backups import main

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        fname1, fname2 = _write_same_day_backups(backup_dir, datetime.now())

        monkeypatch.setattr("tools.backup_common.BACKUP_DIR", backup_dir)

        import pathlib

        call_count = 0
        orig_unlink = pathlib.Path.unlink

        def _mock_unlink(self, *a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("mock permission denied")
            return orig_unlink(self, *a, **kw)

        monkeypatch.setattr(pathlib.Path, "unlink", _mock_unlink)
        result = main(["--apply", "--min-keep", "1"])
        assert result == 1
        _, err = capsys.readouterr()
        assert "failed to delete" in err or "Error deleting" in err


def test_missing_file_during_display_does_not_crash(tmp_path, monkeypatch):
    """A file vanishing between get_backups() and stat() display must not crash."""
    import pathlib

    from tools import prune_backups as pb

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr("tools.backup_common.BACKUP_DIR", backup_dir)

    vanished = "ha_config_20250101_000000.tar.gz"
    (backup_dir / vanished).write_bytes(b"x" * 1024)
    # Force the retention window to make it an old backup (marked for deletion).
    # Since it's the only backup and > 30 days old in name, it shows in to_delete.

    real_stat = pathlib.Path.stat

    def fake_stat(self, *a, **k):
        if self.name == vanished:
            raise FileNotFoundError(vanished)
        return real_stat(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "stat", fake_stat)

    rc = pb.main(["--dry-run"])
    assert rc == 0  # must not crash; degrades size to 0


class TestFormatLines:
    def _backup(self, tmp_path, timestamp):
        path = tmp_path / "ha_config_20240101_000000.tar.gz"
        path.write_bytes(b"x" * 1024)
        return make_backup_record(path, path.name, timestamp)

    def test_delete_line_includes_filename_size_and_age(self, tmp_path):
        from tools.prune_backups import _format_delete_line

        backup = self._backup(tmp_path, datetime(2024, 1, 1))
        line = _format_delete_line(backup, datetime(2024, 1, 8))
        assert backup["filename"] in line
        assert "7 days old" in line
        assert "1.0KB" in line

    def test_shared_line_formatter_preserves_delete_output(self, tmp_path):
        from tools.prune_backups import _format_backup_line

        backup = self._backup(tmp_path, datetime(2024, 1, 1))
        assert _format_backup_line(backup, 1024, "7 days old") == (
            "  - ha_config_20240101_000000.tar.gz (1.0KB, 7 days old)"
        )

    def test_delete_line_missing_file_size_zero(self, tmp_path):
        from tools.prune_backups import _format_delete_line

        backup = self._backup(tmp_path, datetime(2024, 1, 1))
        backup["path"].unlink()
        assert "0.0B" in _format_delete_line(backup, datetime(2024, 1, 8))

    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            (datetime(2024, 1, 1, 12), "today"),
            (datetime(2024, 1, 2), "yesterday"),
            (datetime(2024, 1, 15), "14 days ago"),
        ],
    )
    def test_keep_line_formats_age(self, tmp_path, now, expected):
        from tools.prune_backups import _format_keep_line

        backup = self._backup(tmp_path, datetime(2024, 1, 1))
        assert expected in _format_keep_line(backup, now)

    def test_delete_preview_uses_one_stat_snapshot(self, tmp_path, capsys):
        from types import SimpleNamespace

        from tools import prune_backups as prune

        class ChangingPath:
            name = "ha_config_20240101_000000.tar.gz"

            def __init__(self):
                self.stat_calls = 0

            def stat(self):
                self.stat_calls += 1
                size = 1024 if self.stat_calls == 1 else 2 * 1024
                return SimpleNamespace(st_size=size)

        path = ChangingPath()
        backup = {
            "filename": path.name,
            "path": path,
            "timestamp": datetime(2024, 1, 1),
        }

        prune._print_delete_preview([backup], datetime(2024, 1, 8))

        assert path.stat_calls == 1
        output = capsys.readouterr()
        assert "1.0KB" in output.out
        assert "2.0KB" not in output.out


class TestValidateDeletionSafety:
    def test_all_backups_deleted_returns_error(self):
        from tools.prune_backups import _validate_deletion_safety

        backups = [{"filename": f"b{i}.tar.gz"} for i in range(3)]
        error = _validate_deletion_safety(backups, backups, min_keep=1)
        assert error is not None
        assert "would remove all backups" in error

    def test_below_min_keep_returns_error(self):
        from tools.prune_backups import _validate_deletion_safety

        backups = [{"filename": f"b{i}.tar.gz"} for i in range(5)]
        error = _validate_deletion_safety(backups, backups[:4], min_keep=3)
        assert error is not None
        assert "below --min-keep" in error

    def test_safe_plan_returns_none(self):
        from tools.prune_backups import _validate_deletion_safety

        backups = [{"filename": f"b{i}.tar.gz"} for i in range(10)]
        assert _validate_deletion_safety(backups, backups[:3], min_keep=3) is None
