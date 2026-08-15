"""Unit tests for stale_sensors.py — Stale Sensor Diagnostic Scanner."""

import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tests.helpers import assert_diagnostic, assert_no_diagnostic, mock_json_client
from tools.common import HARequestError
from tools.validators.stale_sensors import StaleSensorValidator, main


@pytest.fixture(autouse=True)
def setup_env():
    """Set up default environment variables for testing."""
    env = {
        "HA_URL": "http://localhost:8123",
        "HA_TOKEN": "mock_token",
    }
    if "CI" in os.environ:
        env["CI"] = "false"
    if "HA_STALE_FAIL" in os.environ:
        env["HA_STALE_FAIL"] = "false"
    if "HA_STALE_TIMEOUT" in os.environ:
        env["HA_STALE_TIMEOUT"] = "2"
    with patch.dict("os.environ", env):
        yield


@pytest.fixture
def config_dir(tmp_path):
    """Temporary configuration directory containing mock storage registry."""
    (tmp_path / ".storage").mkdir()
    return tmp_path


def _write_entity_registry(config_dir, entities_list):
    """Helper to write mock core.entity_registry."""
    registry_file = config_dir / ".storage" / "core.entity_registry"
    data = {
        "version": 1,
        "minor_version": 1,
        "key": "core.entity_registry",
        "data": {"entities": entities_list},
    }
    with open(registry_file, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _mock_states(states_list) -> MagicMock:
    """Helper to mock HA API client response."""
    return mock_json_client(states_list)


def _run_stale_validation(
    config_dir, registry_entries, states, current_time, **validator_options
):
    """Write fixtures, run validation with a mocked client and frozen time."""
    _write_entity_registry(config_dir, registry_entries)
    mock_client = _mock_states(states)
    with (
        patch("tools.validators.stale_sensors.HAClient", return_value=mock_client),
        patch.object(
            StaleSensorValidator, "_get_current_time", return_value=current_time
        ),
    ):
        validator = StaleSensorValidator(str(config_dir), **validator_options)
        result = validator.validate_all()
    return validator, result


def _mock_offline() -> MagicMock:
    """Helper to mock unreachable HA API."""
    return mock_json_client(side_effect=HARequestError("API Connection Timeout"))


def test_file_deps_empty():
    """StaleSensorValidator should return empty file dependencies to bypass caching."""
    v = StaleSensorValidator()
    assert v.file_deps() == []


class TestExcludeDomains:
    def test_init_accepts_sequence_domains(self):
        v = StaleSensorValidator(
            only_domains=["sensor", "binary_sensor"],
            exclude_domains=["weather"],
        )
        assert v.only_domains == {"sensor", "binary_sensor"}

    def test_default_excluded_platform_policy_is_not_shared(self, tmp_path):
        expected = {
            "template",
            "group",
            "derivative",
            "utility_meter",
            "min_max",
            "threshold",
            "integration",
            "history_stats",
            "filter",
        }
        first = StaleSensorValidator(config_dir=str(tmp_path))
        second = StaleSensorValidator(config_dir=str(tmp_path))

        assert first.exclude_platforms == expected
        first.exclude_platforms.remove("template")
        assert second.exclude_platforms == expected

    def test_exclude_subtracts_from_only_domains(self, tmp_path):
        v = StaleSensorValidator(
            config_dir=str(tmp_path),
            only_domains={"sensor", "binary_sensor"},
            exclude_domains={"sensor"},
        )
        assert v.only_domains == {"binary_sensor"}

    def test_exclude_default_empty(self, tmp_path):
        assert StaleSensorValidator(config_dir=str(tmp_path)).only_domains == {"sensor"}

    def test_exclude_empty_set_no_op(self, tmp_path):
        v = StaleSensorValidator(
            config_dir=str(tmp_path), only_domains={"sensor"}, exclude_domains=set()
        )
        assert v.only_domains == {"sensor"}

    def test_exclude_all_yields_empty_set(self, tmp_path):
        v = StaleSensorValidator(
            config_dir=str(tmp_path),
            only_domains={"sensor"},
            exclude_domains={"sensor"},
        )
        assert v.only_domains == set()

    def test_new_keyword_does_not_shift_existing_positional_args(self, tmp_path):
        v = StaleSensorValidator(
            str(tmp_path),
            False,
            False,
            24,
            {"sensor"},
            {"template"},
            True,
            True,
            exclude_domains={"sensor"},
        )
        assert v.exclude_platforms == {"template"}
        assert v.ignore_restored is True
        assert v.fail_on_stale is True


def test_api_offline_degrades_gracefully(config_dir):
    """If the HA API is offline, the validator logs info and returns True."""
    mock_client = _mock_offline()
    with patch("tools.validators.stale_sensors.HAClient", return_value=mock_client):
        v = StaleSensorValidator(str(config_dir))
        assert v.validate_all() is True
        assert_diagnostic(v, "info", "skipped")


def test_live_fetch_closes_client_on_success(config_dir):
    client = _mock_states([])

    with patch("tools.validators.stale_sensors.HAClient", return_value=client):
        validator = StaleSensorValidator(str(config_dir))
        assert validator._fetch_live_states() == []

    client.close.assert_called_once_with()


def test_live_fetch_closes_client_on_api_error(config_dir):
    client = _mock_offline()

    with patch("tools.validators.stale_sensors.HAClient", return_value=client):
        validator = StaleSensorValidator(str(config_dir))
        assert validator._fetch_live_states() is None

    client.close.assert_called_once_with()


def test_malformed_state_shapes_are_skipped(config_dir):
    validator = StaleSensorValidator(str(config_dir))

    stale, warnings = validator._scan_states(
        [
            {"entity_id": 123, "state": "on"},
            {
                "entity_id": "sensor.bad_attributes",
                "state": "on",
                "attributes": [],
                "last_changed": "2020-01-01T00:00:00+00:00",
                "last_updated": "2020-01-01T00:00:00+00:00",
            },
        ],
        None,
        datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
    )

    assert stale == []
    assert warnings == []


def test_oserror_during_states_fetch_degrades(config_dir):
    """OSError during HAClient construction or states fetch must degrade."""
    with patch(
        "tools.validators.stale_sensors.HAClient",
        side_effect=OSError("socket error"),
    ):
        v = StaleSensorValidator(str(config_dir))
        assert v.validate_all() is True
        assert_diagnostic(v, "info", "skipped")


def test_stale_sensor_detected(config_dir):
    """Active sensor that has not updated for > 24 hours triggers warning."""
    validator, result = _run_stale_validation(
        config_dir,
        [
            {
                "entity_id": "sensor.test_temp",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
        [
            {
                "entity_id": "sensor.test_temp",
                "state": "21.5",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {},
            }
        ],
        datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
    )
    assert result is True
    assert_diagnostic(validator, "warnings", "sensor.test_temp")
    assert_diagnostic(validator, "warnings", "stale")


def test_healthy_sensor_ignored(config_dir):
    """Active sensor that updated recently is ignored."""
    validator, result = _run_stale_validation(
        config_dir,
        [
            {
                "entity_id": "sensor.test_temp",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
        [
            {
                "entity_id": "sensor.test_temp",
                "state": "21.5",
                "last_updated": "2026-06-25T20:00:00+00:00",
                "attributes": {},
            }
        ],
        datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
    )
    assert result is True
    assert_no_diagnostic(validator, "warnings")


def test_unavailable_state_flagged_immediately(config_dir):
    """A sensor in unavailable/unknown state must surface at once,
    not wait for threshold_hours to elapse."""
    validator, _result = _run_stale_validation(
        config_dir,
        [
            {
                "entity_id": "sensor.dead_battery",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
        [
            {
                "entity_id": "sensor.dead_battery",
                "state": "unavailable",
                "last_updated": "2026-06-25T21:00:00+00:00",
                "attributes": {},
            }
        ],
        datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
    )
    v = validator
    assert_diagnostic(v, "warnings", "sensor.dead_battery")
    assert any(
        text in warning.lower()
        for text in ("unavailable", "unknown", "not reporting")
        for warning in v.warnings
    ), "unavailable state must surface immediately"
    assert "sensor.dead_battery" in v.stale_entities


def test_parse_timestamp_handles_positive_offset_aest():
    """A +10:00 (AEST) offset is parsed and normalised to UTC
    correctly so elapsed-hours math is right."""
    v = StaleSensorValidator()
    parsed = v.parse_timestamp("2026-07-17T08:00:00+10:00")
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=10)
    now_utc = datetime(2026, 7, 16, 22, 0, 0, tzinfo=UTC)
    delta_hours = (now_utc - parsed).total_seconds() / 3600.0
    assert delta_hours == 0.0


def test_disabled_or_hidden_sensor_ignored(config_dir):
    """Disabled or hidden entities are excluded from staleness checks."""
    validator, result = _run_stale_validation(
        config_dir,
        [
            {
                "entity_id": "sensor.disabled_temp",
                "platform": "zha",
                "disabled_by": "user",
                "hidden_by": None,
            },
            {
                "entity_id": "sensor.hidden_temp",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": "user",
            },
        ],
        [
            {
                "entity_id": "sensor.disabled_temp",
                "state": "21.5",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {},
            },
            {
                "entity_id": "sensor.hidden_temp",
                "state": "22.0",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {},
            },
        ],
        datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
    )
    assert result is True
    assert_no_diagnostic(validator, "warnings")


def test_virtual_platform_ignored(config_dir):
    """Template/group/utility_meter platform entities are ignored."""
    validator, result = _run_stale_validation(
        config_dir,
        [
            {
                "entity_id": "sensor.template_temp",
                "platform": "template",
                "disabled_by": None,
                "hidden_by": None,
            },
            {
                "entity_id": "sensor.group_temp",
                "platform": "group",
                "disabled_by": None,
                "hidden_by": None,
            },
        ],
        [
            {
                "entity_id": "sensor.template_temp",
                "state": "21.5",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {},
            },
            {
                "entity_id": "sensor.group_temp",
                "state": "22.0",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {},
            },
        ],
        datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
    )
    assert result is True
    assert_no_diagnostic(validator, "warnings")


def test_restored_entity_flagged(config_dir):
    """Restored entities are flagged as stale immediately."""
    validator, result = _run_stale_validation(
        config_dir,
        [
            {
                "entity_id": "sensor.restored_temp",
                "platform": "mqtt",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
        [
            {
                "entity_id": "sensor.restored_temp",
                "state": "21.5",
                "last_updated": "2026-06-25T20:55:00+00:00",
                "attributes": {"restored": True},
            }
        ],
        datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
    )
    assert result is True
    assert_diagnostic(validator, "warnings", "sensor.restored_temp")
    assert_diagnostic(validator, "warnings", "restored")


def test_custom_heartbeat_timestamp(config_dir):
    """Zigbee last_seen attribute is checked if present, catching hidden staleness."""
    validator, result = _run_stale_validation(
        config_dir,
        [
            {
                "entity_id": "sensor.z2m_temp",
                "platform": "mqtt",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
        [
            {
                "entity_id": "sensor.z2m_temp",
                "state": "21.5",
                "last_updated": "2026-06-25T20:55:00+00:00",
                "attributes": {"last_seen": "2026-06-24T20:00:00Z"},
            }
        ],
        datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
    )
    assert result is True
    assert_diagnostic(validator, "warnings", "sensor.z2m_temp")
    assert_diagnostic(validator, "warnings", "stale")


def test_numeric_heartbeat_timestamp(config_dir):
    """Validator parses integer/float epochs in heartbeats."""
    validator, result = _run_stale_validation(
        config_dir,
        [
            {
                "entity_id": "sensor.numeric_temp",
                "platform": "mqtt",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
        [
            {
                "entity_id": "sensor.numeric_temp",
                "state": "21.5",
                "last_updated": "2026-06-25T20:55:00+00:00",
                "attributes": {"last_reported": 1782331200},  # epoch seconds
            }
        ],
        datetime.fromtimestamp(1782421200, tz=UTC),
    )
    assert result is True
    assert_diagnostic(validator, "warnings", "sensor.numeric_temp")
    assert_diagnostic(validator, "warnings", "stale")


@pytest.mark.parametrize("ci_value", ["true", "TRUE", "1"])
def test_ci_environment_skips_validation(config_dir, ci_value):
    """Skipping checking and returning True in CI environment to avoid hangs."""
    mock_client = _mock_states([])
    with (
        patch("tools.validators.stale_sensors.HAClient", return_value=mock_client),
        patch("os.getenv", side_effect=lambda k, d=None: ci_value if k == "CI" else d),
    ):
        v = StaleSensorValidator(str(config_dir))
        assert v.validate_all() is True
        assert_diagnostic(v, "info", "CI environment")


def test_registry_absent_falls_back_gracefully(config_dir):
    """When the registry file is missing/empty, validation degrades gracefully
    and emits an info-level note."""
    # We do NOT write core.entity_registry to config_dir, so it's missing.
    mock_client = _mock_states(
        [
            {
                "entity_id": "sensor.unknown_temp",
                "state": "21.5",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {},
            }
        ]
    )
    with patch("tools.validators.stale_sensors.HAClient", return_value=mock_client):
        v = StaleSensorValidator(str(config_dir))
        v._get_current_time = MagicMock(
            return_value=datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC)
        )
        assert v.validate_all() is True
    assert_diagnostic(v, "warnings", "sensor.unknown_temp")
    assert_diagnostic(v, "warnings", "stale")
    assert_diagnostic(v, "info", "registry")


def test_retry_on_registry_read_failure(config_dir):
    """Validator retries once if registry read fails with JSONDecodeError."""
    # We will write invalid json, and patch open or json.load to fail
    # on first call but succeed on second retry.
    _write_entity_registry(
        config_dir,
        [
            {
                "entity_id": "sensor.test_temp",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
    )

    # Let's mock open/json.load behavior.
    mock_client = _mock_states(
        [
            {
                "entity_id": "sensor.test_temp",
                "state": "21.5",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {},
            }
        ]
    )

    call_count = 0
    original_json_load = json.load

    def mock_json_load(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise json.JSONDecodeError("Expecting value", "", 0)
        return original_json_load(*args, **kwargs)

    with (
        patch("tools.validators.stale_sensors.HAClient", return_value=mock_client),
        patch("json.load", side_effect=mock_json_load),
        patch("time.sleep") as mock_sleep,
    ):
        v = StaleSensorValidator(str(config_dir))
        v._get_current_time = MagicMock(
            return_value=datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC)
        )
        assert v.validate_all() is True
        assert_diagnostic(v, "warnings", "sensor.test_temp")
        assert call_count == 2
        mock_sleep.assert_called_once_with(0.1)


def test_second_registry_decode_failure_warns_without_extra_retry(config_dir):
    """A second JSON decode failure warns after exactly one bounded retry."""
    _write_entity_registry(config_dir, [])
    v = StaleSensorValidator(str(config_dir))
    decode_error = json.JSONDecodeError("still invalid", "", 0)

    with (
        patch(
            "tools.validators.stale_sensors.load_storage_registry",
            side_effect=decode_error,
        ) as load_registry,
        patch("time.sleep") as mock_sleep,
    ):
        assert v._load_registry() is None

    assert load_registry.call_count == 2
    mock_sleep.assert_called_once_with(0.1)
    assert_diagnostic(v, "warnings", "Failed to read entity registry: still invalid")


@pytest.mark.parametrize(
    "error",
    [
        OSError("permanent read failure"),
        KeyError("missing field"),
        TypeError("wrong shape"),
        ValueError("invalid envelope"),
        AttributeError("unexpected shape"),
    ],
    ids=["oserror", "keyerror", "typeerror", "valueerror", "attributeerror"],
)
def test_permanent_registry_failures_are_not_retried(config_dir, error):
    """Only transient JSON decoding should incur the retry delay."""
    _write_entity_registry(config_dir, [])
    v = StaleSensorValidator(str(config_dir))

    with (
        patch(
            "tools.validators.stale_sensors.load_storage_registry",
            side_effect=error,
        ) as load_registry,
        patch("time.sleep") as mock_sleep,
    ):
        assert v._load_registry() is None

    load_registry.assert_called_once()
    mock_sleep.assert_not_called()
    assert_diagnostic(v, "warnings", "Falling back to state-only analysis")


def test_live_api_failure_does_not_load_registry(config_dir):
    """Live-fetch failure returns before local registry work begins."""
    client = _mock_offline()
    with (
        patch("tools.validators.stale_sensors.HAClient", return_value=client),
        patch.object(StaleSensorValidator, "_load_registry") as load_registry,
    ):
        v = StaleSensorValidator(str(config_dir))

        assert v.validate_all() is True

    load_registry.assert_not_called()


def test_unexpected_timestamp_parser_error_propagates(config_dir):
    """Unexpected timestamp parser failures must not be silently recovered."""
    _write_entity_registry(
        config_dir,
        [{"entity_id": "sensor.test_temp", "platform": "zha"}],
    )
    mock_client = _mock_states(
        [
            {
                "entity_id": "sensor.test_temp",
                "state": "21.5",
                "last_changed": "2026-06-24T15:00:00+00:00",
                "last_updated": "2026-06-24T19:00:00+00:00",
                "attributes": {},
            }
        ]
    )
    with patch("tools.validators.stale_sensors.HAClient", return_value=mock_client):
        v = StaleSensorValidator(str(config_dir))
        v._get_current_time = MagicMock(
            return_value=datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC)
        )
        v.parse_timestamp = MagicMock(
            side_effect=[
                None,
                None,
                RuntimeError("unexpected parser bug"),
                datetime.now(UTC),
            ]
        )
        with pytest.raises(RuntimeError, match="unexpected parser bug"):
            v.validate_all()


def test_ha_stale_timeout_env_overrides_default(config_dir):
    """HA_STALE_TIMEOUT env var overrides the default 2-second timeout."""
    with patch("tools.validators.stale_sensors.HAClient") as mock_class:
        inst = _mock_states([])
        mock_class.return_value = inst
        with patch.dict("os.environ", {"HA_STALE_TIMEOUT": "9"}):
            v = StaleSensorValidator(str(config_dir))
            v.validate_all()
            mock_class.assert_called_once()
            assert mock_class.call_args.kwargs["timeout"] == 9


def test_fail_on_stale_mode_returns_false_when_stale(config_dir):
    """fail_on_stale=True + stale sensors → validate_all returns False."""
    validator, result = _run_stale_validation(
        config_dir,
        [
            {
                "entity_id": "sensor.test_temp",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
        [
            {
                "entity_id": "sensor.test_temp",
                "state": "21.5",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {},
            }
        ],
        datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
        fail_on_stale=True,
    )
    assert result is False
    assert_diagnostic(validator, "errors", "failed")


def test_fail_on_stale_off_keeps_diagnostic(config_dir):
    """fail_on_stale=False (default) returns True even when stale."""
    validator, result = _run_stale_validation(
        config_dir,
        [
            {
                "entity_id": "sensor.test_temp",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
        [
            {
                "entity_id": "sensor.test_temp",
                "state": "21.5",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {},
            }
        ],
        datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
        fail_on_stale=False,
    )
    assert result is True
    assert_diagnostic(validator, "warnings", "sensor.test_temp")


def test_fail_on_stale_no_stale_returns_true(config_dir):
    """fail_on_stale=True with no stale sensors returns True."""
    validator, result = _run_stale_validation(
        config_dir,
        [
            {
                "entity_id": "sensor.test_temp",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
        [
            {
                "entity_id": "sensor.test_temp",
                "state": "21.5",
                "last_updated": "2026-06-25T20:00:00+00:00",
                "attributes": {},
            }
        ],
        datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
        fail_on_stale=True,
    )
    assert result is True
    assert_no_diagnostic(validator, "warnings")


def test_fail_on_stale_env_var_picked_up_after_load_env(config_dir, monkeypatch):
    """HA_STALE_FAIL env var is read after load_env_file() in validate_all()."""
    monkeypatch.setenv("HA_URL", "http://localhost:8123")
    monkeypatch.setenv("HA_TOKEN", "mock_token")
    _write_entity_registry(
        config_dir,
        [
            {
                "entity_id": "sensor.test_temp",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
    )
    mock_client = _mock_states(
        [
            {
                "entity_id": "sensor.test_temp",
                "state": "21.5",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {},
            }
        ]
    )
    with patch("tools.validators.stale_sensors.HAClient", return_value=mock_client):
        v = StaleSensorValidator(str(config_dir))
        monkeypatch.setenv("HA_STALE_FAIL", "true")
        v._get_current_time = MagicMock(
            return_value=datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC)
        )
        assert v.fail_on_stale is False
        assert v.validate_all() is False
        assert_diagnostic(v, "errors", "failed")


def test_repeated_validation_resets_run_state(config_dir, monkeypatch):
    """A reused validator reflects the current env and states on each run."""
    _write_entity_registry(
        config_dir,
        [
            {
                "entity_id": "sensor.test_temp",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
    )
    stale = [
        {
            "entity_id": "sensor.test_temp",
            "state": "21.5",
            "last_updated": "2026-06-24T20:00:00+00:00",
            "attributes": {},
        }
    ]
    fresh = [
        {
            "entity_id": "sensor.test_temp",
            "state": "21.5",
            "last_updated": "2026-06-25T20:00:00+00:00",
            "attributes": {},
        }
    ]
    mock_client = _mock_states([])
    mock_client.get_json.side_effect = [stale, fresh]
    with (
        patch("tools.validators.stale_sensors.HAClient", return_value=mock_client),
        patch.object(
            StaleSensorValidator,
            "_get_current_time",
            return_value=datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC),
        ),
    ):
        validator = StaleSensorValidator(str(config_dir))
        monkeypatch.setenv("HA_STALE_FAIL", "true")
        assert validator.validate_all() is False

        monkeypatch.setenv("HA_STALE_FAIL", "false")
        assert validator.validate_all() is True

    assert validator.stale_entities == []
    assert_no_diagnostic(validator, "errors")
    assert_no_diagnostic(validator, "warnings")


def test_naive_datetime_handling():
    """Validator parses naive ISO strings and treats them as UTC timezone-aware."""
    v = StaleSensorValidator()
    # Naive ISO format string (no timezone info)
    dt = v.parse_timestamp("2026-06-25T20:00:00")
    assert dt is not None
    assert dt.tzinfo == UTC
    assert dt.hour == 20


def test_boolean_handling_in_parse_timestamp():
    """Validator ignores boolean values in parse_timestamp."""
    v = StaleSensorValidator()
    assert v.parse_timestamp(True) is None
    assert v.parse_timestamp(False) is None


def test_malformed_registry_json(config_dir):
    """Validator falls back gracefully if registry JSON is malformed list."""
    registry_file = config_dir / ".storage" / "core.entity_registry"
    # Write a JSON list instead of a dict mapping
    with open(registry_file, "w", encoding="utf-8") as f:
        f.write("[]")

    mock_client = _mock_states(
        [
            {
                "entity_id": "sensor.test_temp",
                "state": "21.5",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {},
            }
        ]
    )

    with patch("tools.validators.stale_sensors.HAClient", return_value=mock_client):
        v = StaleSensorValidator(str(config_dir))
        v._get_current_time = MagicMock(
            return_value=datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC)
        )
        assert v.validate_all() is True
        # Verify the registry-load warning was logged.
        assert_diagnostic(v, "warnings", "Falling back")
        # Verify stale sensor detection still worked
        assert_diagnostic(v, "warnings", "sensor.test_temp")


def test_main_dispatch_with_ci_short_circuit(monkeypatch):
    """main() returns 0 when CI=true short-circuits stale sensor validation."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr("sys.argv", ["stale_sensors", "config"])
    assert main() == 0


# Timestamp parsing variants


@pytest.mark.parametrize(
    "value",
    [1782331200000, "1782331200", "1782331200000"],
    ids=["integer-milliseconds", "seconds-string", "milliseconds-string"],
)
def test_parse_timestamp_epoch_variants(value):
    """Equivalent epoch forms normalize to the same UTC timestamp."""
    v = StaleSensorValidator()
    result = v.parse_timestamp(value)
    assert result == datetime.fromtimestamp(1782331200, tz=UTC)


def test_parse_timestamp_malformed_string_appends_warning():
    """A non-numeric, non-ISO string appends a warning and returns None."""
    v = StaleSensorValidator()
    assert v.parse_timestamp("not a date") is None
    assert_diagnostic(v, "warnings", "Failed to parse")


def test_parse_timestamp_nonfinite_string_appends_warning():
    """A non-finite numeric string is treated as malformed input."""
    v = StaleSensorValidator()
    assert v.parse_timestamp("inf") is None
    assert_diagnostic(v, "warnings", "Failed to parse")


def test_zero_epoch_is_a_valid_baseline_timestamp():
    """A numeric epoch of zero is not mistaken for a missing timestamp."""
    v = StaleSensorValidator()
    baseline = v._baseline_timestamp({"last_changed": 0}, None)
    assert baseline == datetime.fromtimestamp(0, tz=UTC)


class TestParseIsoString:
    def test_z_suffix_normalized_to_utc(self):
        v = StaleSensorValidator()
        dt = v._parse_iso_string("2026-07-17T10:00:00Z")
        assert dt == datetime.fromisoformat("2026-07-17T10:00:00+00:00")

    def test_naive_gets_utc_attached(self):
        v = StaleSensorValidator()
        dt = v._parse_iso_string("2026-07-17T10:00:00")
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_offset_preserved(self):
        v = StaleSensorValidator()
        dt = v._parse_iso_string("2026-07-17T10:00:00+10:00")
        assert dt.utcoffset().total_seconds() == 10 * 3600

    def test_malformed_returns_none(self):
        v = StaleSensorValidator()
        assert v._parse_iso_string("not a date") is None


class TestParseEpoch:
    @pytest.mark.parametrize(
        ("value", "expected_seconds"),
        [
            (1782331200, 1782331200),
            (1782331200000, 1782331200),
            (1_000_000_000, 1_000_000_000),
        ],
        ids=["seconds", "milliseconds", "below-millisecond-threshold"],
    )
    def test_epoch_variants(self, value, expected_seconds):
        v = StaleSensorValidator()
        dt = v._parse_epoch(value)
        assert dt == datetime.fromtimestamp(expected_seconds, tz=UTC)


def test_parse_timestamp_unsupported_type_returns_none():
    """parse_timestamp returns None for list/dict values."""
    v = StaleSensorValidator()
    assert v.parse_timestamp([1, 2]) is None
    assert v.parse_timestamp({"nested": True}) is None


# Invalid HA_STALE_TIMEOUT


def test_missing_ha_url_token_skips_gracefully(config_dir):
    """When HA_URL or HA_TOKEN are not set, skip with info and return True."""
    with patch.dict("os.environ", {}, clear=True):
        v = StaleSensorValidator(str(config_dir))
        assert v.validate_all() is True
        assert_diagnostic(v, "info", "not set")


def test_invalid_ha_stale_timeout_warns(config_dir):
    """Non-integer HA_STALE_TIMEOUT logs an info warning with the default fallback."""
    with patch("tools.validators.stale_sensors.HAClient") as mock_class:
        inst = _mock_states([])
        mock_class.return_value = inst
        with patch.dict("os.environ", {"HA_STALE_TIMEOUT": "notanint"}):
            v = StaleSensorValidator(str(config_dir))
            v.validate_all()
            assert_diagnostic(v, "info", "must be an integer")


# API shape-guard continue paths


def test_states_not_list_is_skipped(config_dir):
    """When /api/states returns a dict instead of a list, skip with info."""
    client = _mock_states({})
    with patch("tools.validators.stale_sensors.HAClient", return_value=client):
        v = StaleSensorValidator(str(config_dir))
        assert v.validate_all() is True
        assert_diagnostic(v, "info", "invalid API states format")


def test_non_dict_state_entry_skipped(config_dir):
    """Non-dict items in the states list are skipped."""
    with patch(
        "tools.validators.stale_sensors.HAClient",
        return_value=_mock_states(["oops", 42]),
    ):
        assert StaleSensorValidator(str(config_dir)).validate_all() is True


def test_dotless_entity_id_skipped(config_dir):
    """State entries without a dot in entity_id are skipped."""
    states = [
        {
            "entity_id": "no_dot",
            "last_updated": "2026-06-24T20:00:00+00:00",
            "attributes": {},
        }
    ]
    with patch(
        "tools.validators.stale_sensors.HAClient",
        return_value=_mock_states(states),
    ):
        assert StaleSensorValidator(str(config_dir)).validate_all() is True


def test_non_sensor_domain_skipped(config_dir):
    """States with domains outside only_domains are skipped."""
    states = [
        {
            "entity_id": "light.x",
            "last_updated": "2026-06-24T20:00:00+00:00",
            "attributes": {},
        }
    ]
    with patch(
        "tools.validators.stale_sensors.HAClient",
        return_value=_mock_states(states),
    ):
        assert StaleSensorValidator(str(config_dir)).validate_all() is True


def test_sensor_with_unparseable_baseline_skipped(config_dir):
    """A sensor with an unparseable timestamp yields no baseline and is skipped."""
    states = [
        {
            "entity_id": "sensor.x",
            "last_updated": "garbage",
            "attributes": {},
        }
    ]
    with patch(
        "tools.validators.stale_sensors.HAClient",
        return_value=_mock_states(states),
    ):
        assert StaleSensorValidator(str(config_dir)).validate_all() is True


# Binary sensor without heartbeat: use only_domains so the default sensor filter
# does not skip the heartbeat-specific path.


def test_binary_sensor_without_heartbeat_skipped(config_dir):
    """A binary sensor without last_seen/last_reported is skipped."""
    states = [
        {
            "entity_id": "binary_sensor.x",
            "last_updated": "2026-06-24T20:00:00+00:00",
            "attributes": {},
        }
    ]
    with patch(
        "tools.validators.stale_sensors.HAClient",
        return_value=_mock_states(states),
    ):
        v = StaleSensorValidator(str(config_dir), only_domains={"binary_sensor"})
        assert v.validate_all() is True


def test_fail_on_stale_ignores_non_staleness_warnings(config_dir):
    """A registry-read failure (warning) with fresh sensors must NOT trip fail mode."""
    registry_file = config_dir / ".storage" / "core.entity_registry"
    registry_file.write_text("{ not valid json")
    fresh = [
        {
            "entity_id": "sensor.ok",
            "state": "1",
            "last_updated": "2026-07-17T00:00:00+00:00",
            "attributes": {},
        }
    ]
    mock_client = _mock_states(fresh)
    with (
        patch("tools.validators.stale_sensors.HAClient", return_value=mock_client),
        patch.object(
            StaleSensorValidator,
            "_get_current_time",
            return_value=datetime(2026, 7, 17, 0, 0, 10, tzinfo=UTC),
        ),
    ):
        v = StaleSensorValidator(
            str(config_dir), fail_on_stale=True, threshold_hours=24
        )
        assert v.validate_all() is True
    assert v.warnings
    assert_no_diagnostic(v, "errors")


def test_fail_on_stale_trips_on_real_stale_sensor(config_dir):
    """A genuinely stale sensor with fail_on_stale=True must fail (return False)."""
    _write_entity_registry(
        config_dir,
        [
            {"entity_id": "sensor.stale", "platform": "zha", "disabled_by": None},
        ],
    )
    stale = [
        {
            "entity_id": "sensor.stale",
            "state": "1",
            "last_updated": "2026-07-15T00:00:00+00:00",
            "attributes": {},
        }
    ]
    mock_client = _mock_states(stale)
    with (
        patch("tools.validators.stale_sensors.HAClient", return_value=mock_client),
        patch.object(
            StaleSensorValidator,
            "_get_current_time",
            return_value=datetime(2026, 7, 17, 0, 0, 0, tzinfo=UTC),
        ),
    ):
        v = StaleSensorValidator(
            str(config_dir), fail_on_stale=True, threshold_hours=24
        )
        assert v.validate_all() is False
    assert_diagnostic(v, "errors", "Stale sensor check failed")


# Restored entities, timestamp selection, and threshold boundary behavior


def test_ignore_restored_true_skips_restored_entities(config_dir):
    """ignore_restored=True skips entities with restored=true."""
    _write_entity_registry(
        config_dir,
        [
            {
                "entity_id": "sensor.restored_temp",
                "platform": "mqtt",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
    )
    mock_client = _mock_states(
        [
            {
                "entity_id": "sensor.restored_temp",
                "state": "21.5",
                "last_updated": "2026-06-24T20:00:00+00:00",
                "attributes": {"restored": True},
            }
        ]
    )
    with patch("tools.validators.stale_sensors.HAClient", return_value=mock_client):
        v = StaleSensorValidator(str(config_dir), ignore_restored=True)
        v._get_current_time = MagicMock(
            return_value=datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC)
        )
        assert v.validate_all() is True
        assert_no_diagnostic(v, "warnings")


def test_last_changed_and_last_updated_uses_older_timestamp(config_dir):
    """When both timestamps exist, validation uses the older timestamp."""
    _write_entity_registry(
        config_dir,
        [
            {
                "entity_id": "sensor.test_temp",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
    )
    mock_client = _mock_states(
        [
            {
                "entity_id": "sensor.test_temp",
                "state": "21.5",
                "last_changed": "2026-06-24T15:00:00+00:00",
                "last_updated": "2026-06-25T00:00:00+00:00",
                "attributes": {},
            }
        ]
    )
    with patch("tools.validators.stale_sensors.HAClient", return_value=mock_client):
        v = StaleSensorValidator(str(config_dir), threshold_hours=24)
        v._get_current_time = MagicMock(
            return_value=datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC)
        )
        assert v.validate_all() is True
        assert_diagnostic(v, "warnings", "sensor.test_temp")
        assert_diagnostic(v, "warnings", "stale")


def test_strict_boundary_not_flagged(config_dir):
    """Elapsed time exactly at the threshold is not flagged (strict >)."""
    _write_entity_registry(
        config_dir,
        [
            {
                "entity_id": "sensor.boundary",
                "platform": "zha",
                "disabled_by": None,
                "hidden_by": None,
            }
        ],
    )
    mock_client = _mock_states(
        [
            {
                "entity_id": "sensor.boundary",
                "state": "21.5",
                "last_updated": "2026-06-24T21:00:00+00:00",
                "attributes": {},
            }
        ]
    )
    with patch("tools.validators.stale_sensors.HAClient", return_value=mock_client):
        v = StaleSensorValidator(str(config_dir), threshold_hours=24)
        v._get_current_time = MagicMock(
            return_value=datetime(2026, 6, 25, 21, 0, 0, tzinfo=UTC)
        )
        assert v.validate_all() is True
        assert_no_diagnostic(v, "warnings")


def test_stale_validator_missing_config_dir_uses_base_validation():
    """A missing config directory is rejected before contacting HA."""
    with patch(
        "tools.validators.stale_sensors.HAClient", side_effect=OSError("unreachable")
    ) as mock_ha_client:
        v = StaleSensorValidator("/nonexistent/path/that/does/not/exist")
        result = v.validate_all()
    assert result is False
    assert f"Config directory {v.config_dir} does not exist" in v.errors
    mock_ha_client.assert_not_called()
