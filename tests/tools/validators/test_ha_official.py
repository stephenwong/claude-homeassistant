"""Tests for tools/ha_official_validator.py - official HA validation."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tools.validators.ha_official import (
    HAOfficialValidator,
    _is_benign_package_line,
    _is_explicit_error,
)


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "configuration.yaml").write_text("homeassistant:\n  name: Test\n")
    return tmp_path


@pytest.fixture
def validator(config_dir):
    return HAOfficialValidator(str(config_dir))


class TestHAOfficialValidatorMain:
    """Cover lines 169-186: main() function."""

    def test_main_valid(self, tmp_path, monkeypatch):
        from tools.validators.ha_official import main

        (tmp_path / "configuration.yaml").write_text("homeassistant:\n  name: Test\n")
        monkeypatch.setattr("sys.argv", ["ha_official_validator", str(tmp_path)])

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Configuration check successful!"
        mock_result.stderr = ""
        with patch(
            "tools.validators.ha_official.subprocess.run", return_value=mock_result
        ):
            assert main() == 0

    def test_main_invalid(self, tmp_path, monkeypatch):
        from tools.validators.ha_official import main

        monkeypatch.setattr("sys.argv", ["ha_official_validator", "/nonexistent"])
        assert main() == 1

    def test_subprocess_output_uses_replacement_decoding(self, validator, monkeypatch):
        result = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch(
            "tools.validators.ha_official.subprocess.run", return_value=result
        ) as run:
            assert validator.run_ha_check_config() is True
        assert run.call_args.kwargs["errors"] == "replace"

    def test_warning_on_stderr_is_not_an_error(self, validator):
        validator.parse_check_config_output("", "WARNING: deprecated setting\n")
        assert validator.errors == []
        assert any("deprecated" in warning for warning in validator.warnings)


@pytest.mark.parametrize(
    "message",
    [
        "Unable to locate turbojpeg library",
        "libturbojpeg not found",
        "Camera snapshot performance will be sub-optimal",
        "TurboJPEGSingleton warning",
        "selector']['reorder'] is not valid",
        "Traceback (most recent call last):",
        "Unable to install package somepkg",
        "No solution found when resolving",
        "requirements are unsatisfiable",
        "Requirements for xyz",
        "could not be loaded",
    ],
)
def test_ignorable_message(validator, message):
    assert validator.is_ignorable_message(message) is True


def test_runtimeerror_not_ignored_by_is_ignorable_message(validator):
    """M12: RuntimeError is no longer blanket-suppressed by is_ignorable_message."""
    assert validator.is_ignorable_message("RuntimeError: something") is False


def test_normal_error_not_ignorable(validator):
    msg = "Invalid configuration for sensor"
    assert validator.is_ignorable_message(msg) is False


@pytest.mark.parametrize(
    "line",
    [
        "ERROR: invalid config",
        "error: invalid config",
        "^^^RuntimeError: invalid config",
    ],
)
def test_is_explicit_error_matches_error_prefixes(line):
    assert _is_explicit_error(line) is True


@pytest.mark.parametrize(
    "line",
    ["Configuration check successful!", "Found 0 errors", "some error in the middle"],
)
def test_is_explicit_error_rejects_non_prefixes(line):
    assert _is_explicit_error(line) is False


@pytest.mark.parametrize(
    "line",
    [
        "Unable to install package somepkg",
        "NO SOLUTION FOUND WHEN RESOLVING dependencies",
        "Requirements are unsatisfiable",
        "requirements for package X",
    ],
)
def test_is_benign_package_line_matches_known_markers(line):
    assert _is_benign_package_line(line) is True


def test_is_benign_package_line_rejects_unrelated_line():
    assert _is_benign_package_line("RuntimeError: real configuration failure") is False


class TestL53IgnorablePatterns:
    """L53: pin each known-benign pattern so a regression surfaces."""

    @pytest.mark.parametrize(
        "pattern",
        [
            "Unable to install package foo",
            "No solution found when resolving",
            "Requirements are unsatisfiable",
            "Requirements for package X",
            "could not be loaded",
        ],
    )
    def test_ignorable_message_covers_known_patterns(self, validator, pattern):
        assert validator.is_ignorable_message(pattern) is True

    def test_normal_error_not_ignorable(self, validator):
        assert validator.is_ignorable_message("real config error") is False


class TestL52ErrorCountRegex:
    """L52: the regex must match several HA phrasings."""

    @pytest.mark.parametrize(
        "line,expected",
        [
            ("Found 3 errors in configuration", 3),
            ("3 errors found", 3),
            ("Found 3 errors", 3),
            ("Configuration has 1 error", 1),
        ],
    )
    def test_error_count_regex_matches_variants(self, validator, line, expected):
        validator.parse_check_config_output(line + "\n", "")
        # Should produce info for 0 errors, error for non-zero
        if expected == 0:
            assert any(str(expected) in i for i in validator.info)
        else:
            assert any(str(expected) in e for e in validator.errors)


class TestIsIgnorableTracebackLine:
    def test_python_file_line_benign_context(self, validator):
        assert (
            validator.is_ignorable_traceback_line(
                '  File "/usr/lib/python3.12/site-packages/test.py", line 42',
                benign_ctx=True,
            )
            is True
        )

    def test_python_file_line_no_benign_context(self, validator):
        """Without benign_ctx, traceback lines are NOT suppressed (M12)."""
        assert (
            validator.is_ignorable_traceback_line(
                '  File "/usr/lib/python3.12/site-packages/test.py", line 42',
                benign_ctx=False,
            )
            is False
        )

    def test_normal_line(self, validator):
        assert validator.is_ignorable_traceback_line("ERROR: something") is False


class TestParseCheckConfigOutput:
    def test_success_message(self, validator):
        validator.parse_check_config_output(
            "Testing configuration at /config\nConfiguration check successful!\n",
            "",
        )
        assert any("successful" in i for i in validator.info)
        assert len(validator.errors) == 0

    def test_error_message(self, validator):
        validator.parse_check_config_output(
            "ERROR: Invalid config for sensor.test\n", ""
        )
        assert any("Invalid config" in e for e in validator.errors)

    def test_explicit_error_with_ignorable_phrase_is_not_dropped(self, validator):
        validator.parse_check_config_output(
            "ERROR: Integration could not be loaded\n", ""
        )
        assert any("could not be loaded" in e for e in validator.errors)

    def test_lowercase_error_prefix_is_an_error(self, validator):
        validator.parse_check_config_output("error: invalid config\n", "")
        assert any("invalid config" in e for e in validator.errors)

    def test_warning_message(self, validator):
        validator.parse_check_config_output("WARNING: Deprecated feature used\n", "")
        assert any("Deprecated" in w for w in validator.warnings)

    def test_zero_errors_found(self, validator):
        validator.parse_check_config_output("0 errors found\n", "")
        assert len(validator.errors) == 0
        assert any("0 errors" in i for i in validator.info)

    def test_nonzero_errors_found(self, validator):
        validator.parse_check_config_output("3 errors found\n", "")
        assert any("3 errors" in e for e in validator.errors)

    def test_skips_ignorable_messages(self, validator):
        validator.parse_check_config_output("Unable to locate turbojpeg library\n", "")
        assert len(validator.errors) == 0
        assert len(validator.warnings) == 0

    def test_stderr_errors(self, validator):
        validator.parse_check_config_output("", "actual error message\n")
        assert any("actual error" in e for e in validator.errors)

    def test_stderr_skips_debug(self, validator):
        validator.parse_check_config_output("", "DEBUG: internal message\n")
        assert len(validator.errors) == 0

    def test_stderr_skips_info(self, validator):
        validator.parse_check_config_output("", "INFO: starting setup\n")
        assert len(validator.errors) == 0

    def test_stderr_skips_setup_messages(self, validator):
        validator.parse_check_config_output("", "Setup of domain sensor done\n")
        assert len(validator.errors) == 0

    def test_info_lines_not_included(self, validator):
        validator.parse_check_config_output("INFO: Some debug info\n", "")
        # INFO: lines are skipped
        assert not any("Some debug info" in i for i in validator.info)

    def test_other_lines_become_info(self, validator):
        validator.parse_check_config_output("Some informational line\n", "")
        assert any("informational" in i for i in validator.info)

    def test_error_substring_in_middle_not_flagged(self, validator):
        """Mid-line 'error' substring no longer triggers error classification."""
        validator.parse_check_config_output("Found no errors in the config\n", "")
        assert len(validator.errors) == 0

    def test_no_errors_found_not_flagged(self, validator):
        """'0 errors found' goes to info, not errors."""
        validator.parse_check_config_output("0 errors found at all\n", "")
        assert len(validator.errors) == 0
        assert any("0 errors" in i for i in validator.info)


class TestM11StderrNoiseFilter:
    """M11: benign-substring filters must not swallow real errors."""

    def test_benign_loading_still_suppressed(self, validator):
        validator.parse_check_config_output("", "INFO: Now loading integrations\n")
        assert validator.errors == []

    def test_error_during_loading_survives(self, validator):
        validator.parse_check_config_output(
            "", "Error loading configuration.yaml: bad key\n"
        )
        assert any("Error loading" in e for e in validator.errors)

    def test_failed_initialized_survives(self, validator):
        validator.parse_check_config_output(
            "", "Integration foo failed to be initialized\n"
        )
        assert any("failed to be initialized" in e for e in validator.errors)

    def test_failed_starting_survives(self, validator):
        validator.parse_check_config_output(
            "", "Failed starting integration some_integration\n"
        )
        assert any("Failed starting" in e for e in validator.errors)


class TestM12RuntimeErrorScoping:
    """M12: RuntimeError suppression must be scoped to benign-package context."""

    def test_runtimeerror_with_package_marker_suppressed(self, validator):
        stdout = (
            "Unable to install package somepkg\n"
            "Traceback (most recent call last):\n"
            "  File '/srv/foo.py', line 1, in <module>\n"
            "raise RuntimeError('boom')\n"
        )
        validator.parse_check_config_output(stdout, "")
        assert not any("RuntimeError" in e for e in validator.errors)

    def test_runtimeerror_with_package_marker_still_survives(self, validator):
        stdout = (
            "Unable to install package somepkg\n"
            "RuntimeError: real configuration failure\n"
        )
        validator.parse_check_config_output(stdout, "")
        assert any("real configuration failure" in e for e in validator.errors)

    def test_runtimeerror_without_marker_survives(self, validator):
        stdout = (
            "Traceback (most recent call last):\n"
            "  File '/srv/async_setup.py', line 42\n"
            "RuntimeError: Platform already configured: sensor\n"
        )
        validator.parse_check_config_output(stdout, "")
        assert any("RuntimeError" in e for e in validator.errors), (
            "genuine HA RuntimeError must not be blanket-suppressed"
        )


class TestRunHACheckConfig:
    def test_successful_check(self, validator):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Configuration check successful!"
        mock_result.stderr = ""

        with patch(
            "tools.validators.ha_official.subprocess.run",
            return_value=mock_result,
        ):
            assert validator.run_ha_check_config() is True

    def test_failed_check(self, validator):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "ERROR: Invalid configuration"
        mock_result.stderr = ""

        with patch(
            "tools.validators.ha_official.subprocess.run",
            return_value=mock_result,
        ):
            assert validator.run_ha_check_config() is False

    def test_failed_check_without_output_has_diagnostic(self, validator):
        mock_result = MagicMock(returncode=1, stdout="", stderr="")

        with patch(
            "tools.validators.ha_official.subprocess.run",
            return_value=mock_result,
        ):
            assert validator.run_ha_check_config() is False

        assert validator.errors

    def test_timeout(self, validator):
        with patch(
            "tools.validators.ha_official.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="test", timeout=120),
        ):
            assert validator.run_ha_check_config() is False
            assert any("timed out" in e for e in validator.errors)

    def test_ha_not_found(self, validator):
        with patch(
            "tools.validators.ha_official.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert validator.run_ha_check_config() is False
            assert any("not found" in e for e in validator.errors)

    def test_generic_exception_propagates(self, validator):
        with (
            patch(
                "tools.validators.ha_official.subprocess.run",
                side_effect=RuntimeError("unexpected"),
            ),
            pytest.raises(RuntimeError),
        ):
            validator.run_ha_check_config()

    def test_exit0_demotes_parsed_errors_to_warnings(self, validator):
        """HA exits 0 but stdout has an ERROR line not in the ignore list →
        demote to warnings, return passed=True (CI green)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "INFO: Successful config (partial)\nERROR: real error\n"
        mock_result.stderr = ""
        with patch(
            "tools.validators.ha_official.subprocess.run",
            return_value=mock_result,
        ):
            assert validator.run_ha_check_config() is True
        assert len(validator.errors) == 0
        assert len(validator.warnings) >= 1

    def test_nonzero_exit_treats_parsed_errors_as_authoritative(self, validator):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "ERROR: bad config\n"
        mock_result.stderr = ""
        with patch(
            "tools.validators.ha_official.subprocess.run",
            return_value=mock_result,
        ):
            assert validator.run_ha_check_config() is False
        assert any("bad config" in e for e in validator.errors)

    def test_nonexistent_config_dir(self):
        v = HAOfficialValidator("/nonexistent/path")
        assert v.validate_all() is False

    def test_missing_configuration_yaml(self, tmp_path):
        v = HAOfficialValidator(str(tmp_path))
        assert v.validate_all() is False
        assert any("configuration.yaml not found" in e for e in v.errors)

    def test_delegates_to_check_config(self, config_dir, validator):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Configuration check successful!"
        mock_result.stderr = ""

        with patch(
            "tools.validators.ha_official.subprocess.run",
            return_value=mock_result,
        ):
            assert validator.validate_all() is True


def test_non_subprocess_error_propagates(monkeypatch, tmp_path):
    (tmp_path / "configuration.yaml").write_text("default_config:")
    v = HAOfficialValidator(str(tmp_path))

    def boom(*a, **k):
        raise ValueError("unexpected logic bug")

    monkeypatch.setattr("tools.validators.ha_official.subprocess.run", boom)
    with pytest.raises(ValueError):
        v.validate_all()


class TestTimeoutConfiguration:
    def test_uses_timeout_from_env(self, config_dir, monkeypatch):
        monkeypatch.setenv("HA_VALIDATION_TIMEOUT", "240")
        v = HAOfficialValidator(str(config_dir))
        assert v.validation_timeout == 240

    def test_invalid_timeout_warns_and_falls_back(self, config_dir, monkeypatch):
        monkeypatch.setenv("HA_VALIDATION_TIMEOUT", "bad")
        v = HAOfficialValidator(str(config_dir))
        assert v.validation_timeout == 120
        assert any("HA_VALIDATION_TIMEOUT" in warning for warning in v.warnings)


class TestBenignPackageMarkers:
    """Pin the two hoisted constant tuples — they serve different policies."""

    def test_install_markers_are_strict_subset_of_ignorable(self):
        from tools.validators.ha_official import (
            _BENIGN_PACKAGE_INSTALL_MARKERS,
            _IGNORABLE_STDOUT_SUBSTRINGS,
        )

        ignorable_lower = {s.lower() for s in _IGNORABLE_STDOUT_SUBSTRINGS}
        for m in _BENIGN_PACKAGE_INSTALL_MARKERS:
            assert m.lower() in ignorable_lower, (
                f"{m!r} missing from _IGNORABLE_STDOUT_SUBSTRINGS"
            )

    def test_install_markers_count_matches_ha_known_set(self):
        from tools.validators.ha_official import _BENIGN_PACKAGE_INSTALL_MARKERS

        assert len(_BENIGN_PACKAGE_INSTALL_MARKERS) == 4


class TestClassifyStdoutLine:
    """Direct unit tests for the extracted _classify_stdout_line helper."""

    def test_zero_errors_count_goes_to_info(self, validator):
        v = validator
        v._classify_stdout_line("Found 0 errors")
        assert v.info and not v.errors

    def test_nonzero_errors_count_goes_to_errors(self, validator):
        v = validator
        v._classify_stdout_line("Found 2 errors")
        assert v.errors and not v.info

    def test_error_prefixed_line_goes_to_errors(self, validator):
        v = validator
        v._classify_stdout_line("ERROR: bad config")
        assert v.errors

    def test_warning_prefixed_line_goes_to_warnings(self, validator):
        v = validator
        v._classify_stdout_line("WARNING: deprecated")
        assert v.warnings

    def test_successful_check_goes_to_info(self, validator):
        v = validator
        v._classify_stdout_line("Configuration check successful!")
        assert v.info

    def test_info_prefixed_line_dropped(self, validator):
        v = validator
        v._classify_stdout_line("INFO: something")
        assert not v.info and not v.errors and not v.warnings


class TestParseStderr:
    """Direct unit tests for the extracted _parse_stderr helper."""

    def test_error_indicator_line_routed_to_errors(self, validator):
        v = validator
        v._parse_stderr("error: missing field")
        assert any("missing field" in e for e in v.errors)

    def test_benign_debug_line_suppressed(self, validator):
        v = validator
        v._parse_stderr("DEBUG: verbose noise")
        assert not v.errors

    def test_unknown_line_treated_as_error(self, validator):
        v = validator
        v._parse_stderr("something unusual happened")
        assert v.errors
