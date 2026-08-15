"""Tests for tools/yaml_validator.py - YAML syntax validation."""

import pytest
import yaml

from tests.helpers import assert_diagnostic, assert_no_diagnostic
from tools.validators.yaml import YAMLValidator, main


@pytest.fixture
def validator(config_dir):
    return YAMLValidator(str(config_dir))


class TestValidateYamlSyntax:
    def test_valid_yaml(self, config_dir, validator):
        f = config_dir / "test.yaml"
        f.write_text("key: value\nlist:\n  - a\n  - b\n")
        assert validator.validate_yaml_syntax(f) is True
        assert len(validator.errors) == 0

    def test_invalid_yaml(self, config_dir, validator):
        f = config_dir / "test.yaml"
        f.write_text("key: value\n  bad indent: oops\n")
        assert validator.validate_yaml_syntax(f) is False
        assert_diagnostic(validator, "errors", "YAML syntax error")

    def test_non_utf8_encoding(self, config_dir, validator):
        f = config_dir / "test.yaml"
        f.write_bytes(b"\xff\xfe key: value")
        assert validator.validate_yaml_syntax(f) is False


class TestEmptyHomeAssistantSection:
    def test_empty_homeassistant_section_no_warning(self, config_dir):
        """L36: homeassistant: (parses to None) must NOT trigger a warning."""
        (config_dir / "configuration.yaml").write_text("homeassistant:\n")
        v = YAMLValidator(str(config_dir))
        v.validate_all()
        assert_no_diagnostic(v, "warnings", "homeassistant")


class TestValidateConfigurationStructure:
    def test_non_configuration_yaml_skipped(self, config_dir, validator):
        f = config_dir / "other.yaml"
        f.write_text("key: value")
        assert validator.validate_configuration_structure(f, {"key": "value"}) is True

    def test_valid_configuration(self, config_dir, validator):
        f = config_dir / "configuration.yaml"
        content = "homeassistant:\n  name: Test\n"
        f.write_text(content)
        assert (
            validator.validate_configuration_structure(f, yaml.safe_load(content))
            is True
        )
        assert len(validator.errors) == 0

    def test_configuration_not_dict(self, config_dir, validator):
        f = config_dir / "configuration.yaml"
        content = "- item1\n- item2\n"
        f.write_text(content)
        assert (
            validator.validate_configuration_structure(f, yaml.safe_load(content))
            is False
        )
        assert_diagnostic(validator, "errors", "must be a dictionary")

    def test_configuration_missing_homeassistant(self, config_dir, validator):
        f = config_dir / "configuration.yaml"
        content = "logger:\n  default: info\n"
        f.write_text(content)
        validator.validate_configuration_structure(f, yaml.safe_load(content))
        assert_diagnostic(validator, "warnings", "homeassistant")

    def test_configuration_deprecated_keys(self, config_dir, validator):
        f = config_dir / "configuration.yaml"
        content = "homeassistant:\n  name: Test\ndiscovery:\nintroduction:\n"
        f.write_text(content)
        validator.validate_configuration_structure(f, yaml.safe_load(content))
        assert_diagnostic(validator, "warnings", "discovery")
        assert_diagnostic(validator, "warnings", "introduction")

    def test_configuration_homeassistant_null_no_warning(self, config_dir, validator):
        """L36: homeassistant: null (None) must NOT trigger a warning."""
        f = config_dir / "configuration.yaml"
        content = "homeassistant: null\n"
        f.write_text(content)
        validator.validate_configuration_structure(f, yaml.safe_load(content))
        assert_no_diagnostic(validator, "warnings", "homeassistant")


class TestValidateAutomationsStructure:
    def test_non_automations_yaml_skipped(self, config_dir, validator):
        f = config_dir / "other.yaml"
        f.write_text("key: value")
        assert validator.validate_automations_structure(f, {"key": "value"}) is True

    def test_empty_automations(self, config_dir, validator):
        f = config_dir / "automations.yaml"
        f.write_text("")
        assert validator.validate_automations_structure(f, None) is True

    def test_valid_automations(self, config_dir, validator):
        f = config_dir / "automations.yaml"
        content = (
            "- alias: Test\n  trigger:\n    platform: state\n"
            "  action:\n    service: test\n"
        )
        f.write_text(content)
        assert (
            validator.validate_automations_structure(f, yaml.safe_load(content)) is True
        )

    def test_automations_not_list(self, config_dir, validator):
        f = config_dir / "automations.yaml"
        content = "key: value\n"
        f.write_text(content)
        assert (
            validator.validate_automations_structure(f, yaml.safe_load(content))
            is False
        )
        assert_diagnostic(validator, "errors", "must be a list")


class TestValidateScriptsStructure:
    def test_non_scripts_yaml_skipped(self, config_dir, validator):
        f = config_dir / "other.yaml"
        f.write_text("key: value")
        assert validator.validate_scripts_structure(f, {"key": "value"}) is True

    def test_empty_scripts(self, config_dir, validator):
        f = config_dir / "scripts.yaml"
        f.write_text("")
        assert validator.validate_scripts_structure(f, None) is True

    def test_valid_scripts(self, config_dir, validator):
        f = config_dir / "scripts.yaml"
        content = "my_script:\n  sequence:\n    - service: test\n"
        f.write_text(content)
        assert validator.validate_scripts_structure(f, yaml.safe_load(content)) is True

    def test_scripts_not_dict(self, config_dir, validator):
        f = config_dir / "scripts.yaml"
        content = "- item1\n- item2\n"
        f.write_text(content)
        assert validator.validate_scripts_structure(f, yaml.safe_load(content)) is False
        assert_diagnostic(validator, "errors", "must be a dictionary")


class TestValidateAll:
    def test_scans_yaml_directory_once(self, config_dir, monkeypatch):
        (config_dir / "configuration.yaml").write_text("homeassistant:\n")
        validator = YAMLValidator(str(config_dir))
        original_get_yaml_files = validator.get_yaml_files
        calls = []

        def get_yaml_files():
            calls.append(True)
            return original_get_yaml_files()

        monkeypatch.setattr(validator, "get_yaml_files", get_yaml_files)

        assert validator.validate_all() is True
        assert len(calls) == 1

    def test_nonexistent_config_dir(self):
        v = YAMLValidator("/nonexistent/path")
        assert v.validate_all() is False
        assert_diagnostic(v, "errors", "does not exist")

    def test_empty_config_dir(self, config_dir, validator):
        assert validator.validate_all() is True
        assert_diagnostic(validator, "warnings", "No YAML files")

    def test_skips_secrets_yaml(self, config_dir, validator):
        (config_dir / "secrets.yaml").write_text("api_key: secret123\n")
        (config_dir / "configuration.yaml").write_text("homeassistant:\n  name: Test\n")
        assert validator.validate_all() is True

    def test_validates_all_files(self, config_dir, validator):
        (config_dir / "configuration.yaml").write_text("homeassistant:\n  name: Test\n")
        (config_dir / "automations.yaml").write_text(
            "- alias: Test\n  trigger:\n    platform: state\n"
            "  action:\n    service: test\n"
        )
        (config_dir / "scripts.yaml").write_text(
            "my_script:\n  sequence:\n    - service: test\n"
        )
        assert validator.validate_all() is True

    def test_fails_on_bad_processes_good_also(self, config_dir, validator):
        """L37: validator collects errors from BOTH files — not just the first."""
        (config_dir / "good.yaml").write_text("key: value\n")
        (config_dir / "bad.yaml").write_text("key: value\n  bad: indent\n")
        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "YAML syntax error")

    def test_fails_on_encoding_error(self, config_dir, validator):
        (config_dir / "bad.yaml").write_bytes(b"\xff\xfe\x00\x01")
        assert validator.validate_all() is False

    def test_fails_on_syntax_error(self, config_dir, validator):
        (config_dir / "bad.yaml").write_text("key: value\n  bad: indent\n")
        assert validator.validate_all() is False

    def test_automations_not_list_via_validate_all(self, config_dir, validator):
        (config_dir / "automations.yaml").write_text("not_a_list: true\n")
        (config_dir / "configuration.yaml").write_text("homeassistant:\n")
        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "Automations must be a list")

    def test_scripts_not_dict_via_validate_all(self, config_dir, validator):
        (config_dir / "scripts.yaml").write_text("- item\n")
        (config_dir / "configuration.yaml").write_text("homeassistant:\n")
        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "Scripts must be a dictionary")

    def test_configuration_not_dict_via_validate_all(self, config_dir, validator):
        (config_dir / "configuration.yaml").write_text("- item\n")
        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "Configuration must be a dictionary")

    def test_mixed_invalid_files_collect_all_structure_diagnostics(
        self, config_dir, validator
    ):
        """A failed check must not short-circuit the remaining checks."""
        (config_dir / "configuration.yaml").write_text("- item\n")
        (config_dir / "automations.yaml").write_text("not_a_list: true\n")
        (config_dir / "scripts.yaml").write_text("- item\n")

        assert validator.validate_all() is False
        assert_diagnostic(validator, "errors", "Configuration must be a dictionary")
        assert_diagnostic(validator, "errors", "Automations must be a list")
        assert_diagnostic(validator, "errors", "Scripts must be a dictionary")


class TestYAMLValidatorMain:
    """Cover lines 149-164: main() function."""

    def test_main_valid(self, config_dir, monkeypatch):
        (config_dir / "configuration.yaml").write_text("homeassistant:\n  name: Test\n")
        monkeypatch.setattr("sys.argv", ["yaml_validator", str(config_dir)])
        assert main() == 0

    def test_main_invalid(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["yaml_validator", "/nonexistent"])
        assert main() == 1
