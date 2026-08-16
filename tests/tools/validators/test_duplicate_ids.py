"""Unit tests for duplicate_id_validator.py — duplicate automation ID detection."""

import builtins
from unittest.mock import patch

import pytest

from tests.helpers import assert_diagnostic, assert_no_diagnostic, write_yaml
from tools.validators.duplicate_ids import DuplicateIDValidator, main


@pytest.fixture
def validator(config_dir):
    return DuplicateIDValidator(str(config_dir))


class TestFileDeps:
    def test_file_deps_automations_and_scripts(self):
        """DuplicateIDValidator reads automations.yaml and scripts.yaml (M10b)."""
        v = DuplicateIDValidator()
        deps = v.file_deps()
        assert "automations.yaml" in deps
        assert "scripts.yaml" in deps
        assert len(deps) == 2


class TestM10bScriptsDuplicateKeys:
    """M10b: duplicate top-level keys in scripts.yaml must be flagged."""

    def test_duplicate_script_keys_flagged(self, config_dir):
        """PyYAML safe_load silently dedupes; we need a key-aware loader."""
        (config_dir / "scripts.yaml").write_text(
            "good_script:\n"
            "  alias: First\n"
            "  sequence: []\n"
            "good_script:\n"
            "  alias: Second\n"
            "  sequence: []\n"
        )
        v = DuplicateIDValidator(str(config_dir))
        is_valid = v.validate_all()
        assert is_valid is False
        assert_diagnostic(v, "errors", "good_script")

    def test_scripts_without_duplicates_pass(self, config_dir):
        (config_dir / "scripts.yaml").write_text(
            "script_one:\n"
            "  alias: First\n"
            "  sequence: []\n"
            "script_two:\n"
            "  alias: Second\n"
            "  sequence: []\n"
        )
        v = DuplicateIDValidator(str(config_dir))
        assert v.validate_all() is True

    def test_home_assistant_tagged_script_passes(self, config_dir):
        """Duplicate-key parsing accepts the same tags as HA YAML parsing."""
        (config_dir / "scripts.yaml").write_text(
            "tagged_script:\n  sequence: []\n  icon: !secret script_icon\n"
        )

        v = DuplicateIDValidator(str(config_dir))

        assert v.validate_all() is True
        assert v.errors == []

    def test_constructor_error_is_not_reported_as_duplicate_key(self, config_dir):
        """An unrelated key-construction error must retain its parse category."""
        (config_dir / "scripts.yaml").write_text(
            "!unknown first: {sequence: []}\n!unknown second: {sequence: []}\n"
        )

        v = DuplicateIDValidator(str(config_dir))

        assert v.validate_all() is False
        assert_diagnostic(v, "errors", "could not determine a constructor")
        assert_no_diagnostic(v, "errors", "duplicate top-level key: None")

    def test_yaml_merge_keys_are_supported(self, config_dir):
        (config_dir / "scripts.yaml").write_text(
            "base: &base\n  sequence: []\nmerged:\n  <<: *base\n"
        )

        v = DuplicateIDValidator(str(config_dir))

        assert v.validate_all() is True

    def test_unhashable_yaml_key_is_reported_as_parse_error(self, config_dir):
        (config_dir / "scripts.yaml").write_text(
            "? [first, second]\n: {sequence: []}\n"
        )

        v = DuplicateIDValidator(str(config_dir))

        assert v.validate_all() is False
        assert_diagnostic(v, "errors", "unhashable key")


class TestDuplicateIDValidator:
    def test_valid_automations(self, config_dir, validator):
        automations = [
            {"id": "auto_1", "alias": "First", "trigger": [], "action": []},
            {"id": "auto_2", "alias": "Second", "trigger": [], "action": []},
        ]
        write_yaml(config_dir, automations)
        assert validator.validate_all() is True
        assert len(validator.errors) == 0

    def test_duplicate_ids_fail(self, config_dir, validator):
        automations = [
            {"id": "same_id", "alias": "First", "trigger": [], "action": []},
            {"id": "same_id", "alias": "Second", "trigger": [], "action": []},
        ]
        write_yaml(config_dir, automations)
        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "same_id")

    def test_missing_id_warns(self, config_dir, validator):
        automations = [
            {"alias": "No Id Automation", "trigger": [], "action": []},
        ]
        write_yaml(config_dir, automations)
        assert validator.validate_all() is True
        assert_diagnostic(validator, "warnings", "missing")

    def test_empty_file_passes(self, config_dir, validator):
        """No automations.yaml at all — nothing to check."""
        assert validator.validate_all() is True

    def test_nonexistent_dir_errors(self):
        v = DuplicateIDValidator("/nonexistent")
        assert v.validate_all() is False
        assert_diagnostic(v, "errors", "does not exist")

    def test_non_list_automations_handled(self, config_dir, validator):
        f = config_dir / "automations.yaml"
        f.write_text("not_a_list: true\n")
        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "must be a list")

    def test_broken_yaml_fails(self, config_dir, validator):
        f = config_dir / "automations.yaml"
        f.write_text("{{{ not valid yaml\n")
        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "syntax error")

    def test_empty_file_exists_passes(self, config_dir, validator):
        """File exists but YAML parses to None (empty doc)."""
        f = config_dir / "automations.yaml"
        f.write_text("")
        assert validator.validate_all() is True
        assert len(validator.errors) == 0

    def test_non_dict_entry_handled(self, config_dir, validator):
        automations = [
            "not_a_dict",
            {"id": "ok", "alias": "OK", "trigger": [], "action": []},
        ]
        write_yaml(config_dir, automations)
        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "must be a dictionary")

    def test_mixed_duplicates_and_missing(self, config_dir, validator):
        automations = [
            {"id": "dup", "alias": "First", "trigger": [], "action": []},
            {"alias": "No ID", "trigger": [], "action": []},
            {"id": "dup", "alias": "Second", "trigger": [], "action": []},
        ]
        write_yaml(config_dir, automations)
        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "dup")
        assert_diagnostic(validator, "warnings", "missing")
        assert_diagnostic(validator, "info", "missing 'id'")

    def test_three_way_duplicate_detected(self, config_dir, validator):
        automations = [
            {"id": "dup", "alias": "A", "trigger": [], "action": []},
            {"id": "dup", "alias": "B", "trigger": [], "action": []},
            {"id": "dup", "alias": "C", "trigger": [], "action": []},
        ]
        write_yaml(config_dir, automations)
        assert validator.validate_all() is False
        dup_errors = [e for e in validator.errors if "duplicate" in e.lower()]
        assert len(dup_errors) == 1
        assert "positions" in dup_errors[0]
        assert "[0, 1, 2]" in dup_errors[0]
        assert "A" in dup_errors[0] or "B" in dup_errors[0] or "C" in dup_errors[0]

    def test_duplicate_id_error_lists_positions_and_aliases(
        self, config_dir, validator
    ):
        """L46: the error must include positions and aliases, not just a count."""
        automations = [
            {"id": "dup", "alias": "First", "trigger": [], "action": []},
            {"id": "dup", "alias": "Second", "trigger": [], "action": []},
            {"id": "unique", "alias": "Unique", "trigger": [], "action": []},
        ]
        write_yaml(config_dir, automations)
        assert validator.validate_all() is False
        dup_errors = [e for e in validator.errors if "duplicate" in e.lower()]
        assert len(dup_errors) == 1
        assert "positions [0, 1]" in dup_errors[0]
        assert "First" in dup_errors[0]
        assert "Second" in dup_errors[0]

    def test_null_id_treated_as_missing(self, config_dir, validator):
        automations = [
            {"id": None, "alias": "Null ID", "trigger": [], "action": []},
        ]
        write_yaml(config_dir, automations)
        assert validator.validate_all() is True
        assert_diagnostic(validator, "warnings", "missing")

    def test_int_id_handled(self, config_dir, validator):
        automations = [
            {"id": 123, "alias": "Int ID", "trigger": [], "action": []},
            {"id": 123, "alias": "Dup Int", "trigger": [], "action": []},
        ]
        write_yaml(config_dir, automations)
        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "123")


class TestConfigurationYamlOpenedOnce:
    def test_invalid_utf8_scripts_is_reported(self, config_dir):
        (config_dir / "scripts.yaml").write_bytes(b"script: \xff\n")
        validator = DuplicateIDValidator(str(config_dir))
        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "failed to parse")

    def test_configuration_yaml_not_accessed_unnecessarily(self, config_dir):
        """Validator should not parse configuration.yaml at all — it only reads
        automations.yaml. This is an efficiency regression test."""
        yaml_content = (
            "- id: a\n  alias: A\n  trigger:\n"
            "    platform: state\n  action:\n    service: test\n"
        )
        (config_dir / "automations.yaml").write_text(yaml_content)
        (config_dir / "configuration.yaml").write_text("homeassistant:\n  name: Test\n")

        open_count = 0
        real_open = builtins.open

        def tracking_open(file, *args, **kwargs):
            nonlocal open_count
            if "configuration.yaml" in str(file):
                open_count += 1
            return real_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=tracking_open):
            v = DuplicateIDValidator(str(config_dir))
            v.validate_all()

        assert open_count == 0, (
            f"configuration.yaml was opened {open_count} time(s); "
            "DuplicateIDValidator must not open it."
        )


class TestMainFunction:
    def test_main_passes_with_valid_config(self, config_dir, monkeypatch):
        (config_dir / "automations.yaml").write_text(
            "- id: a1\n  alias: A1\n  trigger: []\n  action: []\n"
        )
        monkeypatch.setattr("sys.argv", ["duplicate_ids.py", str(config_dir)])
        assert main() == 0

    def test_main_fails_with_duplicate(self, config_dir, monkeypatch):
        (config_dir / "automations.yaml").write_text(
            "- id: a1\n  alias: A1\n  trigger: []\n  action: []\n"
            "- id: a1\n  alias: A2\n  trigger: []\n  action: []\n"
        )
        monkeypatch.setattr("sys.argv", ["duplicate_ids.py", str(config_dir)])
        assert main() == 1
