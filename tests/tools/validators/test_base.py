"""Tests for ValidatorBase.run_cli() classmethod."""

from typing import Any

import pytest

from tools.validators.base import ValidatorBase, format_diagnostics


class _DummyValidator(ValidatorBase):
    """Concrete validator for testing run_cli()."""

    validator_name = "Test validator"

    def __init__(self, config_dir: str = "config", **kwargs: Any) -> None:
        super().__init__(config_dir, **kwargs)
        self.called_with_kwargs = dict(kwargs)

    def file_deps(self) -> list[str]:
        return []

    def _validate(self) -> bool:
        return True


@pytest.fixture
def dummy_config(tmp_path):
    (tmp_path / "configuration.yaml").write_text("homeassistant:\n  name: Test\n")
    return tmp_path


class TestRunCli:
    def test_returns_zero_on_success(self, dummy_config, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dummy", str(dummy_config)])
        assert _DummyValidator.run_cli("dummy description") == 0

    def test_returns_one_when_config_dir_missing(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dummy", "/nonexistent"])
        assert _DummyValidator.run_cli("dummy description") == 1

    def test_returns_one_when_config_path_is_a_file(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("homeassistant:\n")
        monkeypatch.setattr("sys.argv", ["dummy", str(config_file)])
        assert _DummyValidator.run_cli("dummy description") == 1

    def test_default_config_dir_is_config(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["dummy"])
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "configuration.yaml").write_text("homeassistant:\n")
        assert _DummyValidator.run_cli("dummy description") == 0

    def test_description_propagated_to_argparse(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dummy", "--help"])
        with pytest.raises(SystemExit) as exc:
            _DummyValidator.run_cli("UNIQUE_DESCRIPTION_TOKEN")
        assert exc.value.code == 0

    def test_add_args_callback_invoked(self, dummy_config, monkeypatch):
        def add_extra(parser):
            parser.add_argument("--flag", action="store_true")

        monkeypatch.setattr("sys.argv", ["dummy", str(dummy_config), "--flag"])
        assert _DummyValidator.run_cli("dummy", add_args=add_extra) == 0

    def test_add_args_rejects_unknown_flag(self, dummy_config, monkeypatch):
        monkeypatch.setattr("sys.argv", ["dummy", str(dummy_config), "--quiet"])
        with pytest.raises(SystemExit) as exc:
            _DummyValidator.run_cli("dummy")
        assert exc.value.code == 2

    def test_build_validator_kwargs_invoked(self, dummy_config, monkeypatch):
        def to_kwargs(args):
            return {"quiet": getattr(args, "quiet", False)}

        monkeypatch.setattr("sys.argv", ["dummy", str(dummy_config)])
        captured_stdout = []

        def fake_print_results(self):
            captured_stdout.append(self.called_with_kwargs)

        monkeypatch.setattr(_DummyValidator, "print_results", fake_print_results)
        _DummyValidator.run_cli("dummy", build_validator_kwargs=to_kwargs)
        assert captured_stdout == [{"quiet": False}]

    def test_print_results_called(self, dummy_config, monkeypatch):
        called = []
        monkeypatch.setattr(
            _DummyValidator, "print_results", lambda self: called.append(True)
        )
        monkeypatch.setattr("sys.argv", ["dummy", str(dummy_config)])
        _DummyValidator.run_cli("dummy")
        assert called == [True]

    def test_quiet_mode_still_prints_errors(self, dummy_config, capsys):
        validator = _DummyValidator(str(dummy_config), quiet=True)
        validator.errors.append("bad config")
        validator.print_results()
        assert "bad config" in capsys.readouterr().err

    def test_get_yaml_files_returns_a_glob_order_independent_list(
        self, tmp_path, monkeypatch
    ):
        validator = _DummyValidator(str(tmp_path))

        def unordered_glob(path, pattern):
            if pattern == "*.yaml":
                return [path / "z.yaml", path / "a.yaml"]
            return [path / "z.yml", path / "a.yml"]

        monkeypatch.setattr(type(validator.config_dir), "glob", unordered_glob)

        assert validator.get_yaml_files() == [
            tmp_path / "a.yaml",
            tmp_path / "a.yml",
            tmp_path / "z.yaml",
            tmp_path / "z.yml",
        ]


def test_format_diagnostics_preserves_severity_order_and_prefixes():
    assert format_diagnostics(["bad"], ["careful"], ["note"]) == (
        "ERROR: bad\nWARN: careful\nINFO: note"
    )


def test_format_diagnostics_empty_is_empty():
    assert format_diagnostics([], [], []) == ""


def test_validate_all_called(dummy_config, monkeypatch):
    called = []
    monkeypatch.setattr(
        _DummyValidator, "validate_all", lambda self: called.append(True) or True
    )
    monkeypatch.setattr("sys.argv", ["dummy", str(dummy_config)])
    _DummyValidator.run_cli("dummy")
    assert called == [True]
