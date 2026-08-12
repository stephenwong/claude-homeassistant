"""Unit tests for EntityDefinitionExtractor."""

import json

import pytest

from tools.validators.entity_definitions import EntityDefinitionExtractor


def test_extracts_builtin_entities(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "sun.sun" in entities
    assert "zone.home" in entities


def test_template_sensor_with_null_sensors_does_not_crash(tmp_path):
    """A null template sensor mapping does not crash extraction."""
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        "sensor:\n  - platform: template\n    sensors:\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    # Must not raise TypeError.
    entities = ext.get_config_defined_entities()
    assert isinstance(entities, set)


def test_extracts_timer_counter_schedule_helpers(tmp_path):
    """Timer, counter, and schedule helpers are extracted."""
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        "timer:\n"
        "  laundry:\n"
        "    duration: '00:30:00'\n"
        "counter:\n"
        "  coffee_count:\n"
        "    initial: 0\n"
        "schedule:\n"
        "  work_hours:\n"
        "    monday:\n"
        "      - from: '09:00'\n"
        "        to: '17:00'\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "timer.laundry" in entities
    assert "counter.coffee_count" in entities
    assert "schedule.work_hours" in entities


def test_extracts_single_dict_template_form(tmp_path):
    """A single-dict template form is extracted like the list form."""
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        "template:\n  - sensor:\n      name: My Sensor\n      state: '42'\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "sensor.my_sensor" in entities


def test_extracts_group_entity(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        "group:\n  my_group:\n    entities: []\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "group.my_group" in entities


def test_extracts_input_helpers(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        "input_boolean:\n  test_switch:\n    name: Test\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "input_boolean.test_switch" in entities


def test_extracts_template_list_entities(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        "template:\n  - sensor:\n      - name: Anyone Home\n        state: 'on'\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "sensor.anyone_home" in entities


def test_extracts_template_dict_entities(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        "template:\n  sensor:\n    - name: Dict Template\n      state: 'on'\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "sensor.dict_template" in entities


def test_extracts_platform_template_sensor(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        "sensor:\n"
        "  - platform: template\n"
        "    sensors:\n"
        "      custom_temp:\n"
        "        value_template: '{{ 42 }}'\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "sensor.custom_temp" in entities


def test_extracts_automation_entity_from_alias(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "automations.yaml").write_text(
        "- alias: Morning Lights\n"
        "  trigger:\n"
        "    platform: time\n"
        "  action:\n"
        "    service: test\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "automation.morning_lights" in entities


def test_automation_id_fallback(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "automations.yaml").write_text(
        "- id: backup_lights\n"
        "  trigger:\n    platform: time\n"
        "  action:\n    service: test\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "automation.backup_lights" in entities


def test_automation_empty_alias_and_id_skipped(tmp_path):
    """When alias is empty and id is only symbols, nothing is added."""
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "automations.yaml").write_text(
        "- alias:\n  trigger:\n    platform: time\n  action:\n    service: test\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert not any(e.startswith("automation.") for e in entities)


def test_extracts_script_entity(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "scripts.yaml").write_text(
        "disable_alarm_timed:\n  alias: Disable Alarm\n  sequence: []\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "script.disable_alarm_timed" in entities


def test_script_invalid_object_id_skipped(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "scripts.yaml").write_text(
        "UPPERCASE_SCRIPT:\n  sequence: []\nvalid_script:\n  sequence: []\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "script.UPPERCASE_SCRIPT" not in entities
    assert "script.valid_script" in entities


def test_extracts_scene_entity(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "scenes.yaml").write_text("- name: Office Night\n  entities: {}\n")
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "scene.office_night" in entities


def test_scene_name_slugified(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "scenes.yaml").write_text("- name: Evening Mode!!\n  entities: {}\n")
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "scene.evening_mode" in entities


def test_scene_falls_back_when_no_name_field(tmp_path):
    """A scene without a name is silently skipped."""
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "scenes.yaml").write_text(
        "- entities:\n    light.kitchen: 'on'\n"  # no `name`
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert not any(e.startswith("scene.") for e in entities)


def test_extracts_zone_from_config_yaml(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text("zone:\n  - name: Work\n")
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "zone.work" in entities


def test_extracts_zone_from_storage(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    zone_data = {"data": {"items": [{"name": "Back Yard"}, {"name": ""}]}}
    (tmp_path / ".storage" / "core.zone").write_text(json.dumps(zone_data))
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "zone.back_yard" in entities


def test_handles_missing_configuration_yaml(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "sun.sun" in entities


def test_handles_parse_error(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text("template: !bad_tag\n")
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert isinstance(entities, set)
    assert any("Failed to extract entity definitions" in msg for msg in w)


def test_reports_summary(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text("group:\n  test_group: {}\n")
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    ext.get_config_defined_entities()
    assert any("Config-defined entities:" in info for info in i)


def test_caches_result(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    r1 = ext.get_config_defined_entities()
    r2 = ext.get_config_defined_entities()
    assert r1 is r2


def test_restore_state_missing_file(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    result = ext.load_restore_state_entities()
    assert result == set()


def test_restore_state_bad_json(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / ".storage" / "core.restore_state").write_text("not json")
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    result = ext.load_restore_state_entities()
    assert result == set()
    assert any("Failed to load restore state" in msg for msg in w)


def test_restore_state_various_entries(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    data = {
        "data": [
            "not_a_dict",
            {"state": "not_a_dict"},
            {"state": {"entity_id": 123}},
            {"state": {"entity_id": "no_dot"}},
            {"state": {"entity_id": "sensor.restored"}},
        ]
    }
    (tmp_path / ".storage" / "core.restore_state").write_text(json.dumps(data))
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    result = ext.load_restore_state_entities()
    assert result == {"sensor.restored"}


def test_restore_state_cache(tmp_path):
    (tmp_path / ".storage").mkdir(exist_ok=True)
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    r1 = ext.load_restore_state_entities()
    r2 = ext.load_restore_state_entities()
    assert r1 is r2


def test_shared_warnings_append_to_validator(tmp_path):
    """Warnings/info lists shared with the validator get populated."""
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / ".storage" / "core.restore_state").write_text("bad json")
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    ext.load_restore_state_entities()
    shared_before = len(w)
    ext.load_restore_state_entities()
    assert len(w) == shared_before  # no new warnings on cache hit


def test_shared_info_append(tmp_path):
    """Extraction summary info appears in shared list."""
    (tmp_path / ".storage").mkdir(exist_ok=True)
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    ext.get_config_defined_entities()
    assert any("Config-defined entities:" in msg for msg in i)


@pytest.mark.parametrize("domain", ["cover", "fan", "lock", "vacuum", "weather"])
def test_extracts_additional_template_domains(tmp_path, domain):
    """Template integration supports the configured additional domains."""
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        f"template:\n  - {domain}:\n      - name: My Device\n        state: open\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert f"{domain}.my_device" in entities


def test_extracts_entities_from_packages(tmp_path):
    """The packages block is recursively searched for entity definitions."""
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        "packages:\n"
        "  my_pkg:\n"
        "    input_boolean:\n"
        "      guest_mode:\n        name: Guest Mode\n"
        "    template:\n"
        "      - sensor:\n"
        "          - name: Pkg Temp\n            state: '21'\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "input_boolean.guest_mode" in entities
    assert "sensor.pkg_temp" in entities


def test_extracts_entities_from_homeassistant_packages(tmp_path):
    (tmp_path / "configuration.yaml").write_text(
        "homeassistant:\n"
        "  packages:\n"
        "    lights:\n"
        "      input_boolean:\n"
        "        package_light: {}\n"
    )
    extractor = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", [], [])
    assert "input_boolean.package_light" in extractor.get_config_defined_entities()


def test_include_outside_config_dir_is_rejected(tmp_path):
    outside = tmp_path.parent / "outside.yaml"
    outside.write_text("- alias: Secret\n")
    extractor = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", [], [])
    assert extractor._resolve_include("!include ../outside.yaml") is None
    assert extractor.warnings


def test_extracts_entities_from_included_homeassistant_packages(tmp_path):
    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    (package_dir / "lights.yaml").write_text(
        "input_boolean:\n  package_light: {}\n"
        "automation:\n  - alias: Package Automation\n"
    )
    (tmp_path / "configuration.yaml").write_text(
        "homeassistant:\n  packages: !include_dir_named packages\n"
    )
    extractor = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", [], [])
    entities = extractor.get_config_defined_entities()
    assert "input_boolean.package_light" in entities
    assert "automation.package_automation" in entities


def test_resolves_include_dir_list_for_automation(tmp_path):
    """An automation !include_dir_list is resolved from configuration.yaml."""
    auto_dir = tmp_path / "automations_pkg"
    auto_dir.mkdir()
    # !include_dir_list: each file is a single item (dict).
    (auto_dir / "a.yaml").write_text(
        "alias: Included Auto\ntriggers: []\nactions: []\n"
    )
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include_dir_list automations_pkg/\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "automation.included_auto" in entities


def test_resolves_include_dir_merge_list_for_automation(tmp_path):
    """!include_dir_merge_list: each file is a list, merged into automation."""
    auto_dir = tmp_path / "auto_pkg"
    auto_dir.mkdir()
    (auto_dir / "a.yaml").write_text("- alias: From A\n  triggers: []\n  actions: []\n")
    (auto_dir / "b.yaml").write_text("- alias: From B\n  triggers: []\n  actions: []\n")
    (tmp_path / ".storage").mkdir(exist_ok=True)
    (tmp_path / "configuration.yaml").write_text(
        "automation: !include_dir_merge_list auto_pkg/\n"
    )
    w, i = [], []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", w, i)
    entities = ext.get_config_defined_entities()
    assert "automation.from_a" in entities
    assert "automation.from_b" in entities


class TestMakeEntityId:
    """Direct unit tests for EntityDefinitionExtractor._make_entity_id."""

    def test_returns_domain_dot_slug_for_simple_name(self):
        assert (
            EntityDefinitionExtractor._make_entity_id("scene", "Living Room")
            == "scene.living_room"
        )

    def test_returns_none_for_empty_name(self):
        assert EntityDefinitionExtractor._make_entity_id("scene", "") is None

    def test_returns_none_for_punctuation_only_name(self):
        assert EntityDefinitionExtractor._make_entity_id("scene", "!!!") is None

    def test_explicit_id_overrides_name(self):
        assert (
            EntityDefinitionExtractor._make_entity_id(
                "automation", "Friendly Name", explicit_id="custom_id"
            )
            == "automation.custom_id"
        )

    def test_explicit_id_falsy_falls_back_to_name(self):
        assert (
            EntityDefinitionExtractor._make_entity_id(
                "automation", "Friendly Name", explicit_id=None
            )
            == "automation.friendly_name"
        )


class TestLoadYamlGlob:
    """Direct unit tests for EntityDefinitionExtractor._load_yaml_glob."""

    def test_returns_sorted_path_data_pairs(self, tmp_path):
        (tmp_path / "b.yaml").write_text("foo: 1\n")
        (tmp_path / "a.yaml").write_text("bar: 2\n")
        ext = EntityDefinitionExtractor(tmp_path, tmp_path, [], [])
        result = ext._load_yaml_glob(tmp_path)
        assert [p.name for p, _d in result] == ["a.yaml", "b.yaml"]
        assert result[0][1] == {"bar": 2}
        assert result[1][1] == {"foo": 1}

    def test_skips_non_yaml_files(self, tmp_path):
        (tmp_path / "ignore.txt").write_text("nope\n")
        (tmp_path / "keep.yaml").write_text("x: 1\n")
        ext = EntityDefinitionExtractor(tmp_path, tmp_path, [], [])
        result = ext._load_yaml_glob(tmp_path)
        assert len(result) == 1
        assert result[0][0].name == "keep.yaml"

    def test_empty_dir_returns_empty_list(self, tmp_path):
        ext = EntityDefinitionExtractor(tmp_path, tmp_path, [], [])
        assert ext._load_yaml_glob(tmp_path) == []


class TestLoadYamlFile:
    """Direct unit tests for EntityDefinitionExtractor._load_yaml_file."""

    def test_parses_valid_yaml(self, tmp_path):
        f = tmp_path / "f.yaml"
        f.write_text("foo: 1\nbar: [2, 3]\n")
        warnings: list[str] = []
        ext = EntityDefinitionExtractor(tmp_path, tmp_path, warnings, [])
        assert ext._load_yaml_file(f) == {"foo": 1, "bar": [2, 3]}
        assert warnings == []

    def test_records_warning_on_malformed_yaml(self, tmp_path):
        f = tmp_path / "f.yaml"
        f.write_text("foo: [unterminated\n")
        warnings: list[str] = []
        ext = EntityDefinitionExtractor(tmp_path, tmp_path, warnings, [])
        assert ext._load_yaml_file(f) is None
        assert len(warnings) == 1

    def test_records_warning_on_missing_file(self, tmp_path):
        warnings: list[str] = []
        ext = EntityDefinitionExtractor(tmp_path, tmp_path, warnings, [])
        assert ext._load_yaml_file(tmp_path / "nonexistent.yaml") is None
        assert len(warnings) == 1


def test_input_helper_domain_constants_match_ha_set():
    """Pin the canonical HA input-helper set so a HA-release update is loud."""
    from tools.validators.entity_definitions import _INPUT_HELPER_DOMAINS

    assert set(_INPUT_HELPER_DOMAINS) == {
        "input_boolean",
        "input_number",
        "input_text",
        "input_select",
        "input_datetime",
        "input_button",
        "timer",
        "counter",
        "schedule",
    }


def test_template_entity_domain_constants_match_extracted_set():
    """Pin the canonical HA template-entity set so a HA-release update is loud."""
    from tools.validators.entity_definitions import _TEMPLATE_ENTITY_DOMAINS

    assert "sensor" in _TEMPLATE_ENTITY_DOMAINS
    assert "binary_sensor" in _TEMPLATE_ENTITY_DOMAINS
    assert "weather" in _TEMPLATE_ENTITY_DOMAINS
    assert len(_TEMPLATE_ENTITY_DOMAINS) == 17


def test_automation_with_both_alias_and_id_uses_alias(tmp_path):
    """Pin behavior: when both alias and id are present, alias wins."""
    auto_file = tmp_path / "automations.yaml"
    auto_file.write_text("- alias: Friendly Name\n  id: abc123\n")
    ext = EntityDefinitionExtractor(tmp_path, tmp_path, [], [])
    result = ext._extract_automation_entities()
    assert "automation.friendly_name" in result
    assert "automation.abc123" not in result


def test_missing_automations_yaml_is_silent_skip(tmp_path):
    """No warning is appended when automations.yaml is absent."""
    warnings: list[str] = []
    ext = EntityDefinitionExtractor(tmp_path, tmp_path, warnings, [])
    result = ext._extract_automation_entities()
    assert result == set()
    assert warnings == []


@pytest.mark.parametrize(
    ("domain", "config_key", "name_field", "file_name", "yaml_data", "expected"),
    [
        (
            "automation",
            "automation",
            "alias",
            "automations.yaml",
            "- alias: Morning Lights\n- id: id_only\n- alias: '!!!'\n- invalid\n",
            {"automation.morning_lights", "automation.id_only"},
        ),
        (
            "scene",
            "scene",
            "name",
            "scenes.yaml",
            "- name: Evening Mode!!\n- name: '!!!'\n- invalid\n",
            {"scene.evening_mode"},
        ),
    ],
    ids=["automation", "scene"],
)
def test_named_entity_include_and_file_fallback_match(
    tmp_path, domain, config_key, name_field, file_name, yaml_data, expected
):
    """Include and file-fallback inputs share the same extraction contract."""
    include_root = tmp_path / "include"
    fallback_root = tmp_path / "fallback"
    for root in (include_root, fallback_root):
        (root / ".storage").mkdir(parents=True)
        (root / file_name).write_text(yaml_data)

    kwargs = {
        "domain": domain,
        "config_key": config_key,
        "name_field": name_field,
        "file_name": file_name,
        "allow_id_fallback": domain == "automation",
    }
    included = EntityDefinitionExtractor(
        include_root, include_root / ".storage", [], []
    )
    fallback = EntityDefinitionExtractor(
        fallback_root, fallback_root / ".storage", [], []
    )

    include_result = included._extract_named_entities(
        {config_key: f"!include {file_name}"}, **kwargs
    )
    fallback_result = fallback._extract_named_entities(None, **kwargs)

    assert include_result == expected
    assert fallback_result == expected


def test_missing_explicit_include_does_not_fall_back_to_sidecar(tmp_path):
    (tmp_path / ".storage").mkdir()
    (tmp_path / "automations.yaml").write_text("- alias: Sidecar Only\n")
    extractor = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", [], [])

    result = extractor._extract_named_entities(
        {"automation": "!include missing.yaml"},
        domain="automation",
        config_key="automation",
        name_field="alias",
        file_name="automations.yaml",
        allow_id_fallback=True,
    )

    assert result == set()


def test_missing_explicit_script_include_does_not_fall_back_to_sidecar(tmp_path):
    (tmp_path / ".storage").mkdir()
    (tmp_path / "scripts.yaml").write_text("sidecar:\n  sequence: []\n")
    extractor = EntityDefinitionExtractor(tmp_path, tmp_path / ".storage", [], [])

    assert (
        extractor._extract_script_entities({"script": "!include missing.yaml"}) == set()
    )


@pytest.mark.parametrize("payload", ["{}", "[]", "null", '"not a mapping"'])
def test_restore_state_malformed_root_is_reported(tmp_path, payload):
    storage = tmp_path / ".storage"
    storage.mkdir()
    (storage / "core.restore_state").write_text(payload)
    warnings: list[str] = []
    ext = EntityDefinitionExtractor(tmp_path, storage, warnings, [])

    assert ext.load_restore_state_entities() == set()
    assert any("Failed to load restore state" in warning for warning in warnings)


@pytest.mark.parametrize("payload", ["{}", "[]", "null", '"not a mapping"'])
def test_zone_storage_malformed_root_is_reported(tmp_path, payload):
    storage = tmp_path / ".storage"
    storage.mkdir()
    zone_file = storage / "core.zone"
    zone_file.write_text(payload)
    warnings: list[str] = []
    ext = EntityDefinitionExtractor(tmp_path, storage, warnings, [])

    assert ext._extract_zone_entities(None) == set()
    assert any(
        "Failed to extract entity definitions" in warning for warning in warnings
    )
