"""Tests for tools/commands/validate.py — in-process validator runner."""

import hashlib
import inspect
import json
from argparse import Namespace
from unittest.mock import patch

from tests.helpers import parse_command_args
from tools.commands import validate
from tools.commands.validate import ValidatorResult, _run_one, run, run_validators
from tools.validators.yaml import YAMLValidator

_FAKE_VALIDATOR_SOURCE = "class YAMLValidator:\n    pass"


def _fake_source_hash(source: str = _FAKE_VALIDATOR_SOURCE) -> str:
    return hashlib.sha1("\n".join((source, source)).encode()).hexdigest()


def _raise_value_error(*_args, **_kwargs):
    raise ValueError("bug")


def _raise_runtime_error(*_args, **_kwargs):
    raise RuntimeError("boom")


class TestValidatorResult:
    def test_constructs_with_all_fields(self):
        r = ValidatorResult(
            description="Test",
            passed=True,
            stderr="err",
            duration=0.5,
        )
        assert r.description == "Test"
        assert r.passed is True
        assert r.duration == 0.5

    def test_cached_defaults_to_false(self):
        r = ValidatorResult(description="Test", passed=True, duration=0.0)
        assert r.cached is False

    def test_cached_true(self):
        r = ValidatorResult(description="Test", passed=True, duration=0.0, cached=True)
        assert r.cached is True


class TestRunOne:
    def test_private_cache_helpers_do_not_accept_unused_description(self):
        assert (
            "description"
            not in inspect.signature(validate._load_cached_result).parameters
        )
        assert (
            "description" not in inspect.signature(validate._run_validator).parameters
        )

    def test_validator_duration_uses_perf_counter(self, config_dir, monkeypatch):
        ticks = iter((10.0, 11.0))

        def wall_clock_called():
            raise AssertionError("validator duration must not use wall-clock time")

        monkeypatch.setattr(validate.time, "perf_counter", lambda: next(ticks))
        monkeypatch.setattr(validate.time, "time", wall_clock_called)
        result = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=True)
        assert result.passed is True
        assert result.duration == 1.0

    def test_run_validator_returns_internal_execution_result(self, config_dir):
        from tools.commands.validate import _run_validator, _ValidatorExecutionResult

        instance = YAMLValidator(config_dir, quiet=True, summary=True)
        result = _run_validator(instance, 0.0)
        assert isinstance(result, _ValidatorExecutionResult)
        assert result.passed is True
        assert result.cached is False
        assert result.duration is not None

    def test_successful_validator(self, config_dir):
        """A validator that passes returns passed=True."""
        from tools.validators.yaml import YAMLValidator

        result = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=True)
        assert result.passed is True
        assert isinstance(result.duration, float)
        assert result.duration >= 0
        assert result.cached is False

    def test_failing_validator(self, tmp_path):
        """A validator that finds errors returns passed=False."""
        from tools.validators.yaml import YAMLValidator

        result = _run_one(
            YAMLValidator,
            "YAML",
            str(tmp_path / "nonexistent"),
            quiet=True,
            force=True,
        )
        assert result.passed is False

    def test_validator_exception_caught(self):
        """If a validator raises unexpectedly, it's captured as a failure."""
        from tools.validators.yaml import YAMLValidator

        with patch.object(
            YAMLValidator, "validate_all", side_effect=RuntimeError("boom")
        ):
            result = _run_one(YAMLValidator, "YAML", "config", quiet=True, force=True)
        assert result.passed is False
        assert "Failed to run validator" in result.stderr
        assert "boom" in result.stderr

    def test_system_exit_zero_treated_as_success(self):
        """A validator that raises SystemExit(0) passes."""
        from tools.validators.yaml import YAMLValidator

        with patch.object(YAMLValidator, "validate_all", side_effect=SystemExit(0)):
            result = _run_one(YAMLValidator, "YAML", "config", quiet=True, force=True)
        assert result.passed is True

    def test_system_exit_nonzero_treated_as_failure(self):
        from tools.validators.yaml import YAMLValidator

        with patch.object(YAMLValidator, "validate_all", side_effect=SystemExit(1)):
            result = _run_one(YAMLValidator, "YAML", "config", quiet=True, force=True)
        assert result.passed is False

    def test_incomplete_hash_does_not_read_or_write_cache(
        self, config_dir, monkeypatch
    ):
        from tools.validators.yaml import YAMLValidator

        monkeypatch.setattr(
            validate,
            "_compute_hash_status",
            lambda *_args: ("digest", False),
        )
        with (
            patch("tools.commands.validate.load_cache") as load,
            patch("tools.commands.validate.save_cache") as save,
        ):
            result = _run_one(
                YAMLValidator, "YAML", config_dir, quiet=True, force=False
            )
        assert result.cached is False
        load.assert_not_called()
        save.assert_not_called()

    def test_malformed_cache_record_is_a_cache_miss(self, config_dir):
        from tools.cache import cache_path
        from tools.validators.yaml import YAMLValidator

        first = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=True)
        assert first.passed
        path = cache_path(config_dir, YAMLValidator.__name__)
        data = json.loads(path.read_text())
        data["passed"] = 1
        path.write_text(json.dumps(data))

        result = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=False)
        assert result.passed
        assert result.cached is False

    def test_failed_cache_record_is_a_cache_miss(self, config_dir):
        from tools.cache import cache_path
        from tools.validators.yaml import YAMLValidator

        first = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=True)
        assert first.passed
        path = cache_path(config_dir, YAMLValidator.__name__)
        data = json.loads(path.read_text())
        data["passed"] = False
        path.write_text(json.dumps(data))

        result = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=False)

        assert result.passed
        assert result.cached is False

    def test_quiet_propagated_to_validator(self, config_dir, monkeypatch):
        """_run_one forwards quiet to the validator instance."""
        (config_dir / "configuration.yaml").write_text("homeassistant:\n")
        captured = {}
        orig_init = YAMLValidator.__init__

        def spy(self, *args, **kwargs):
            captured["kwargs"] = kwargs
            orig_init(self, *args, **kwargs)

        monkeypatch.setattr(YAMLValidator, "__init__", spy)
        _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=True)
        assert captured["kwargs"].get("quiet") is True

    def test_cache_hit_returns_cached_result(self, config_dir):
        """When file hash matches cache, validation is skipped.
        M1: fhash now includes source hash component — mock it too."""
        from tools.validators.yaml import YAMLValidator

        hash_val = f"abc123:{_fake_source_hash()}"
        with (
            patch(
                "tools.commands.validate._compute_hash_status",
                return_value=("abc123", True),
            ),
            patch(
                "tools.commands.validate.inspect.getsource",
                return_value=_FAKE_VALIDATOR_SOURCE,
            ),
            patch(
                "tools.commands.validate.load_cache",
                return_value={"hash": hash_val, "passed": True, "duration": 0.42},
            ),
        ):
            result = _run_one(
                YAMLValidator, "YAML", config_dir, quiet=True, force=False
            )
        assert result.passed is True
        assert result.cached is True
        assert result.duration == 0.42

    def test_cache_hit_preserves_saved_diagnostics(self, config_dir):
        """A cached pass retains diagnostics from its original validation."""
        from tools.validators.yaml import YAMLValidator

        hash_val = f"abc123:{_fake_source_hash()}"
        with (
            patch(
                "tools.commands.validate._compute_hash_status",
                return_value=("abc123", True),
            ),
            patch(
                "tools.commands.validate.inspect.getsource",
                return_value=_FAKE_VALIDATOR_SOURCE,
            ),
            patch(
                "tools.commands.validate.load_cache",
                return_value={
                    "hash": hash_val,
                    "passed": True,
                    "duration": 0.42,
                    "stderr": "WARN: previous warning",
                },
            ),
        ):
            result = _run_one(
                YAMLValidator, "YAML", config_dir, quiet=True, force=False
            )
        assert result.cached is True
        assert result.stderr == "WARN: previous warning"

    def test_cache_miss_runs_validator(self, config_dir):
        """Hash mismatch runs full validation."""
        from tools.validators.yaml import YAMLValidator

        with (
            patch(
                "tools.commands.validate._compute_hash_status",
                return_value=("newhash", True),
            ),
            patch(
                "tools.commands.validate.load_cache",
                return_value={"hash": "oldhash", "passed": True, "duration": 0.1},
            ),
        ):
            result = _run_one(
                YAMLValidator, "YAML", config_dir, quiet=True, force=False
            )
        assert result.cached is False

    def test_force_bypasses_cache(self, config_dir):
        """--force ignores cached result."""
        from tools.validators.yaml import YAMLValidator

        with (
            patch(
                "tools.commands.validate._compute_hash_status",
                return_value=("abc", True),
            ),
            patch(
                "tools.commands.validate.load_cache",
                return_value={"hash": "abc", "passed": False, "duration": 0.1},
            ),
        ):
            result = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=True)
        assert result.cached is False

    def test_cache_failure_falls_through_to_run(self, config_dir):
        """If load_cache raises, validation still runs."""
        from tools.validators.yaml import YAMLValidator

        with patch(
            "tools.commands.validate.load_cache",
            side_effect=OSError("disk full"),
        ):
            result = _run_one(
                YAMLValidator, "YAML", config_dir, quiet=True, force=False
            )
        assert result.passed is True
        assert result.cached is False

    def test_save_cache_called_on_success(self, config_dir):
        """On pass, result is cached (hash now includes source component)."""
        from tools.validators.yaml import YAMLValidator

        file_hash = "hash123"
        combined_hash = f"{file_hash}:{_fake_source_hash()}"
        with (
            patch(
                "tools.commands.validate._compute_hash_status",
                return_value=(file_hash, True),
            ),
            patch(
                "tools.commands.validate.inspect.getsource",
                return_value=_FAKE_VALIDATOR_SOURCE,
            ),
            patch("tools.commands.validate.load_cache", return_value=None),
            patch("tools.commands.validate.save_cache") as mock_save,
        ):
            result = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=True)
        assert result.passed is True
        mock_save.assert_called_once()
        args, kwargs = mock_save.call_args
        assert args[2] == "YAML"  # description
        assert args[3] == combined_hash  # file_hash (now includes source hash)
        assert args[4] is True  # passed

    def test_save_cache_not_called_on_failure(self, tmp_path):
        """Failures are not cached — they always re-run."""
        from tools.validators.yaml import YAMLValidator

        with (
            patch("tools.commands.validate.load_cache", return_value=None),
            patch("tools.commands.validate.save_cache") as mock_save,
        ):
            result = _run_one(
                YAMLValidator,
                "YAML",
                str(tmp_path / "nonexistent"),
                quiet=True,
                force=True,
            )
        assert result.passed is False
        mock_save.assert_not_called()

    def test_compute_hash_non_oserror_returns_failed_result(
        self, monkeypatch, tmp_path
    ):
        import tools.commands.validate as mod
        from tools.validators.yaml import YAMLValidator

        (tmp_path / "configuration.yaml").write_text("a: 1")
        monkeypatch.setattr(
            mod,
            "_compute_hash_status",
            _raise_value_error,
        )
        result = mod._run_one(YAMLValidator, "YAML", str(tmp_path), True, True)
        assert result.passed is False
        assert "bug" in result.stderr

    def test_save_cache_unexpected_error_returns_failed_result(
        self, monkeypatch, tmp_path
    ):
        import tools.commands.validate as mod
        from tools.validators.yaml import YAMLValidator

        (tmp_path / "configuration.yaml").write_text("a: 1")
        monkeypatch.setattr(
            mod, "_compute_hash_status", lambda *a, **k: ("fakehash", True)
        )
        monkeypatch.setattr(mod, "load_cache", lambda *a, **k: None)
        monkeypatch.setattr(
            mod,
            "save_cache",
            _raise_runtime_error,
        )
        result = mod._run_one(YAMLValidator, "YAML", str(tmp_path), True, False)
        assert result.passed is False
        assert "boom" in result.stderr

    def test_validator_init_exception_returns_failed_result(self, tmp_path):
        """A validator __init__ that raises must yield a failed result."""
        import tools.commands.validate as mod
        from tools.validators.yaml import YAMLValidator

        class BoomValidator(YAMLValidator):
            def __init__(self, *a, **k):
                raise RuntimeError("init blew up")

        result = mod._run_one(BoomValidator, "Boom", str(tmp_path), True, True)
        assert result.passed is False
        assert "init blew up" in result.stderr

    def test_validator_source_change_invalidates_cache(self, config_dir, monkeypatch):
        """M1: editing a validator's source must invalidate its cache."""
        import tools.commands.validate as vmod
        from tools.validators.yaml import YAMLValidator

        # First run populates the cache.
        r1 = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=False)
        assert r1.passed
        assert not r1.cached

        # Second run hits the cache (same hash).
        r2 = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=False)
        assert r2.cached

        # Now mutate the perceived source of YAMLValidator — cache must miss.
        original_getsource = vmod.inspect.getsource
        monkeypatch.setattr(
            vmod.inspect,
            "getsource",
            lambda obj: original_getsource(obj) + " # changed",
        )
        r3 = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=False)
        assert not r3.cached, "validator source change must invalidate cache"

    def test_validator_base_source_change_invalidates_cache(
        self, config_dir, monkeypatch
    ):
        """Changes to shared validator behavior must invalidate each cache."""
        import tools.commands.validate as vmod
        from tools.validators.base import ValidatorBase
        from tools.validators.yaml import YAMLValidator

        first = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=False)
        assert first.passed
        assert _run_one(
            YAMLValidator, "YAML", config_dir, quiet=True, force=False
        ).cached

        original_getsource = vmod.inspect.getsource

        def fake_getsource(obj):
            source = original_getsource(obj)
            if obj is ValidatorBase:
                source += "\n# shared behavior changed"
            return source

        monkeypatch.setattr(vmod.inspect, "getsource", fake_getsource)
        result = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=False)
        assert result.cached is False

    def test_save_cache_failure_does_not_crash(self, config_dir):
        """If saving cache throws, validation result is still returned."""
        from tools.validators.yaml import YAMLValidator

        with (
            patch("tools.commands.validate.load_cache", return_value=None),
            patch(
                "tools.commands.validate.save_cache",
                side_effect=OSError("disk full"),
            ),
        ):
            result = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=True)
        assert result.passed is True

    def test_system_exit_path_includes_diagnostic_lines(self, config_dir, monkeypatch):
        """SystemExit path must surface errors/warnings/info like the success path."""
        from tools.validators.yaml import YAMLValidator

        def fake_validate(self):
            self.errors.append("boom: bad entity")
            self.warnings.append("warn: missing alias")
            raise SystemExit(1)

        monkeypatch.setattr(YAMLValidator, "validate_all", fake_validate)
        monkeypatch.setattr(YAMLValidator, "file_deps", lambda self: [])
        result = _run_one(YAMLValidator, "YAML", config_dir, quiet=True, force=True)
        assert result.passed is False
        assert "ERROR: boom: bad entity" in result.stderr
        assert "WARN: warn: missing alias" in result.stderr


class TestRunValidators:
    def test_results_keep_declared_validator_order_when_completion_is_racy(
        self, config_dir, monkeypatch
    ):
        """Parallel completion order must not change diagnostic presentation order."""
        import time

        class FirstValidator:
            pass

        class SecondValidator:
            pass

        monkeypatch.setattr(
            validate,
            "_VALIDATORS",
            [(FirstValidator, "first"), (SecondValidator, "second")],
        )

        def fake_run_one(
            cls: object, description: str, *_args: object, **_kwargs: object
        ) -> ValidatorResult:
            if description == "first":
                time.sleep(0.01)
            return ValidatorResult(description=description, passed=True)

        monkeypatch.setattr(validate, "_run_one", fake_run_one)

        results = run_validators(config_dir, quiet=True, force=True)

        assert [result.description for result in results] == ["first", "second"]

    def test_returns_all_results(self, config_dir):
        """Default suite runs 7 validators (yaml, refs, dup, svc, tpl, stale, ha)."""
        results = run_validators(config_dir, quiet=True, force=True)
        assert len(results) == 7
        descriptions = {r.description for r in results}
        assert "YAML Syntax Validation" in descriptions
        assert "Entity/Device Reference Validation" in descriptions
        assert "Duplicate Automation ID Validation" in descriptions
        assert "Service Reference Validation" in descriptions
        assert "Jinja2 Template Validation" in descriptions
        assert "Stale Sensor Validation" in descriptions
        assert "Official Home Assistant Configuration Validation" in descriptions

    def test_all_results_have_required_fields(self, config_dir):
        results = run_validators(config_dir, quiet=True, force=True)
        for r in results:
            assert isinstance(r.description, str)
            assert isinstance(r.passed, bool)
            assert isinstance(r.stderr, str)
            assert isinstance(r.duration, float)

    def test_second_run_uses_real_cache(self, config_dir):
        """End-to-end: first run populates cache; second run hits it."""
        results1 = run_validators(config_dir, quiet=True, force=True)
        # All should be non-cached first time (force=True)
        for r in results1:
            assert r.cached is False

        results2 = run_validators(config_dir, quiet=True, force=False)
        # Without --force, unchanged files should yield cache hits
        cached_count = sum(1 for r in results2 if r.cached)
        assert cached_count >= 1, "Expected at least one validator to hit cache"

    def test_ha_official_is_never_cached(self, config_dir, monkeypatch):
        """HAOfficialValidator depends on the HA environment, not just files."""
        from tools.validators.ha_official import HAOfficialValidator

        monkeypatch.setattr(HAOfficialValidator, "validate_all", lambda self: True)
        monkeypatch.setattr(HAOfficialValidator, "file_deps", lambda self: [])
        with (
            patch(
                "tools.commands.validate._compute_hash_status",
                return_value=("hash", True),
            ),
            patch(
                "tools.commands.validate.load_cache",
                return_value={
                    "hash": "hash",
                    "passed": True,
                    "duration": 0.1,
                },
            ),
            patch("tools.commands.validate.save_cache") as mock_save,
        ):
            result = _run_one(
                HAOfficialValidator,
                "HA Official",
                config_dir,
                quiet=True,
                force=False,
            )
        # Even with a cache hit, should run (not cached)
        assert result.cached is False
        assert result.passed is True
        # And should not save (since file_deps is empty, no hash computed)
        mock_save.assert_not_called()

    def test_file_change_invalidates_cache(self, config_dir):
        """Modifying a watched file invalidates that validator's cache."""
        # First run: populate cache
        run_validators(config_dir, quiet=True, force=True)

        # Second run: should be cached
        results2 = run_validators(config_dir, quiet=True, force=False)
        # Count cached for YAML (fast enough to re-run, HA official dominates)
        yaml_result = [r for r in results2 if "YAML" in r.description][0]
        assert yaml_result.cached is True

        # Touch a YAML file
        import os

        cf = os.path.join(config_dir, "configuration.yaml")
        with open(cf, "a") as f:
            f.write("\n# cache bust\n")

        # Third run: YAML validator should re-run (no longer cached)
        results3 = run_validators(config_dir, quiet=True, force=False)
        yaml_result3 = [r for r in results3 if "YAML" in r.description][0]
        assert yaml_result3.cached is False


class TestM3PassingWarnings:
    """M3: passing-validator warnings must be shown in verbose mode."""

    def test_passing_validator_warrning_visible_in_verbose(
        self, config_dir, capsys, monkeypatch
    ):
        from tools.validators.yaml import YAMLValidator

        def fake_validate_all(self):
            self.warnings.append("minor: missing alias somewhere")
            self.info.append("info: 3 automations processed")
            return True

        monkeypatch.setattr(YAMLValidator, "validate_all", fake_validate_all)
        monkeypatch.setattr(YAMLValidator, "file_deps", lambda self: [])
        from argparse import Namespace

        from tools.commands.validate import run

        args = Namespace(
            config=config_dir, quiet=False, force=True, summary=False, no_summary=True
        )
        run(args)
        captured = capsys.readouterr()
        assert "minor: missing alias" in captured.err, (
            "passing-validator warnings must be visible in verbose mode"
        )


class TestRun:
    def _args(
        self, config_dir=None, quiet=False, force=False, summary=None, no_summary=None
    ):
        d = {"config": config_dir or "config", "quiet": quiet, "force": force}
        if summary is not None:
            d["summary"] = summary
        if no_summary is not None:
            d["no_summary"] = no_summary
        return Namespace(**d)

    def test_all_pass_returns_zero(self, config_dir):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[
                ValidatorResult(description="V1", passed=True, duration=0.1),
                ValidatorResult(description="V2", passed=True, duration=0.1),
            ],
        ):
            result = run(self._args(config_dir, quiet=True))
        assert result == 0

    def test_any_failure_returns_one(self, config_dir, capsys):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[
                ValidatorResult(description="V1", passed=True, duration=0.1),
                ValidatorResult(
                    description="V2", passed=False, stderr="broke", duration=0.1
                ),
            ],
        ):
            result = run(self._args(config_dir, quiet=True))
        assert result == 1
        out, err = capsys.readouterr()
        assert "FAIL" in out
        assert "broke" in err

    def test_quiet_suppresses_pass_output(self, config_dir, capsys):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[ValidatorResult(description="V1", passed=True, duration=0.1)],
        ):
            run(self._args(config_dir, quiet=True))
        out = capsys.readouterr().out
        assert "Running all validators" not in out
        assert "TEST SUMMARY" not in out

    @patch("tools.common._is_tty", return_value=True)
    def test_non_quiet_prints_banner_and_summary(self, _, config_dir, capsys):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[ValidatorResult(description="V1", passed=True, duration=0.1)],
        ):
            run(self._args(config_dir, quiet=False))
        out, err = capsys.readouterr()
        assert "Running all validators" in err
        assert "TEST SUMMARY" in err
        assert "Passed" in err

    @patch("tools.common._is_tty", return_value=True)
    def test_prints_duration_per_validator(self, _, config_dir, capsys):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[ValidatorResult(description="V1", passed=True, duration=1.5)],
        ):
            run(self._args(config_dir, quiet=False))
        out, err = capsys.readouterr()
        assert "1.50s" in err

    @patch("tools.common._is_tty", return_value=True)
    def test_cached_result_shows_cached_label(self, _, config_dir, capsys):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[
                ValidatorResult(
                    description="V1", passed=True, duration=0.0, cached=True
                ),
            ],
        ):
            run(self._args(config_dir, quiet=False))
        out, err = capsys.readouterr()
        assert "(cached)" in err

    @patch("tools.common._is_tty", return_value=True)
    def test_force_shows_cache_ignored_message(self, _, config_dir, capsys):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[ValidatorResult(description="V1", passed=True, duration=0.1)],
        ):
            run(self._args(config_dir, quiet=False, force=True))
        out, err = capsys.readouterr()
        assert "cache ignored" in err

    def test_passes_force_to_run_validators(self, config_dir):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[ValidatorResult(description="V1", passed=True, duration=0.1)],
        ) as mock_rv:
            run(self._args(config_dir, quiet=True, force=True))
        # config_dir passed positionally, quiet + force + summary as keywords
        mock_rv.assert_called_once_with(
            config_dir, quiet=True, force=True, summary=True
        )

    def test_overall_duration_uses_perf_counter(self, config_dir, monkeypatch):
        ticks = iter((20.0, 21.0))

        def wall_clock_called():
            raise AssertionError("overall duration must not use wall-clock time")

        monkeypatch.setattr(validate.time, "perf_counter", lambda: next(ticks))
        monkeypatch.setattr(validate.time, "time", wall_clock_called)
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[ValidatorResult(description="V1", passed=True, duration=0.1)],
        ):
            assert run(self._args(config_dir, quiet=True, summary=True)) == 0

    # ── Summary mode tests ──────────────────────────────────────────

    @patch("tools.common._is_tty", return_value=False)
    def test_summary_compact_output_all_pass(self, _, config_dir, capsys):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[ValidatorResult(description="V1", passed=True, duration=0.1)],
        ):
            run(self._args(config_dir, quiet=False))
        out = capsys.readouterr().out
        assert out.strip().startswith("PASS V1")
        # No banner, no emoji, no TEST SUMMARY
        assert "\U0001f50d" not in out
        assert "TEST SUMMARY" not in out
        # Expect final PASSED line
        assert "PASSED 1/1" in out

    @patch("tools.common._is_tty", return_value=False)
    def test_summary_with_failure_shows_compact_errors(self, _, config_dir, capsys):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[
                ValidatorResult(description="V1", passed=True, duration=0.1),
                ValidatorResult(
                    description="V2",
                    passed=False,
                    stderr="something broke",
                    duration=0.2,
                ),
            ],
        ):
            run(self._args(config_dir, quiet=False))
        out, err = capsys.readouterr()
        # Should show per-validator status
        assert "PASS V1" in out
        assert "FAIL V2" in out
        # Error should appear on stderr (no section headers in summary)
        assert "something broke" in err
        assert "📋" not in err  # no clipboard icon section header
        assert "Status:" not in err
        # Final FAILED line
        assert "FAILED 1/2" in out

    @patch("tools.common._is_tty", return_value=True)
    def test_summary_explicit_flag_in_tty(self, _, config_dir, capsys):
        with (
            patch(
                "tools.commands.validate.run_validators",
                return_value=[
                    ValidatorResult(description="V1", passed=True, duration=0.1)
                ],
            ),
        ):
            run(self._args(config_dir, quiet=False, summary=True))
        out = capsys.readouterr().out
        assert "PASS V1" in out
        assert "TEST SUMMARY" not in out

    @patch("tools.common._is_tty", return_value=False)
    def test_summary_with_quiet_suppresses_pass_lines(self, _, config_dir, capsys):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[
                ValidatorResult(description="V1", passed=True, duration=0.1),
                ValidatorResult(
                    description="V2", passed=False, stderr="error detail", duration=0.2
                ),
            ],
        ):
            run(self._args(config_dir, quiet=True))
        out, err = capsys.readouterr()
        # quiet suppresses PASS lines but still shows FAIL lines
        assert "PASS V1" not in out
        assert "FAIL V2" in out
        assert "error detail" in err
        # Final aggregate line still shows
        assert "FAILED 1/2" in out

    @patch("tools.common._is_tty", return_value=False)
    def test_summary_shows_cached_as_letter_c(self, _, config_dir, capsys):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[
                ValidatorResult(
                    description="V1", passed=True, duration=0.0, cached=True
                ),
            ],
        ):
            run(self._args(config_dir, quiet=False))
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 2
        # Per-validator line: "PASS V1 C" (no duration)
        assert lines[0].strip().endswith("C")
        assert "0.00s" not in lines[0]
        # Final aggregate line: "PASSED 1/1 (0.00s)" (has duration)
        assert lines[1].startswith("PASSED 1/1")
        assert "(cached)" not in "\n".join(lines)

    @patch("tools.common._is_tty", return_value=False)
    def test_no_summary_flag_forces_verbose_in_pipe(self, _, config_dir, capsys):
        """--no-summary forces verbose output even when stdout is not a TTY."""
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[ValidatorResult(description="V1", passed=True, duration=0.1)],
        ):
            run(self._args(config_dir, quiet=False, no_summary=True))
        out, err = capsys.readouterr()
        assert "🔍" in err  # banner present (verbose mode)
        assert "TEST SUMMARY" in err
        assert "Passed" in err

    @patch("tools.common._is_tty", return_value=False)
    def test_conflicting_flags_warning(self, _, config_dir, capsys):
        """Both --summary and --no-summary prints a warning."""
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[ValidatorResult(description="V1", passed=True, duration=0.1)],
        ):
            run(self._args(config_dir, quiet=False, summary=True, no_summary=True))
        _, err = capsys.readouterr()
        assert "WARN" in err
        assert "--summary" in err

    # ── Verbose mode with failure (covers 238, 260-272, 294) ──────

    @patch("tools.common._is_tty", return_value=True)
    def test_verbose_mode_with_failure_prints_detail(self, _, config_dir, capsys):
        with patch(
            "tools.commands.validate.run_validators",
            return_value=[
                ValidatorResult(
                    description="MyValidator",
                    passed=False,
                    stderr="the error text",
                    duration=1.5,
                ),
            ],
        ):
            run(self._args(config_dir, quiet=False))
        _, err = capsys.readouterr()
        assert "MyValidator" in err  # 260
        assert "FAILED" in err  # 238/262
        assert "the error text" in err  # 268-271
        assert "1.50s" in err  # 263
        assert "failed" in err.lower()  # 294


class TestAddParser:
    def test_subparser_registered_with_validate_name(self):
        """add_parser should register a 'validate' subcommand."""
        args = parse_command_args("validate", validate.add_parser, [])
        assert args.command == "validate"

    def test_add_parser_attaches_run_func(self):
        args = parse_command_args("validate", validate.add_parser, [])
        assert callable(args.func)

    def test_force_flag_defaults_false(self):
        args = parse_command_args("validate", validate.add_parser, [])
        assert args.force is False

    def test_force_flag_set_true(self):
        args = parse_command_args("validate", validate.add_parser, ["--force"])
        assert args.force is True

    def test_summary_flag_defaults_false(self):
        args = parse_command_args("validate", validate.add_parser, [])
        assert args.summary is False
        assert args.no_summary is False

    def test_summary_flag_set_true(self):
        args = parse_command_args("validate", validate.add_parser, ["--summary"])
        assert args.summary is True

    def test_no_summary_flag_set_true(self):
        args = parse_command_args("validate", validate.add_parser, ["--no-summary"])
        assert args.no_summary is True


class TestFormatResultLine:
    """Direct unit tests for _format_result_line."""

    def _result(self, **kw):
        defaults = dict(
            description="YAML Syntax Validation",
            passed=True,
            stderr="",
            duration=1.5,
            cached=False,
        )
        defaults.update(kw)
        return ValidatorResult(**defaults)

    def test_summary_mode_pass_with_duration(self):
        from tools.commands.validate import _format_result_line

        r = self._result(passed=True, duration=2.5, cached=False)
        assert (
            _format_result_line(r, summary=True, quiet=False)
            == "PASS YAML Syntax Validation (2.50s)"
        )

    def test_summary_mode_pass_cached(self):
        from tools.commands.validate import _format_result_line

        r = self._result(passed=True, duration=0.0, cached=True)
        assert (
            _format_result_line(r, summary=True, quiet=False)
            == "PASS YAML Syntax Validation C"
        )

    def test_summary_mode_fail(self):
        from tools.commands.validate import _format_result_line

        r = self._result(passed=False, duration=3.0)
        assert (
            _format_result_line(r, summary=True, quiet=False)
            == "FAIL YAML Syntax Validation (3.00s)"
        )

    def test_verbose_mode_pass(self):
        from tools.commands.validate import _format_result_line

        r = self._result(passed=True, duration=1.25, cached=False)
        line = _format_result_line(r, summary=False, quiet=False)
        assert "\u2705" in line
        assert "PASSED" in line
        assert "(1.25s)" in line

    def test_verbose_mode_pass_cached(self):
        from tools.commands.validate import _format_result_line

        r = self._result(passed=True, duration=0.0, cached=True)
        line = _format_result_line(r, summary=False, quiet=False)
        assert " (cached)" in line

    def test_quiet_suppresses_passing_line(self):
        from tools.commands.validate import _format_result_line

        r = self._result(passed=True)
        assert _format_result_line(r, summary=False, quiet=True) is None

    def test_quiet_does_not_suppress_failing_line(self):
        from tools.commands.validate import _format_result_line

        r = self._result(passed=False)
        assert _format_result_line(r, summary=False, quiet=True) is not None


class TestPrintIntro:
    """Direct unit tests for _print_intro."""

    def test_verbose_prints_banner(self, capsys):
        from tools.commands.validate import _print_intro

        _print_intro(force=False, quiet=False, summary=False)
        err = capsys.readouterr().err
        assert "Running Home Assistant Configuration Validation Tests" in err
        assert "Running all validators in parallel" in err

    def test_verbose_force_prints_cache_ignored_message(self, capsys):
        from tools.commands.validate import _print_intro

        _print_intro(force=True, quiet=False, summary=False)
        err = capsys.readouterr().err
        assert "cache ignored" in err

    def test_summary_mode_prints_nothing(self, capsys):
        from tools.commands.validate import _print_intro

        _print_intro(force=False, quiet=False, summary=True)
        assert capsys.readouterr().err == ""

    def test_quiet_mode_prints_nothing(self, capsys):
        from tools.commands.validate import _print_intro

        _print_intro(force=False, quiet=True, summary=False)
        assert capsys.readouterr().err == ""


class TestPrintSummaryBlock:
    """Direct unit tests for _print_summary_block."""

    def test_summary_pass_prints_passed_count(self, capsys):
        from tools.commands.validate import _print_summary_block

        results = [ValidatorResult(description="A", passed=True, duration=1.0)]
        _print_summary_block(
            results, all_passed=True, overall_duration=1.0, summary=True, quiet=False
        )
        out = capsys.readouterr().out
        assert "PASSED 1/1" in out
        assert "1.00s" in out

    def test_summary_fail_prints_failed_count(self, capsys):
        from tools.commands.validate import _print_summary_block

        results = [
            ValidatorResult(description="A", passed=True, duration=1.0),
            ValidatorResult(description="B", passed=False, duration=2.0),
        ]
        _print_summary_block(
            results, all_passed=False, overall_duration=3.0, summary=True, quiet=False
        )
        out = capsys.readouterr().out
        assert "FAILED 1/2" in out

    def test_verbose_mode_prints_test_summary_block_to_stderr(self, capsys):
        from tools.commands.validate import _print_summary_block

        results = [ValidatorResult(description="A", passed=True, duration=1.0)]
        _print_summary_block(
            results, all_passed=True, overall_duration=1.0, summary=False, quiet=False
        )
        err = capsys.readouterr().err
        assert "TEST SUMMARY" in err
        assert "Total tests: 1" in err
        assert "All tests passed" in err
