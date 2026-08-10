"""Tests for tools/reload_config.py - HA config reload via API."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tools.common import HARequestError
from tools.reload_config import (
    CORE_RELOAD_SERVICE,
    FILE_TO_SERVICE,
    FULL_RELOAD_SERVICE,
    SERVICE_LABELS,
    detect_changed_services,
    reload_config,
    reload_service,
)


def _diff_only(stdout):
    """Return side_effect list: diff stdout is NUL-delimited, status is empty."""
    return [
        MagicMock(returncode=0, stdout=stdout),
        MagicMock(returncode=0, stdout=""),
    ]


def _nul(s: str) -> str:
    """Replace \\n with \\0 for NUL-delimited mock output."""
    return s.replace("\n", "\0")


def _make_client():
    """Return a mock HAClient with post stubbed to return success by default."""
    client = MagicMock()
    client.post.return_value = MagicMock(status_code=200)
    client.timeout = 30
    return client


class TestDetectChangedServices:
    def test_core_service_constant_is_used_for_mapping_and_label(self):
        assert FILE_TO_SERVICE["configuration.yaml"] == FULL_RELOAD_SERVICE
        assert SERVICE_LABELS[FULL_RELOAD_SERVICE] == "all YAML config"
        assert SERVICE_LABELS[CORE_RELOAD_SERVICE] == "core config"

    def test_automations_yaml_returns_automation_reload(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = _diff_only(_nul("config/automations.yaml\n"))
            result = detect_changed_services()
        assert result == {"automation/reload"}

    def test_scripts_yaml_returns_script_reload(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = _diff_only(_nul("config/scripts.yaml\n"))
            result = detect_changed_services()
        assert result == {"script/reload"}

    def test_scenes_yaml_returns_scene_reload(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = _diff_only(_nul("config/scenes.yaml\n"))
            result = detect_changed_services()
        assert result == {"scene/reload"}

    def test_configuration_yaml_returns_reload_all(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = _diff_only(_nul("config/configuration.yaml\n"))
            result = detect_changed_services()
        assert result == {FULL_RELOAD_SERVICE}

    def test_unknown_yaml_returns_reload_all(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = _diff_only(_nul("config/secrets.yaml\n"))
            result = detect_changed_services()
        assert result == {FULL_RELOAD_SERVICE}

    def test_full_reload_short_circuits_domain_reloads(self):
        client = _make_client()
        from tools.reload_config import _execute_reload_plan

        results = _execute_reload_plan(
            client, {FULL_RELOAD_SERVICE, "automation/reload"}
        )

        assert results == [(FULL_RELOAD_SERVICE, True, None)]
        client.post.assert_called_once_with(
            "/api/services/homeassistant/reload_all", json={}
        )

    def test_subdir_file_not_included(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = _diff_only(_nul("config/blueprints/foo.yaml\n"))
            result = detect_changed_services()
        assert result == set()

    def test_multiple_files_returns_multiple_services(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = _diff_only(
                _nul("config/automations.yaml\nconfig/scripts.yaml\n")
            )
            result = detect_changed_services()
        assert result == {"automation/reload", "script/reload"}

    def test_no_changed_files_returns_empty_set(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = _diff_only("")
            result = detect_changed_services()
        assert result == set()

    def test_git_nonzero_returns_none(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = detect_changed_services()
        assert result is None

    def test_git_not_found_returns_none(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            result = detect_changed_services()
        assert result is None

    def test_status_short_picks_up_untracked_files(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=""),
                MagicMock(returncode=0, stdout="?? config/automations.yaml\0"),
            ]
            result = detect_changed_services()
        assert result == {"automation/reload"}

    def test_timeout_on_diff_returns_none(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)
            result = detect_changed_services()
        assert result is None

    def test_timeout_on_status_returns_diff_result(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="config/automations.yaml\0"),
                subprocess.TimeoutExpired(cmd="git", timeout=10),
            ]
            result = detect_changed_services()
        assert result == {"automation/reload"}

    def test_status_handles_rename(self):
        """Rename status emits two NUL-delimited entries; old path is skipped."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=""),
                MagicMock(
                    returncode=0, stdout="R  config/automations.yaml\0config/old.yaml\0"
                ),
            ]
            result = detect_changed_services()
        assert result == {"automation/reload"}


class TestReloadService:
    """M16: reload_service now uses client.post directly (not call_service)."""

    def test_success_returns_service_and_true(self):
        client = _make_client()
        client.post.return_value = MagicMock(status_code=200)
        svc, ok, err = reload_service(client, "automation/reload")
        assert svc == "automation/reload"
        assert ok is True
        assert err is None

    def test_failure_returns_service_and_false_with_detail(self):
        client = _make_client()
        resp = MagicMock(status_code=500, text="Internal Server Error: bad config")
        client.post.return_value = resp
        svc, ok, err = reload_service(client, "automation/reload")
        assert svc == "automation/reload"
        assert ok is False
        assert err is not None and "500" in err and "Internal Server Error" in err

    def test_post_invoked_with_correct_path(self):
        client = _make_client()
        client.post.return_value = MagicMock(status_code=200)
        reload_service(client, "automation/reload")
        client.post.assert_called_once_with("/api/services/automation/reload", json={})

    def test_core_config_service_dispatches_correctly(self):
        client = _make_client()
        client.post.return_value = MagicMock(status_code=200)
        reload_service(client, "homeassistant/reload_core_config")
        client.post.assert_called_once_with(
            "/api/services/homeassistant/reload_core_config", json={}
        )

    def test_request_error_returns_service_and_false_with_detail(self):
        """Network errors (HARequestError) propagate error detail."""
        client = _make_client()
        client.post.side_effect = HARequestError(
            "POST /api/services/automation/reload failed: timeout"
        )
        svc, ok, err = reload_service(client, "automation/reload")
        assert svc == "automation/reload"
        assert ok is False
        assert err is not None and "timeout" in err

    def test_unexpected_exception_caught(self):
        """M17: non-HARequestError exceptions in reload_service are caught."""
        client = _make_client()
        client.post.side_effect = RuntimeError("connection reset mid-read")
        svc, ok, err = reload_service(client, "automation/reload")
        assert ok is False
        assert err is not None and "RuntimeError" in err


class TestReloadConfig:
    """All tests stub HAClient.from_env so reload_config() never touches the network."""

    @pytest.fixture(autouse=True)
    def _stub_ha_client(self, monkeypatch):
        """Replace HAClient.from_env with a mock-client factory.

        Tests that need to assert on call_service behavior can read
        `mock_client.call_service` directly.
        """
        client = _make_client()

        def _factory():
            return client

        monkeypatch.setattr("tools.reload_config.HAClient.from_env", _factory)
        # Also expose the client instance for assertions.
        self._mock_client = client

    def test_success(self):
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={FULL_RELOAD_SERVICE},
        ):
            assert reload_config() is True

    def test_api_failure(self):
        self._mock_client.post.return_value = MagicMock(status_code=500)
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={FULL_RELOAD_SERVICE},
        ):
            assert reload_config() is False

    def test_from_env_raises_returns_false(self, capsys):
        """If HAClient.from_env raises HARequestError, reload_config returns False."""
        with (
            patch(
                "tools.reload_config.HAClient.from_env",
                side_effect=HARequestError("HA_URL must start with http://"),
            ),
            patch(
                "tools.reload_config.detect_changed_services",
                return_value={"automation/reload"},
            ),
        ):
            assert reload_config() is False
        out, err = capsys.readouterr()
        assert "HA_URL must start with" in err

    def test_from_env_token_error_includes_hint(self, capsys):
        """Token-related errors print the 'create a token' hint."""
        from tools.common import MissingTokenError

        with (
            patch(
                "tools.reload_config.HAClient.from_env",
                side_effect=MissingTokenError("HA_TOKEN not found"),
            ),
            patch(
                "tools.reload_config.detect_changed_services",
                return_value={"automation/reload"},
            ),
        ):
            assert reload_config() is False
        out, err = capsys.readouterr()
        assert "long_lived_access_token" in err

    def test_detect_returns_none_reloads_all_services(self):
        ok_resp = MagicMock(status_code=200)
        self._mock_client.post.return_value = ok_resp
        with patch("tools.reload_config.detect_changed_services", return_value=None):
            result = reload_config()
        assert result is True
        assert self._mock_client.post.call_count == 1
        self._mock_client.post.assert_called_once_with(
            "/api/services/homeassistant/reload_all", json={}
        )

    def test_detect_returns_empty_set_reloads_all_services(self):
        ok_resp = MagicMock(status_code=200)
        self._mock_client.post.return_value = ok_resp
        with patch("tools.reload_config.detect_changed_services", return_value=set()):
            result = reload_config()
        assert result is True
        assert self._mock_client.post.call_count == 1
        self._mock_client.post.assert_called_once_with(
            "/api/services/homeassistant/reload_all", json={}
        )

    def test_detect_returns_one_service_makes_one_call(self):
        self._mock_client.post.return_value = MagicMock(status_code=200)
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"automation/reload"},
        ):
            result = reload_config()
        assert result is True
        assert self._mock_client.post.call_count == 1

    def test_one_service_fails_returns_false(self):
        ok = MagicMock(status_code=200)
        fail = MagicMock(status_code=500)
        self._mock_client.post.side_effect = [ok, fail]
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"automation/reload", "script/reload"},
        ):
            result = reload_config()
        assert result is False

    def test_prints_reloading_header(self, capsys):
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"automation/reload"},
        ):
            reload_config()
        out, err = capsys.readouterr()
        assert "🔄 Reloading: automations" in err

    def test_prints_success_per_service(self, capsys):
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"automation/reload"},
        ):
            reload_config()
        out, err = capsys.readouterr()
        assert "✅ automations reloaded" in err

    def test_prints_failure_per_service(self, capsys):
        self._mock_client.post.return_value = MagicMock(status_code=500)
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"script/reload"},
        ):
            reload_config()
        out, err = capsys.readouterr()
        assert "❌ scripts failed to reload" in err

    def test_core_config_reloads_before_domain_services(self):
        """reload_core_config must run before any domain services in the same call."""
        call_order = []
        ok_resp = MagicMock(status_code=200)

        def _track_post(path, **kwargs):
            # Path is like "/api/services/automation/reload"
            svc = path.removeprefix("/api/services/")
            call_order.append(svc)
            return ok_resp

        self._mock_client.post.side_effect = _track_post
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"homeassistant/reload_core_config", "automation/reload"},
        ):
            result = reload_config()

        assert result is True
        assert call_order[0] == "homeassistant/reload_core_config"
        assert "automation/reload" in call_order[1:]

    def test_execute_reload_plan_returns_core_then_sorted_domains(self):
        from tools.reload_config import _execute_reload_plan

        client = _make_client()
        client.post.return_value = MagicMock(status_code=200)
        results = _execute_reload_plan(
            client,
            {
                "script/reload",
                "homeassistant/reload_core_config",
                "automation/reload",
            },
        )
        assert [service for service, _ok, _error in results] == [
            "homeassistant/reload_core_config",
            "automation/reload",
            "script/reload",
        ]

    def test_reload_timeout_overrides_client_timeout(self):
        """Reload-specific timeout (HA_RELOAD_TIMEOUT) overrides the client default."""
        with (
            patch(
                "tools.reload_config.detect_changed_services",
                return_value={"automation/reload"},
            ),
            patch.dict("os.environ", {"HA_RELOAD_TIMEOUT": "60"}),
        ):
            reload_config()
        assert self._mock_client.timeout == 60

    def test_summary_mode_compact_success(self, capsys):
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"automation/reload", "script/reload"},
        ):
            result = reload_config(summary=True)
        assert result is True
        out = capsys.readouterr().out.strip()
        assert out.startswith("RELOADED 2/2")

    def test_summary_mode_compact_failure(self, capsys):
        ok = MagicMock(status_code=200)
        fail_resp = MagicMock(status_code=500)
        self._mock_client.post.side_effect = [ok, fail_resp]
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"automation/reload", "script/reload"},
        ):
            result = reload_config(summary=True)
        assert result is False
        out = capsys.readouterr().out.strip()
        assert out.startswith("FAILED 1/2")

    def test_summary_mode_no_emoji_per_service(self, capsys):
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"automation/reload"},
        ):
            reload_config(summary=True)
        out = capsys.readouterr().out
        assert "\u2705" not in out
        assert "\u274c" not in out
        assert "\U0001f504" not in out

    def test_summary_mode_fatal_error_still_prints(self, capsys):
        with (
            patch(
                "tools.reload_config.HAClient.from_env",
                side_effect=HARequestError("HA_TOKEN not found"),
            ),
            patch(
                "tools.reload_config.detect_changed_services",
                return_value={"automation/reload"},
            ),
        ):
            result = reload_config(summary=True)
        assert result is False
        out, err = capsys.readouterr()
        assert "HA_TOKEN" in err or "Error" in err

    def test_summary_mode_timeout_warnings_suppressed(self, capsys, monkeypatch):
        """Timeout/env warnings should not print in summary mode."""
        monkeypatch.setenv("HA_GIT_TIMEOUT", "not_a_number")
        monkeypatch.setenv("HA_RELOAD_TIMEOUT", "also_bad")
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"automation/reload"},
        ):
            reload_config(summary=True)
        out = capsys.readouterr().out
        assert "must be an integer" not in out


class TestM15CoreFailureSkipsDomain:
    """M15: if core config reload fails, domain reloads must be skipped."""

    def test_core_config_failure_skips_domain_reloads(self):
        from unittest.mock import patch

        client = _make_client()
        client.post.return_value = MagicMock(status_code=500)

        def _factory():
            return client

        with (
            patch("tools.reload_config.HAClient.from_env", _factory),
            patch(
                "tools.reload_config.detect_changed_services",
                return_value={
                    "homeassistant/reload_core_config",
                    "automation/reload",
                    "script/reload",
                },
            ),
        ):
            ok = reload_config(summary=True)
        assert ok is False
        # Only the core reload should have been attempted.
        actual_calls = [c[0][0] for c in client.post.call_args_list]
        assert len(actual_calls) == 1
        assert "reload_core_config" in actual_calls[0]


class TestL68DeterministicOrder:
    """L68: verbose per-service output is deterministically ordered."""

    def test_verbose_output_order_is_deterministic(self, monkeypatch, capsys):
        """L68: output across multiple services must be in sorted order."""
        from unittest.mock import MagicMock, patch

        from tools.reload_config import reload_config

        client = MagicMock()
        client.post.return_value = MagicMock(status_code=200)
        client.timeout = 30

        def _factory():
            return client

        monkeypatch.setattr("tools.reload_config.HAClient.from_env", _factory)
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"script/reload", "automation/reload"},
        ):
            ok = reload_config()
        assert ok is True
        _, err = capsys.readouterr()
        # The "Reloading:" line should list services in sorted order
        reloading_line = [ln for ln in err.split("\n") if "Reloading:" in ln]
        assert reloading_line
        # Both services must be listed in alphabetical order
        assert "automations" in reloading_line[0]
        assert "scripts" in reloading_line[0]
        automation_pos = reloading_line[0].index("automations")
        script_pos = reloading_line[0].index("scripts")
        assert automation_pos < script_pos


class TestL70ErrorDetail:
    """L70: error-detail propagates through reload_config."""

    def test_error_detail_propagates_through_reload_config(self, monkeypatch, capsys):
        """L70: a non-2xx reload response must surface response.text in the summary."""
        from unittest.mock import MagicMock, patch

        from tools.reload_config import reload_config

        client = MagicMock()
        client.timeout = 30

        resp = MagicMock(status_code=500, text="Bad config syntax")
        client.post.return_value = resp

        def _factory():
            return client

        monkeypatch.setattr("tools.reload_config.HAClient.from_env", _factory)
        with patch(
            "tools.reload_config.detect_changed_services",
            return_value={"automation/reload"},
        ):
            ok = reload_config()
        assert ok is False
        _, err = capsys.readouterr()
        assert "Bad config syntax" in err


class TestClassifyChangedFiles:
    """Direct unit tests for _classify_changed_files (pure logic, no git)."""

    def test_automations_yaml_maps_to_automation_reload(self):
        from tools.reload_config import _classify_changed_files

        assert _classify_changed_files({"automations.yaml"}) == {"automation/reload"}

    def test_unknown_yaml_falls_back_to_reload_all(self):
        from tools.reload_config import _classify_changed_files

        assert _classify_changed_files({"unknown.yaml"}) == {FULL_RELOAD_SERVICE}

    def test_non_yaml_file_ignored(self):
        from tools.reload_config import _classify_changed_files

        assert _classify_changed_files({"readme.md", "image.png"}) == set()

    def test_mixed_set_returns_union(self):
        from tools.reload_config import _classify_changed_files

        result = _classify_changed_files(
            {
                "automations.yaml",
                "scripts.yaml",
                "notes.txt",
            }
        )
        assert result == {"automation/reload", "script/reload"}

    def test_empty_set_returns_empty(self):
        from tools.reload_config import _classify_changed_files

        assert _classify_changed_files(set()) == set()


class TestRunGitDiff:
    """Direct unit tests for _run_git_diff."""

    def test_top_level_config_basename_handles_nested_and_custom_dirs(self):
        from tools.reload_config import _top_level_config_basename

        assert _top_level_config_basename("config/automations.yaml", "config") == (
            "automations.yaml"
        )
        assert (
            _top_level_config_basename("config/blueprints/foo.yaml", "config") is None
        )
        assert (
            _top_level_config_basename("custom/ha/configuration.yaml", "custom/ha")
            == "configuration.yaml"
        )

    def test_returns_basenames_for_diff_paths(self):
        from unittest.mock import MagicMock, patch

        from tools.reload_config import _run_git_diff

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="config/automations.yaml\0config/scripts.yaml\0",
            )
            result = _run_git_diff("config", git_timeout=10)
        assert result == {"automations.yaml", "scripts.yaml"}

    def test_subdir_file_filtered_out(self):
        from unittest.mock import MagicMock, patch

        from tools.reload_config import _run_git_diff

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="config/blueprints/foo.yaml\0",
            )
            assert _run_git_diff("config", git_timeout=10) == set()

    def test_git_nonzero_returns_none(self):
        from unittest.mock import MagicMock, patch

        from tools.reload_config import _run_git_diff

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert _run_git_diff("config", git_timeout=10) is None


class TestRunGitStatusUntracked:
    """Direct unit tests for _run_git_status_untracked."""

    def test_untracked_yaml_picked_up(self):
        from unittest.mock import MagicMock, patch

        from tools.reload_config import _run_git_status_untracked

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="?? config/automations.yaml\0",
            )
            result = _run_git_status_untracked("config", git_timeout=10)
        assert result == {"automations.yaml"}

    def test_git_failure_returns_empty_set(self):
        from unittest.mock import patch

        from tools.reload_config import _run_git_status_untracked

        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _run_git_status_untracked("config", git_timeout=10) == set()


class TestMissingTokenHelpHint:
    """Pin the help-hint behavior now that it's type-driven (not string-matched)."""

    def test_missing_token_prints_help_hint(self, capsys, monkeypatch):
        from tools import reload_config
        from tools.common import MissingTokenError

        def _raise():
            raise MissingTokenError("HA_TOKEN not found")

        monkeypatch.setattr("tools.reload_config.HAClient.from_env", _raise)
        monkeypatch.setattr(
            "tools.reload_config.detect_changed_services", lambda **kw: set()
        )
        result = reload_config.reload_config(summary=False)
        captured = capsys.readouterr()
        assert "Create a .env file" in captured.err
        assert "HA_TOKEN=your_long_lived_access_token" in captured.err
        assert result is False

    def test_other_harequest_error_does_not_print_help_hint(self, capsys, monkeypatch):
        from tools import reload_config
        from tools.common import HARequestError

        def _raise():
            raise HARequestError("network unreachable")

        monkeypatch.setattr("tools.reload_config.HAClient.from_env", _raise)
        monkeypatch.setattr(
            "tools.reload_config.detect_changed_services", lambda **kw: set()
        )
        result = reload_config.reload_config(summary=False)
        captured = capsys.readouterr()
        assert "Create a .env file" not in captured.err
        assert "network unreachable" in captured.err
        assert result is False
