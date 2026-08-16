"""Tests for tools/common.py - shared utilities."""

import argparse
import os
from io import StringIO
from pathlib import Path

import pytest
import yaml

from tools import common as tools_common
from tools.common import (
    DEFAULT_HA_URL,
    HAYamlLoader,
    ValidatorBase,
    _is_tty,
    format_diagnostics,
    get_env_int,
    get_ha_config,
    load_env_file,
    resolve_summary,
    validate_ha_url,
)


@pytest.mark.parametrize(
    "tag,argument",
    [
        ("!include", "file.yaml"),
        ("!include_dir_named", "mydir"),
        ("!include_dir_merge_named", "mydir"),
        ("!include_dir_merge_list", "mydir"),
        ("!include_dir_list", "mydir"),
        ("!input", "sensor_name"),
        ("!secret", "api_key"),
    ],
)
def test_ha_tag_loader(tag, argument):
    result = yaml.load(f"key: {tag} {argument}", Loader=HAYamlLoader)
    assert result == {"key": f"{tag} {argument}"}


@pytest.mark.parametrize(
    "content,key,expected",
    [
        (
            'HA_TOKEN=tok\nHA_URL="http://localhost:8123"\n',
            "HA_TOKEN",
            "tok",
        ),
        ("# comment\nTEST_VAR=value\n", "TEST_VAR", "value"),
        ("\n\nTEST_EMPTY=works\n\n", "TEST_EMPTY", "works"),
    ],
)
def test_load_env_file_parses(content, key, expected, tmp_path, monkeypatch):
    """Parametrized: normal values, comments, and empty lines."""
    (tmp_path / ".env").write_text(content)
    fake = tmp_path / "tools" / "common.py"
    fake.parent.mkdir()
    fake.touch()
    monkeypatch.setattr(tools_common, "__file__", str(fake))
    monkeypatch.delenv(key, raising=False)
    load_env_file()
    assert os.environ.get(key) == expected


class TestGetEnvInt:
    def test_missing_env_uses_default(self, monkeypatch):
        monkeypatch.delenv("HA_REQUEST_TIMEOUT", raising=False)
        value, warning = get_env_int("HA_REQUEST_TIMEOUT", 10)
        assert value == 10
        assert warning is None

    def test_valid_integer_env(self, monkeypatch):
        monkeypatch.setenv("HA_REQUEST_TIMEOUT", "25")
        value, warning = get_env_int("HA_REQUEST_TIMEOUT", 10)
        assert value == 25
        assert warning is None

    def test_invalid_integer_env_warns_and_falls_back(self, monkeypatch):
        monkeypatch.setenv("HA_REQUEST_TIMEOUT", "abc")
        value, warning = get_env_int("HA_REQUEST_TIMEOUT", 10)
        assert value == 10
        assert warning is not None
        assert "must be an integer" in warning

    def test_below_minimum_warns_and_falls_back(self, monkeypatch):
        monkeypatch.setenv("HA_REQUEST_TIMEOUT", "0")
        value, warning = get_env_int("HA_REQUEST_TIMEOUT", 10)
        assert value == 10
        assert warning is not None
        assert "must be >=" in warning


class TestGetHAConfig:
    def test_reads_shared_environment_configuration(self, monkeypatch):
        monkeypatch.setenv("HA_URL", "https://ha.example.com")
        monkeypatch.setenv("HA_TOKEN", "secret")
        monkeypatch.setenv("HA_REQUEST_TIMEOUT", "25")

        assert get_ha_config() == ("https://ha.example.com", "secret", 25)

    def test_warning_uses_caller_selected_stream(self, monkeypatch):
        monkeypatch.setenv("HA_REQUEST_TIMEOUT", "not-a-number")
        warning_stream = StringIO()

        assert get_ha_config(warning_stream=warning_stream)[2] == 10
        assert warning_stream.getvalue() == (
            "⚠️  HA_REQUEST_TIMEOUT must be an integer, got 'not-a-number'; using 10\n"
        )


class TestValidateHAURL:
    def test_valid_http_url(self):
        assert validate_ha_url("http://homeassistant.local:8123") is None

    def test_valid_https_url(self):
        assert validate_ha_url("https://example.com") is None

    def test_missing_scheme_is_invalid(self):
        error = validate_ha_url("homeassistant.local:8123")
        assert error is not None
        assert "http://" in error

    def test_missing_host_is_invalid(self):
        error = validate_ha_url("http://")
        assert error is not None
        assert "hostname" in error

    @pytest.mark.parametrize("url", ["http://:8123", "https:///api"])
    def test_missing_hostname_is_invalid_even_with_port_or_path(self, url):
        assert validate_ha_url(url) is not None

    def test_default_ha_url_is_valid(self):
        assert validate_ha_url(DEFAULT_HA_URL) is None


class TestResolveSummary:
    """Tests for resolve_summary()."""

    def make_args(self, summary=False, no_summary=False):
        """Helper to create a namespace matching the CLI argparse setup."""
        ns = argparse.Namespace()
        ns.summary = summary
        ns.no_summary = no_summary
        return ns

    def test_summary_flag_returns_true(self):
        assert resolve_summary(self.make_args(summary=True)) is True

    def test_no_summary_flag_returns_false(self):
        assert resolve_summary(self.make_args(no_summary=True)) is False

    def test_conflicting_flags_warns_and_returns_true(self, capsys):
        result = resolve_summary(self.make_args(summary=True, no_summary=True))
        assert result is True
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        assert "--summary" in captured.err.lower()

    def test_neither_flag_with_tty_returns_false(self, monkeypatch):
        class TTY:
            def isatty(self):
                return True

        monkeypatch.setattr("sys.stdout", TTY())
        result = resolve_summary(self.make_args())
        assert result is False

    def test_neither_flag_with_non_tty_returns_true(self, monkeypatch):
        class NonTTY:
            def isatty(self):
                return False

        monkeypatch.setattr("sys.stdout", NonTTY())
        result = resolve_summary(self.make_args())
        assert result is True

    def test_missing_attributes_does_not_crash(self, monkeypatch):
        class NonTTY:
            def isatty(self):
                return False

        monkeypatch.setattr("sys.stdout", NonTTY())
        bare = argparse.Namespace()
        result = resolve_summary(bare)
        # Falls through to non-TTY → True
        assert result is True


class TestResolveMaxChars:
    def _args(self, **kw):
        ns = argparse.Namespace()
        ns.max_chars = None
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_explicit_wins_over_env(self, monkeypatch):
        from tools.common import resolve_max_chars

        monkeypatch.setenv("HA_CLI_MAX_CHARS", "2000")
        assert resolve_max_chars(self._args(max_chars=500), summary=True) == 500

    def test_zero_disables(self):
        from tools.common import resolve_max_chars

        assert resolve_max_chars(self._args(max_chars=0), summary=True) is None

    def test_env_overrides_default(self, monkeypatch):
        from tools.common import resolve_max_chars

        monkeypatch.setenv("HA_CLI_MAX_CHARS", "3000")
        assert resolve_max_chars(self._args(), summary=True) == 3000

    def test_default_only_in_summary(self, monkeypatch):
        from tools.common import DEFAULT_SUMMARY_MAX_CHARS, resolve_max_chars

        monkeypatch.delenv("HA_CLI_MAX_CHARS", raising=False)
        assert (
            resolve_max_chars(self._args(), summary=True) == DEFAULT_SUMMARY_MAX_CHARS
        )
        assert resolve_max_chars(self._args(), summary=False) is None

    def test_invalid_env_falls_through(self, monkeypatch):
        from tools.common import DEFAULT_SUMMARY_MAX_CHARS, resolve_max_chars

        monkeypatch.setenv("HA_CLI_MAX_CHARS", "not-a-number")
        assert (
            resolve_max_chars(self._args(), summary=True) == DEFAULT_SUMMARY_MAX_CHARS
        )


class TestAddOutputShapeArgs:
    def test_registers_all_flags(self):
        from tools.common import add_output_shape_args

        p = argparse.ArgumentParser()
        add_output_shape_args(p)
        ns = p.parse_args(["--first", "5", "--pick", "a,b", "--max-chars", "100"])
        assert ns.first == 5
        assert ns.pick == "a,b"
        assert ns.max_chars == 100

    def test_partial_registration(self):
        from tools.common import add_output_shape_args

        p = argparse.ArgumentParser()
        add_output_shape_args(p, first=False, max_chars=False)
        ns = p.parse_args(["--pick", "a"])
        assert ns.pick == "a"
        assert not hasattr(ns, "first")
        assert not hasattr(ns, "max_chars")

    def test_max_chars_help_uses_shared_default(self):
        from tools.common import DEFAULT_SUMMARY_MAX_CHARS, add_output_shape_args

        p = argparse.ArgumentParser()
        add_output_shape_args(p)
        action = next(a for a in p._actions if a.dest == "max_chars")
        assert str(DEFAULT_SUMMARY_MAX_CHARS) in action.help


class TestAddSummaryArgs:
    def test_registers_both_flags(self):
        from tools.common import add_summary_args

        p = argparse.ArgumentParser()
        add_summary_args(p)
        ns = p.parse_args(["--summary"])
        assert ns.summary is True
        assert ns.no_summary is False
        ns2 = p.parse_args(["--no-summary"])
        assert ns2.summary is False
        assert ns2.no_summary is True

    def test_both_flags_allowed_not_mutually_exclusive(self):
        """Both flags can be set together (resolve_summary handles the conflict)."""
        from tools.common import add_summary_args

        p = argparse.ArgumentParser()
        add_summary_args(p)
        ns = p.parse_args(["--summary", "--no-summary"])
        assert ns.summary is True
        assert ns.no_summary is True


class TestIsTTY:
    """Tests for _is_tty()."""

    def test_returns_true_when_stdout_is_tty(self, monkeypatch):
        class TTY:
            def isatty(self):
                return True

        monkeypatch.setattr("sys.stdout", TTY())
        assert _is_tty() is True

    def test_returns_false_when_stdout_piped(self, monkeypatch):
        class NonTTY:
            def isatty(self):
                return False

        monkeypatch.setattr("sys.stdout", NonTTY())
        assert _is_tty() is False

    def test_returns_false_when_stdout_is_none(self, monkeypatch):
        monkeypatch.setattr("sys.stdout", None)
        assert _is_tty() is False

    def test_returns_false_when_stdout_has_no_isatty(self, monkeypatch):
        monkeypatch.setattr("sys.stdout", object())
        assert _is_tty() is False

    def test_returns_false_when_isatty_raises_typeerror(self, monkeypatch):
        class BadStdout:
            def isatty(self):
                raise TypeError("not callable")

        monkeypatch.setattr("sys.stdout", BadStdout())
        assert _is_tty() is False

    def test_returns_false_when_isatty_raises_valueerror(self, monkeypatch):
        class ClosedStdout:
            def isatty(self):
                raise ValueError("I/O operation on closed file")

        monkeypatch.setattr("sys.stdout", ClosedStdout())
        assert _is_tty() is False


class _ConcreteValidator(ValidatorBase):
    """Concrete subclass for testing ValidatorBase functionality."""

    def _validate(self) -> bool:
        return True


class TestValidatorBase:
    """Test ValidatorBase class."""

    def test_reexports_compact_diagnostics(self):
        assert format_diagnostics(["error"], ["warning"], ["info"]) == (
            "ERROR: error\nWARN: warning\nINFO: info"
        )

    def test_validatorbase_cannot_be_instantiated(self):
        """L33: ValidatorBase is abstract — direct instantiation must fail."""
        with pytest.raises(TypeError):
            ValidatorBase("/tmp")

    def test_template_missing_config_dir(self, tmp_path):
        v = _ConcreteValidator(str(tmp_path / "missing"))
        assert v.validate_all() is False
        assert any("does not exist" in e for e in v.errors)

    @pytest.fixture(autouse=True)
    def _config_dir(self, tmp_path):
        self.config_dir = Path(tmp_path)

    def test_init_sets_defaults(self):
        v = _ConcreteValidator(str(self.config_dir))
        assert v.config_dir == self.config_dir
        assert v.errors == []
        assert v.warnings == []
        assert v.info == []

    def test_get_yaml_files(self):
        (self.config_dir / "test.yaml").write_text("key: value")
        (self.config_dir / "test.yml").write_text("key: value")
        (self.config_dir / "test.txt").write_text("not yaml")

        v = _ConcreteValidator(str(self.config_dir))
        yaml_files = v.get_yaml_files()
        names = {f.name for f in yaml_files}
        assert "test.yaml" in names
        assert "test.yml" in names
        assert "test.txt" not in names

    def test_print_results_valid(self, capsys):
        v = _ConcreteValidator(str(self.config_dir))
        v.print_results()
        captured = capsys.readouterr()
        assert "is valid!" in captured.out

    def test_print_results_with_errors(self, capsys):
        v = _ConcreteValidator(str(self.config_dir))
        v.errors.append("Test error")
        v.print_results()
        captured = capsys.readouterr()
        assert "Test error" in captured.err
        assert "validation failed" in captured.err

    def test_print_results_with_warnings_only(self, capsys):
        v = _ConcreteValidator(str(self.config_dir))
        v.warnings.append("Test warning")
        v.print_results()
        captured = capsys.readouterr()
        assert "Test warning" in captured.err
        assert "with warnings" in captured.out

    def test_print_results_with_info(self, capsys):
        v = _ConcreteValidator(str(self.config_dir))
        v.info.append("Test info")
        v.print_results()
        captured = capsys.readouterr()
        assert "Test info" in captured.err


class TestLoadYamlChecked:
    """Tests for ValidatorBase.load_yaml_checked."""

    def test_valid_file_returns_data_and_ok(self, tmp_path):
        f = tmp_path / "good.yaml"
        f.write_text("key: value\n", encoding="utf-8")
        v = _ConcreteValidator(str(tmp_path))
        data, ok = v.load_yaml_checked(f)
        assert ok is True
        assert data == {"key": "value"}
        assert not v.errors

    def test_empty_file_returns_none_and_ok(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        v = _ConcreteValidator(str(tmp_path))
        data, ok = v.load_yaml_checked(f)
        assert ok is True
        assert data is None
        assert not v.errors

    def test_malformed_yaml_records_error_and_returns_false(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("key: [\n", encoding="utf-8")
        v = _ConcreteValidator(str(tmp_path))
        data, ok = v.load_yaml_checked(f)
        assert ok is False
        assert data is None
        assert any("bad.yaml" in e for e in v.errors)

    def test_nonexistent_file_records_error_and_returns_false(self, tmp_path):
        f = tmp_path / "nonexistent.yaml"
        v = _ConcreteValidator(str(tmp_path))
        data, ok = v.load_yaml_checked(f)
        assert ok is False
        assert data is None
        assert any("nonexistent.yaml" in e for e in v.errors)


class TestArgparseTypes:
    """Argparse integer and float types enforce their documented bounds."""

    def test_positive_int_accepts_valid(self):
        from tools.common import positive_int

        assert positive_int("5") == 5
        assert isinstance(positive_int("5"), int)

    def test_positive_int_rejects_zero(self):
        from tools.common import positive_int

        with pytest.raises((argparse.ArgumentTypeError, ValueError)):
            positive_int("0")

    def test_positive_int_rejects_negative(self):
        from tools.common import positive_int

        with pytest.raises((argparse.ArgumentTypeError, ValueError)):
            positive_int("-1")

    def test_positive_int_rejects_float_string(self):
        from tools.common import positive_int

        with pytest.raises((argparse.ArgumentTypeError, ValueError, TypeError)):
            positive_int("3.5")

    def test_positive_int_rejects_non_numeric(self):
        from tools.common import positive_int

        with pytest.raises((argparse.ArgumentTypeError, ValueError)):
            positive_int("abc")

    def test_non_negative_int_accepts_zero(self):
        from tools.common import non_negative_int

        assert non_negative_int("0") == 0
        assert isinstance(non_negative_int("0"), int)

    def test_non_negative_int_accepts_positive(self):
        from tools.common import non_negative_int

        assert non_negative_int("3") == 3

    def test_non_negative_int_rejects_negative(self):
        from tools.common import non_negative_int

        with pytest.raises((argparse.ArgumentTypeError, ValueError)):
            non_negative_int("-1")

    def test_is_valid_entity_id_valid(self):
        from tools.common import is_valid_entity_id

        assert is_valid_entity_id("sensor.living_room_temp") is True
        assert is_valid_entity_id("light.kitchen") is True
        assert is_valid_entity_id("automation.turn_off_1") is True

    def test_is_valid_entity_id_invalid(self):
        from tools.common import is_valid_entity_id

        assert is_valid_entity_id("sensor") is False
        assert is_valid_entity_id("sensor.") is False
        assert is_valid_entity_id(".living_room") is False
        assert is_valid_entity_id("sensor.temp.extra") is False
        assert is_valid_entity_id("sensor.living-room") is False
        assert is_valid_entity_id("sensor.temp ") is False
        assert is_valid_entity_id("") is False


class TestM5LoadEnvFile:
    """M5: load_env_file must not clobber existing env vars."""

    def test_does_not_clobber_existing_env(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("HA_TOKEN=FROM_FILE\n")
        os.environ["HA_TOKEN"] = "FROM_SHELL"
        try:
            load_env_file(env_file)
            assert os.environ["HA_TOKEN"] == "FROM_SHELL", (
                "real env var must not be clobbered by .env"
            )
        finally:
            del os.environ["HA_TOKEN"]

    def test_sets_missing_vars(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("HA_URL=http://from-file\n")
        os.environ.pop("HA_URL", None)
        try:
            load_env_file(env_file)
            assert os.environ["HA_URL"] == "http://from-file"
        finally:
            os.environ.pop("HA_URL", None)


class TestAtomicWriteText:
    """Tests for the shared atomic-write helper."""

    def test_writes_content_to_path(self, tmp_path):
        from tools.common import atomic_write_text

        target = tmp_path / "out.json"
        atomic_write_text(target, '{"key": "value"}')
        assert target.read_text() == '{"key": "value"}'

    def test_no_tmp_file_left_after_success(self, tmp_path):
        from tools.common import atomic_write_text

        target = tmp_path / "out.json"
        atomic_write_text(target, "content")
        assert not (tmp_path / "out.json.tmp").exists()

    def test_overwrites_existing_file(self, tmp_path):
        from tools.common import atomic_write_text

        target = tmp_path / "out.json"
        target.write_text("old")
        atomic_write_text(target, "new")
        assert target.read_text() == "new"

    def test_preserves_existing_permissions(self, tmp_path):
        from tools.common import atomic_write_text

        target = tmp_path / "out.json"
        target.write_text("old")
        target.chmod(0o600)
        atomic_write_text(target, "new")
        assert target.stat().st_mode & 0o777 == 0o600

    def test_original_survives_on_failure(self, tmp_path, monkeypatch):
        from tools import common
        from tools.common import atomic_write_text

        target = tmp_path / "out.json"
        target.write_text("original")

        def fail_replace(*a, **kw):
            raise OSError("mock failure")

        monkeypatch.setattr(common.os, "replace", fail_replace)
        atomic_write_text(target, "new content")

        assert target.read_text() == "original"
        assert not (tmp_path / "out.json.tmp").exists()

    def test_fsyncs_temporary_file_before_replacement(self, tmp_path, monkeypatch):
        from tools import common
        from tools.common import atomic_write_text

        target = tmp_path / "out.json"
        fsynced = []

        def record_fsync(fd):
            fsynced.append(fd)

        monkeypatch.setattr(common.os, "fsync", record_fsync)
        assert atomic_write_text(target, "content") is True

        assert len(fsynced) == 1
        assert target.read_text() == "content"

    def test_warns_on_oserror(self, tmp_path, monkeypatch, capsys):
        from tools import common
        from tools.common import atomic_write_text

        target = tmp_path / "out.json"

        def fail_replace(*a, **kw):
            raise OSError("mock failure")

        monkeypatch.setattr(common.os, "replace", fail_replace)
        atomic_write_text(target, "content")
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        assert str(target) in captured.err


class TestFailStderr:
    """Pin the fail_stderr helper."""

    def test_prints_emoji_message_to_stderr(self, capsys):
        from tools.common import fail_stderr

        result = fail_stderr("network unreachable")
        captured = capsys.readouterr()
        assert result == 1
        assert "\u274c network unreachable" in captured.err
        assert captured.out == ""

    def test_empty_message_still_prints_emoji(self, capsys):
        from tools.common import fail_stderr

        result = fail_stderr("")
        captured = capsys.readouterr()
        assert result == 1
        assert captured.err.startswith("\u274c")


class TestIterYamlPayloads:
    """ValidatorBase.iter_yaml_payloads generator tests."""

    def test_skips_secrets_yaml(self, tmp_path):
        """secrets.yaml is never yielded, even when present."""
        (tmp_path / "secrets.yaml").write_text("password: hunter2\n")
        (tmp_path / "configuration.yaml").write_text("homeassistant:\n")
        v = _ConcreteValidator(str(tmp_path))
        yielded = list(v.iter_yaml_payloads())
        names = [fp.name for fp, _ in yielded]
        assert "secrets.yaml" not in names
        assert "configuration.yaml" in names

    def test_load_failure_recorded_and_skipped(self, tmp_path):
        """Malformed YAML records an error and is not yielded."""
        (tmp_path / "broken.yaml").write_text("bad: [\n")
        (tmp_path / "good.yaml").write_text("ok: true\n")
        v = _ConcreteValidator(str(tmp_path))
        before = len(v.errors)
        yielded = list(v.iter_yaml_payloads())
        assert len(v.errors) == before + 1
        assert len(yielded) == 1
        assert yielded[0][0].name == "good.yaml"

    def test_empty_yaml_skipped(self, tmp_path):
        """Empty YAML files (data is None) are silently skipped."""
        (tmp_path / "empty.yaml").write_text("")
        (tmp_path / "full.yaml").write_text("key: val\n")
        v = _ConcreteValidator(str(tmp_path))
        yielded = list(v.iter_yaml_payloads())
        names = [fp.name for fp, _ in yielded]
        assert "empty.yaml" not in names
        assert "full.yaml" in names
