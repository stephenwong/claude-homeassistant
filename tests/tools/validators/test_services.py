"""Unit tests for service_validator.py — service reference validation."""

from unittest.mock import MagicMock, patch

from tests.helpers import (
    assert_diagnostic,
    assert_no_diagnostic,
    mock_json_client,
    write_yaml,
)
from tools.common import HARequestError
from tools.validators.services import (
    ServiceValidator,
    _device_action_service,
    _normalize_service_catalog,
    main,
)

_write_automation = write_yaml


def _mock_services(entries: list[dict]) -> MagicMock:
    return mock_json_client(entries)


def _mock_offline() -> MagicMock:
    return mock_json_client(side_effect=HARequestError("offline"))


def _run_service_validation(config_dir, data, catalog, *, filename="automations.yaml"):
    """Write an ordinary YAML fixture and validate it against a catalog."""
    write_yaml(config_dir, data, filename)
    client = _mock_services(catalog)
    with patch("tools.validators.services.HAClient.from_env", return_value=client):
        validator = ServiceValidator(str(config_dir))
        result = validator.validate_all()
    return validator, result


class TestFileDeps:
    def test_file_deps_empty(self):
        v = ServiceValidator()
        assert v.file_deps() == []


class TestM10aDeviceActions:
    """M10a: HA device-action steps (device_id + domain + type) must be
    synthesised as `<domain>.<type>` for service validation."""

    def test_device_action_step_extracted_as_synthetic_service(self):
        found: list[tuple[str, str]] = []
        step = {
            "device_id": "abc-123",
            "domain": "light",
            "entity_id": "light.kitchen",
            "type": "turn_on",
        }
        ServiceValidator._extract_services(step, "automations.yaml", found)
        assert any(svc == "light.turn_on" for svc, _ in found)

    def test_device_action_without_domain_not_extracted(self):
        """Missing domain or type means it's not a valid device action."""
        found: list[tuple[str, str]] = []
        step = {"device_id": "abc-123", "type": "turn_on"}  # no domain
        ServiceValidator._extract_services(step, "automations.yaml", found)
        assert not any(svc == "turn_on" for svc, _ in found)

    def test_device_condition_is_not_extracted_as_service(self):
        found: list[tuple[str, str]] = []
        step = {
            "condition": "device",
            "device_id": "abc-123",
            "domain": "light",
            "type": "is_on",
        }
        ServiceValidator._extract_services(step, "automations.yaml", found)
        assert found == []

    def test_legacy_platform_device_trigger_is_not_extracted(self):
        found: list[tuple[str, str]] = []
        step = {
            "platform": "device",
            "device_id": "abc-123",
            "domain": "light",
            "type": "turned_on",
        }
        ServiceValidator._extract_services(step, "automations.yaml", found)
        assert found == []


class TestDeviceActionService:
    def test_valid_device_action_returns_synthetic_service(self):
        assert (
            _device_action_service(
                {
                    "device_id": "abc-123",
                    "domain": "light",
                    "type": "turn_on",
                }
            )
            == "light.turn_on"
        )

    def test_device_conditions_and_triggers_are_not_services(self):
        base = {
            "device_id": "abc-123",
            "domain": "light",
            "type": "turn_on",
        }
        for key, value in (("condition", "device"), ("trigger", "device")):
            step = {**base, key: value}
            assert _device_action_service(step) is None

    def test_missing_or_dynamic_device_action_fields_return_none(self):
        assert (
            _device_action_service({"device_id": "abc-123", "domain": "light"}) is None
        )
        assert (
            _device_action_service(
                {
                    "device_id": "abc-123",
                    "domain": "{{ domain }}",
                    "type": "turn_on",
                }
            )
            is None
        )


class TestM9DataPayloadNotExtracted:
    """M9: `action:` keys inside `data:` payloads are notification button labels,
    not service calls — must not be extracted."""

    def test_data_payload_actions_not_extracted_as_services(self):
        found: list[tuple[str, str]] = []
        config = {
            "action": "notify.mobile_app",
            "data": {
                "data": {
                    "actions": [
                        {"action": "SNOOZE"},
                        {"action": "DISMISS"},
                    ]
                }
            },
        }
        ServiceValidator._extract_services(config, "automations.yaml", found)
        services = [svc for svc, _ in found]
        assert services == ["notify.mobile_app"]
        assert "SNOOZE" not in services
        assert "DISMISS" not in services


class TestServiceCatalogNormalization:
    def test_normalizes_catalog_entries_to_domain_service_set(self):
        catalog = [
            {"domain": "light", "services": {"turn_on": {}, "turn_off": {}}},
            {"domain": "notify", "services": {"mobile": {}}},
            {"domain": "light", "services": {"turn_on": {}}},
        ]
        assert _normalize_service_catalog(catalog) == {
            "light.turn_on",
            "light.turn_off",
            "notify.mobile",
        }

    def test_ignores_malformed_catalog_entries(self):
        assert (
            _normalize_service_catalog(
                [
                    "not an entry",
                    {"domain": "light", "services": ["turn_on"]},
                    {"domain": None, "services": {"turn_on": {}}},
                    {"domain": "", "services": {"turn_on": {}}},
                    {"domain": "light", "services": {"": {}, None: {}}},
                ]
            )
            == set()
        )


class TestServiceValidation:
    def test_valid_service_passes(self, config_dir):
        v, result = _run_service_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "light.turn_on", "data": {}},
                    ],
                },
            ],
            [{"domain": "light", "services": {"turn_on": {}}}],
        )
        assert result is True
        assert_no_diagnostic(v, "errors")

    def test_unknown_service_warns(self, config_dir):
        v, result = _run_service_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "light.turn_onn", "data": {}},
                    ],
                },
            ],
            [{"domain": "light", "services": {"turn_on": {}}}],
        )
        assert result is True
        assert_diagnostic(v, "warnings", "light.turn_onn")

    def test_duplicate_service_references_have_stable_sorted_diagnostics(
        self, config_dir
    ):
        v, result = _run_service_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "light.zulu", "data": {}},
                        {"action": "light.alpha", "data": {}},
                        {"action": "light.zulu", "data": {}},
                    ],
                },
            ],
            [{"domain": "light", "services": {}}],
        )
        assert result is True

        unknown = [warning for warning in v.warnings if "Unknown service" in warning]
        assert unknown == [
            "automations.yaml[0].actions[1].action: Unknown service "
            "'light.alpha' (service not loaded?)",
            "automations.yaml[0].actions[0].action: Unknown service "
            "'light.zulu' (service not loaded?)",
            "automations.yaml[0].actions[2].action: Unknown service "
            "'light.zulu' (service not loaded?)",
        ]

    def test_legacy_service_key_supported(self, config_dir):
        v, result = _run_service_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"service": "notify.mobile_devices", "data": {}},
                    ],
                },
            ],
            [{"domain": "notify", "services": {"mobile_devices": {}}}],
        )
        assert result is True

    def test_template_action_skipped(self, config_dir):
        v, result = _run_service_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "{{ 'light.turn_on' }}", "data": {}},
                    ],
                },
            ],
            [{"domain": "light", "services": {"turn_on": {}}}],
        )
        assert result is True
        assert_no_diagnostic(v, "errors")

    def test_secrets_yml_skipped(self, config_dir):
        f = config_dir / "secrets.yaml"
        f.write_text("api_key: secret123\n")
        mock_client = _mock_services([])
        with patch(
            "tools.validators.services.HAClient.from_env", return_value=mock_client
        ):
            v = ServiceValidator(str(config_dir))
            assert v.validate_all() is True

    def test_non_domain_service_value_ignored(self, config_dir):
        """Bare service names without a dot (e.g. notify.group sub-services)
        are not domain-qualified service calls and should be skipped."""
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "mobile_app_sm_s926b", "data": {}},
                    ],
                },
            ],
        )
        mock_client = _mock_services([])
        with patch(
            "tools.validators.services.HAClient.from_env", return_value=mock_client
        ):
            v = ServiceValidator(str(config_dir))
            assert v.validate_all() is True
            assert_diagnostic(v, "info", "non-domain")

    def test_no_actions_found_passes(self, config_dir):
        v, result = _run_service_validation(
            config_dir,
            [
                {"id": "t", "alias": "T", "triggers": [], "actions": []},
            ],
            [],
        )
        assert result is True

    def test_service_in_script_detected(self, config_dir):
        v, result = _run_service_validation(
            config_dir,
            {"my_script": {"sequence": [{"action": "light.turn_on", "data": {}}]}},
            [{"domain": "light", "services": {"turn_on": {}}}],
            filename="scripts.yaml",
        )
        assert result is True
        assert_no_diagnostic(v, "warnings")

    def test_broken_yaml_fails(self, config_dir):
        (config_dir / "automations.yaml").write_text("{{{ not valid yaml\n")
        mock_client = _mock_services([])
        with patch(
            "tools.validators.services.HAClient.from_env", return_value=mock_client
        ):
            v = ServiceValidator(str(config_dir))
            assert v.validate_all() is False
            assert_diagnostic(v, "errors", "syntax error")


class TestOfflineDegradation:
    def test_offline_degrades_to_format_check(self, config_dir):
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "light.turn_on", "data": {}},
                    ],
                },
            ],
        )
        mock_client = _mock_offline()
        with patch(
            "tools.validators.services.HAClient.from_env", return_value=mock_client
        ):
            v = ServiceValidator(str(config_dir))
            assert v.validate_all() is True
            assert_diagnostic(v, "info", "skipped")

    def test_offline_bad_format_fails(self, config_dir):
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "light..turn_on", "data": {}},
                    ],
                },
            ],
        )
        mock_client = _mock_offline()
        with patch(
            "tools.validators.services.HAClient.from_env", return_value=mock_client
        ):
            v = ServiceValidator(str(config_dir))
            assert v.validate_all() is False

    def test_catalog_is_none_handled(self, config_dir):
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "light.turn_on", "data": {}},
                    ],
                },
            ],
        )
        mock_client = mock_json_client()
        with patch(
            "tools.validators.services.HAClient.from_env", return_value=mock_client
        ):
            v = ServiceValidator(str(config_dir))
            assert v.validate_all() is True
            assert_diagnostic(v, "info", "skipped")
            assert_no_diagnostic(v, "errors")

    def test_offline_from_env_fails(self, config_dir):
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "light.turn_on", "data": {}},
                    ],
                },
            ],
        )
        with patch(
            "tools.validators.services.HAClient.from_env",
            side_effect=HARequestError("no env"),
        ):
            v = ServiceValidator(str(config_dir))
            assert v.validate_all() is True
            assert_diagnostic(v, "info", "skipped")


class TestEdgeCases:
    def test_nonexistent_dir_errors(self):
        v = ServiceValidator("/nonexistent")
        assert v.validate_all() is False
        assert_diagnostic(v, "errors", "does not exist")

    def test_mixed_known_and_unknown(self, config_dir):
        v, result = _run_service_validation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "light.turn_on", "data": {}},
                        {"action": "light.turn_off", "data": {}},
                        {"action": "light.nonexistent", "data": {}},
                    ],
                },
            ],
            [{"domain": "light", "services": {"turn_on": {}, "turn_off": {}}}],
        )
        assert result is True
        assert_diagnostic(v, "warnings", "light.nonexistent")
        assert_no_diagnostic(v, "errors", "light.turn_on")
        assert_no_diagnostic(v, "errors", "light.turn_off")


class TestL45NetworkGate:
    """L45: catalog fetch must be skipped when no domain-bearing refs exist."""

    def test_no_network_call_when_no_domain_qualified_services(
        self, config_dir, monkeypatch
    ):
        """L45: with only bare names (no dot), the catalog fetch must be skipped."""
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "mobile_app_sm_s926b", "data": {}},
                    ],
                },
            ],
        )
        # Without mocking, the gate should prevent from_env from being called
        v = ServiceValidator(str(config_dir))
        assert v.validate_all() is True
        # No "skipped" message means no network call was attempted
        assert_no_diagnostic(v, "info", "skipped")


class TestMain:
    def test_main_dispatches_clean(self, config_dir, monkeypatch):
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [
                        {"action": "light.turn_on", "data": {}},
                    ],
                },
            ],
        )
        mock_client = _mock_services([{"domain": "light", "services": {"turn_on": {}}}])
        monkeypatch.setattr("sys.argv", ["services", str(config_dir)])
        with patch(
            "tools.validators.services.HAClient.from_env", return_value=mock_client
        ):
            assert main() == 0

    def test_main_invalid(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["services", "/nonexistent"])
        assert main() == 1


class TestL47Nesting:
    """L47: choose/repeat/parallel nesting in service extraction."""

    def test_extracts_services_from_choose_repeat_parallel(self):
        config = {
            "action": [
                {"action": "light.turn_on"},
                {
                    "choose": [
                        {"conditions": [], "sequence": [{"action": "switch.turn_on"}]},
                    ]
                },
                {"repeat": {"count": 1, "sequence": [{"action": "light.turn_off"}]}},
                {"parallel": [{"action": "fan.turn_on"}]},
            ]
        }
        found = []
        ServiceValidator._extract_services(config, "automations.yaml", found)
        services = [svc for svc, _ in found]
        assert set(
            ["light.turn_on", "switch.turn_on", "light.turn_off", "fan.turn_on"]
        ) <= set(services)


class TestOfflineOSError:
    """W3.1: OSError from from_env() must degrade, not crash."""

    def test_oserror_from_from_env_is_skipped(self, config_dir):
        _write_automation(
            config_dir,
            [
                {
                    "id": "t",
                    "alias": "T",
                    "triggers": [],
                    "actions": [{"action": "light.turn_on", "data": {}}],
                },
            ],
        )
        with patch(
            "tools.validators.services.HAClient.from_env",
            side_effect=OSError("permission denied on .env"),
        ):
            v = ServiceValidator(str(config_dir))
            assert v.validate_all() is True
            assert_diagnostic(v, "info", "skipped")
