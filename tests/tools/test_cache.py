"""Tests for tools/cache.py — validator result caching."""

import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.cache import (
    CACHE_SCHEMA_VERSION,
    _blob_hash,
    _compute_hash_status,
    cache_path,
    compute_hash,
    load_blob,
    load_cache,
    save_blob,
    save_cache,
)


@pytest.fixture
def cache_record():
    def factory(**changes):
        record = {
            "schema": CACHE_SCHEMA_VERSION,
            "validator": "Foo",
            "hash": "abc",
            "passed": True,
            "timestamp": "now",
            "duration": 0.5,
            "stderr": "",
        }
        record.update(changes)
        return record

    return factory


class TestComputeHash:
    def test_returns_sha256_hex_string(self, tmp_path):
        (tmp_path / "a.yaml").write_text("hello")
        result = compute_hash(tmp_path, ["*.yaml"])
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex digest
        assert all(c in "0123456789abcdef" for c in result)

    def test_different_content_different_hash(self, tmp_path):
        (tmp_path / "a.yaml").write_text("hello")
        hash1 = compute_hash(tmp_path, ["*.yaml"])
        (tmp_path / "a.yaml").write_text("world")
        hash2 = compute_hash(tmp_path, ["*.yaml"])
        assert hash1 != hash2

    def test_same_content_same_hash(self, tmp_path):
        (tmp_path / "a.yaml").write_text("hello")
        hash1 = compute_hash(tmp_path, ["*.yaml"])
        hash2 = compute_hash(tmp_path, ["*.yaml"])
        assert hash1 == hash2

    def test_empty_patterns_produces_consistent_hash(self, tmp_path):
        assert compute_hash(tmp_path, []) == compute_hash(tmp_path, [])
        (tmp_path / "a.yaml").write_text("x")
        assert compute_hash(tmp_path, []) != compute_hash(tmp_path, ["*.yaml"])

    def test_no_matching_files_produces_consistent_hash(self, tmp_path):
        empty = compute_hash(tmp_path, ["*.yaml"])
        (tmp_path / "a.yaml").write_text("x")
        assert compute_hash(tmp_path, ["*.yaml"]) != empty

    def test_multiple_files_produces_consistent_order(self, tmp_path):
        (tmp_path / "z.yaml").write_text("z")
        (tmp_path / "a.yaml").write_text("a")
        hash1 = compute_hash(tmp_path, ["*.yaml"])
        hash2 = compute_hash(tmp_path, ["*.yaml"])
        assert hash1 == hash2

    def test_overlapping_patterns_deduplicated(self, tmp_path):
        """When two patterns match the same file, the file is only hashed once."""
        (tmp_path / "a.yaml").write_text("hello")
        assert compute_hash(tmp_path, ["*.yaml", "a.yaml"]) == compute_hash(
            tmp_path, ["a.yaml"]
        )

    def test_recursive_glob(self, tmp_path):
        """** patterns should match nested files."""
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.yaml").write_text("top")
        (tmp_path / "sub" / "b.yaml").write_text("nested")
        result = compute_hash(tmp_path, ["**/*.yaml"])
        assert isinstance(result, str)
        assert len(result) == 64

    def test_subdirectory_not_matched_by_top_level(self, tmp_path):
        """Top-level *.yaml does NOT match files in subdirectories."""
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.yaml").write_text("top")
        (tmp_path / "sub" / "b.yaml").write_text("nested")
        hash_top_only = compute_hash(tmp_path, ["*.yaml"])
        # Now add the nested file
        hash_all = compute_hash(tmp_path, ["**/*.yaml"])
        assert hash_top_only != hash_all


class TestL78OrderIndependent:
    """L78: hash must be order-independent (inner sorted() was removed)."""

    def test_compute_hash_is_order_independent(self, tmp_path):
        """L78: hash must be the same regardless of file creation/glob order."""
        (tmp_path / "b.yaml").write_text("content b")
        (tmp_path / "a.yaml").write_text("content a")
        h1 = compute_hash(tmp_path, ["*.yaml"])
        assert isinstance(h1, str)
        assert len(h1) == 64

        # Recreate files in different order — hash must be identical.
        for p in tmp_path.glob("*.yaml"):
            p.unlink()
        (tmp_path / "a.yaml").write_text("content a")
        (tmp_path / "b.yaml").write_text("content b")
        h2 = compute_hash(tmp_path, ["*.yaml"])
        assert h1 == h2, "hash must be independent of file creation order"


class TestL79UnreadableFile:
    """L79: a transient OSError on one file must skip it, not disable caching."""

    def test_compute_hash_skips_unreadable_file(self, tmp_path, monkeypatch, capsys):
        """L79: a transient OSError on one file must skip it, not disable caching."""
        (tmp_path / "a.yaml").write_text("readable")
        (tmp_path / "b.yaml").write_text("also readable")

        orig_read_bytes = Path.read_bytes
        n_calls = 0

        def _mock_read_bytes(self):
            nonlocal n_calls
            n_calls += 1
            if self.name == "b.yaml":
                raise OSError("simulated read failure")
            return orig_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", _mock_read_bytes)
        result = compute_hash(tmp_path, ["*.yaml"])
        assert isinstance(result, str)
        assert len(result) == 64
        _, err = capsys.readouterr()
        assert "WARN" in err

    def test_hash_status_marks_unreadable_match_incomplete(self, tmp_path, monkeypatch):
        (tmp_path / "broken.yaml").write_text("content")
        original = Path.read_bytes

        def fail_for_broken(path):
            if path.name == "broken.yaml":
                raise OSError("nope")
            return original(path)

        monkeypatch.setattr(Path, "read_bytes", fail_for_broken)
        _digest, complete = _compute_hash_status(tmp_path, ["*.yaml"])
        assert complete is False


class TestLoadCache:
    def test_valid_cache_returns_dict(self, tmp_path, cache_record):
        cache_dir = tmp_path / ".cache" / "validators"
        cache_dir.mkdir(parents=True)
        (cache_dir / "Foo.json").write_text(json.dumps(cache_record()))
        result = load_cache(tmp_path, "Foo")
        assert result is not None
        assert result["hash"] == "abc"
        assert result["passed"] is True

    def test_missing_cache_returns_none(self, tmp_path):
        result = load_cache(tmp_path, "Nonexistent")
        assert result is None

    def test_missing_cache_does_not_create_directory(self, tmp_path):
        """A read-only check should not create .cache/validators/ on disk."""
        result = load_cache(tmp_path, "Nonexistent")
        assert result is None
        cache_dir = tmp_path / ".cache" / "validators"
        assert not cache_dir.exists()

    def test_cache_path_is_read_only(self, tmp_path):
        path = cache_path(tmp_path, "Foo")
        assert path == tmp_path / ".cache" / "validators" / "Foo.json"
        assert not path.parent.exists()

    def test_invalid_json_returns_none(self, tmp_path):
        cache_dir = tmp_path / ".cache" / "validators"
        cache_dir.mkdir(parents=True)
        (cache_dir / "Foo.json").write_text("not json")
        result = load_cache(tmp_path, "Foo")
        assert result is None

    def test_undecodable_json_returns_none(self, tmp_path):
        cache_dir = tmp_path / ".cache" / "validators"
        cache_dir.mkdir(parents=True)
        (cache_dir / "Foo.json").write_bytes(b"\xff\xfe")
        assert load_cache(tmp_path, "Foo") is None

    def test_load_uses_shared_cache_path(self, tmp_path, monkeypatch, cache_record):
        import tools.cache as cache

        redirected = tmp_path / "redirected.json"
        redirected.write_text(json.dumps(cache_record()))
        monkeypatch.setattr(cache, "cache_path", lambda _config_dir, _name: redirected)

        result = cache.load_cache(tmp_path, "Foo")
        assert result is not None
        assert result["hash"] == "abc"

    def test_missing_hash_key_returns_none(self, tmp_path):
        cache_dir = tmp_path / ".cache" / "validators"
        cache_dir.mkdir(parents=True)
        (cache_dir / "Foo.json").write_text(json.dumps({"passed": True}))
        result = load_cache(tmp_path, "Foo")
        assert result is None

    def test_missing_passed_key_returns_none(self, tmp_path):
        cache_dir = tmp_path / ".cache" / "validators"
        cache_dir.mkdir(parents=True)
        (cache_dir / "Foo.json").write_text(json.dumps({"hash": "abc"}))
        result = load_cache(tmp_path, "Foo")
        assert result is None

    @pytest.mark.parametrize(
        "change",
        [
            {"schema": True},
            {"hash": 123},
            {"passed": 1},
            {"duration": -1},
            {"duration": math.inf},
            {"duration": True},
            {"duration": None},
            {"stderr": []},
            {"validator": []},
            {"timestamp": 123},
        ],
    )
    def test_malformed_record_returns_none(self, tmp_path, change, cache_record):
        record = cache_record(**change)
        path = tmp_path / ".cache" / "validators" / "Foo.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(record, allow_nan=True))
        assert load_cache(tmp_path, "Foo") is None


class TestCacheSchemaVersion:
    """M1: CACHE_SCHEMA_VERSION must be present in saved caches and checked on load."""

    def test_saved_cache_includes_schema_version(self, tmp_path):
        from tools.cache import CACHE_SCHEMA_VERSION, save_cache

        save_cache(tmp_path, "TestValidator", "Test", "hash123", True, 0.5)
        data = json.loads(
            (tmp_path / ".cache" / "validators" / "TestValidator.json").read_text()
        )
        assert data["schema"] == CACHE_SCHEMA_VERSION

    def test_load_cache_returns_none_on_schema_mismatch(self, tmp_path):
        from tools.cache import CACHE_SCHEMA_VERSION, load_cache

        save_cache(tmp_path, "TestValidator", "Test", "hash123", True, 0.5)
        p = tmp_path / ".cache" / "validators" / "TestValidator.json"
        data = json.loads(p.read_text())
        data["schema"] = CACHE_SCHEMA_VERSION + 999
        p.write_text(json.dumps(data))
        assert load_cache(tmp_path, "TestValidator") is None

    def test_load_cache_returns_none_on_missing_schema(self, tmp_path):
        from tools.cache import load_cache

        p = tmp_path / ".cache" / "validators" / "TestValidator.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"hash": "x", "passed": True}))
        assert load_cache(tmp_path, "TestValidator") is None


class TestSaveCache:
    def test_saves_json_with_required_keys(self, tmp_path):
        save_cache(tmp_path, "Foo", "Test Foo", "hash123", True, 0.42)
        cache_file = tmp_path / ".cache" / "validators" / "Foo.json"
        assert cache_file.exists()
        data = json.loads(cache_file.read_text())
        assert data["validator"] == "Test Foo"
        assert data["hash"] == "hash123"
        assert data["passed"] is True
        assert data["duration"] == 0.42
        assert "timestamp" in data

    def test_saves_failure_result(self, tmp_path):
        """Unit test only: validates serialization; _run_one never caches failures."""
        save_cache(tmp_path, "Bar", "Test Bar", "hash456", False, 0.1)
        cache_file = tmp_path / ".cache" / "validators" / "Bar.json"
        data = json.loads(cache_file.read_text())
        assert data["passed"] is False

    def test_creates_cache_directory(self, tmp_path):
        save_cache(tmp_path, "Baz", "Test", "h", True, 0.0)
        assert (tmp_path / ".cache" / "validators").is_dir()

    def test_overwrites_existing_cache(self, tmp_path):
        save_cache(tmp_path, "Foo", "Old", "old", True, 0.0)
        save_cache(tmp_path, "Foo", "New", "new", True, 0.0)
        cache_file = tmp_path / ".cache" / "validators" / "Foo.json"
        data = json.loads(cache_file.read_text())
        assert data["validator"] == "New"
        assert data["hash"] == "new"


class TestM2AtomicSaveCache:
    """M2: save_cache atomic (temp-file + os.replace) and retry on read errors."""

    def test_save_cache_is_atomic_no_tmp_left(self, tmp_path):
        """A successful save leaves no .tmp file beside the cache."""
        from tools.cache import save_cache

        save_cache(tmp_path, "TestValidator", "Test", "h", True, 0.5)
        cache_dir = tmp_path / ".cache" / "validators"
        files = [p.name for p in cache_dir.iterdir()]
        assert files == ["TestValidator.json"]
        assert not (cache_dir / "TestValidator.json.tmp").exists()

    def test_save_cache_atomic_on_dump_failure(self, tmp_path, monkeypatch):
        """If json.dump raises mid-write, the existing cache is not truncated."""

        from tools.cache import save_cache

        save_cache(tmp_path, "TestValidator", "Test", "orig", True, 0.5)
        cache_file = tmp_path / ".cache" / "validators" / "TestValidator.json"
        original_contents = cache_file.read_text()

        # Force json.dump to fail on the next save.
        import tools.cache as cache_mod

        def boom(*a, **kw):
            raise RuntimeError("disk full")

        monkeypatch.setattr(cache_mod.json, "dumps", boom)
        with pytest.raises(RuntimeError):
            save_cache(tmp_path, "TestValidator", "Test", "new", True, 0.5)

        # Original cache must be intact (atomic write).
        assert cache_file.read_text() == original_contents
        # And no .tmp left behind.
        assert not (cache_file.with_suffix(".json.tmp")).exists()

    def test_load_cache_succeeds_after_transient_json_error(
        self, tmp_path, monkeypatch, cache_record
    ):
        from tools import cache as cache_mod
        from tools.cache import load_cache

        cache_file = tmp_path / ".cache" / "validators" / "TestValidator.json"
        cache_file.parent.mkdir(parents=True)
        valid = cache_record(hash="h", duration=0.1)
        cache_file.write_text(json.dumps(valid))
        real_load = cache_mod.json.load
        calls = 0

        def load(_file):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise cache_mod.json.JSONDecodeError("transient", "", 0)
            return real_load(_file)

        monkeypatch.setattr(cache_mod.json, "load", load)
        monkeypatch.setattr(cache_mod.time, "sleep", lambda _seconds: None)
        assert load_cache(tmp_path, "TestValidator") == valid
        assert calls == 2


class TestSaveBlobErrors:
    @pytest.mark.parametrize(
        ("save_function", "name", "error"),
        [
            (save_blob, "testkey", "permission denied"),
            (save_cache, "Foo", "disk full"),
        ],
        ids=["blob", "validator"],
    )
    def test_save_warns_on_oserror(self, tmp_path, capsys, save_function, name, error):
        """Cache writers print WARN to stderr when file writes fail."""
        with patch("builtins.open", side_effect=OSError(error)):
            if save_function is save_blob:
                save_function(tmp_path, name, {"output": "data"})
            else:
                save_function(tmp_path, name, "Test", "hash", True, 0.5)
        _, err = capsys.readouterr()
        assert "WARN" in err
        assert name in err


class TestBlobCache:
    """Tests for blob cache (save_blob / load_blob / _blob_hash)."""

    def test_blob_hash_empty(self):
        h = _blob_hash([])
        assert isinstance(h, str)
        assert len(h) == 64  # SHA256 hex

    def test_blob_hash_deterministic(self):
        h1 = _blob_hash(["hello", b"world"])
        h2 = _blob_hash(["hello", b"world"])
        assert h1 == h2

    def test_blob_hash_different_inputs_different(self):
        h1 = _blob_hash(["abc"])
        h2 = _blob_hash(["ab", "c"])
        assert h1 != h2

    def test_blob_hash_mixed_str_bytes(self):
        h = _blob_hash(["domain", b"\x00", "light"])
        assert isinstance(h, str)
        assert len(h) == 64

    def test_save_and_load_roundtrip(self, tmp_path):
        data = {"output": '{"a": 1}'}
        save_blob(tmp_path, "testkey", data)
        loaded = load_blob(tmp_path, "testkey")
        assert loaded == data

    def test_load_missing_returns_none(self, tmp_path):
        assert load_blob(tmp_path, "nonexistent") is None

    def test_load_corrupt_returns_none(self, tmp_path):
        cache_dir = tmp_path / ".cache" / "entities"
        cache_dir.mkdir(parents=True)
        (cache_dir / "bad.json").write_text("{invalid")
        assert load_blob(tmp_path, "bad") is None

    def test_load_undecodable_returns_none(self, tmp_path):
        cache_dir = tmp_path / ".cache" / "entities"
        cache_dir.mkdir(parents=True)
        (cache_dir / "bad.json").write_bytes(b"\xff\xfe")
        assert load_blob(tmp_path, "bad") is None

    def test_save_creates_directory(self, tmp_path):
        data = {"output": "test"}
        save_blob(tmp_path, "x", data)
        assert (tmp_path / ".cache" / "entities" / "x.json").is_file()

    def test_save_and_load_overwrite(self, tmp_path):
        save_blob(tmp_path, "k", {"output": "v1"})
        save_blob(tmp_path, "k", {"output": "v2"})
        loaded = load_blob(tmp_path, "k")
        assert loaded == {"output": "v2"}

    def test_blob_hash_delimiter_prevents_collision(self):
        """Without a delimiter, adjacent keys could merge. Verify they don't."""
        h1 = _blob_hash(["ab", "c"])
        h2 = _blob_hash(["a", "bc"])
        assert h1 != h2
