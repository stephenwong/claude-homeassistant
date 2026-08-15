#!/usr/bin/env python3
"""YAML syntax validator for Home Assistant configuration files."""

from pathlib import Path
from typing import Any

from tools.validators.base import ValidatorBase

_DEPRECATED_CONFIGURATION_KEYS = ("discovery", "introduction")


class YAMLValidator(ValidatorBase):
    """Validates YAML syntax and basic structure for Home Assistant files."""

    validator_name = "YAML syntax"

    def validate_yaml_syntax(self, file_path: Path) -> bool:
        """Validate YAML syntax of a single file."""
        _, ok = self.load_yaml_checked(file_path)
        return ok

    def validate_configuration_structure(self, file_path: Path, data: Any) -> bool:
        """Validate basic Home Assistant configuration.yaml structure."""
        if file_path.name != "configuration.yaml":
            return True

        if not isinstance(data, dict):
            self.errors.append(f"{file_path}: Configuration must be a dictionary")
            return False

        # Check for common configuration issues
        if "homeassistant" not in data:
            self.warnings.append(f"{file_path}: Missing 'homeassistant' section")
        elif data.get("homeassistant") is not None and not isinstance(
            data.get("homeassistant"), dict
        ):
            self.warnings.append(
                f"{file_path}: 'homeassistant' section should be a dictionary"
            )

        # Check for deprecated keys
        for key in _DEPRECATED_CONFIGURATION_KEYS:
            if key in data:
                self.warnings.append(f"{file_path}: '{key}' is deprecated")

        return True

    def check_automations_structure(self, automations: list, source: str) -> bool:
        """Validate parsed automations list structure.

        Args:
            automations: Parsed list of automation dictionaries.
            source: Label for error messages (e.g. file path).

        Returns:
            True if all automations are structurally valid.
        """
        all_valid = True
        for i, automation in enumerate(automations):
            if not isinstance(automation, dict):
                self.errors.append(f"{source}: Automation {i} must be a dictionary")
                all_valid = False
                continue

            blueprint_valid = self._check_optional_blueprint(
                automation, f"{source}: Automation {i}"
            )
            if not blueprint_valid:
                all_valid = False
            elif "use_blueprint" not in automation:
                trigger_key = "trigger" if "trigger" in automation else "triggers"
                action_key = "action" if "action" in automation else "actions"
                if trigger_key not in automation or automation[trigger_key] is None:
                    self.errors.append(
                        f"{source}: Automation {i} missing 'trigger' or 'triggers'"
                    )
                    all_valid = False
                if action_key not in automation or automation[action_key] is None:
                    self.errors.append(
                        f"{source}: Automation {i} missing 'action' or 'actions'"
                    )
                    all_valid = False

            if "alias" not in automation:
                self.warnings.append(
                    f"{source}: Automation {i} missing 'alias' (recommended)"
                )

        return all_valid

    def check_scripts_structure(self, scripts: dict, source: str) -> bool:
        """Validate parsed scripts dict structure.

        Args:
            scripts: Parsed dict mapping script names to configs.
            source: Label for error messages (e.g. file path).

        Returns:
            True if all scripts are structurally valid.
        """
        all_valid = True
        for script_name, script_config in scripts.items():
            if not isinstance(script_config, dict):
                self.errors.append(
                    f"{source}: Script '{script_name}' must be a dictionary"
                )
                all_valid = False
                continue

            blueprint_valid = self._check_optional_blueprint(
                script_config, f"{source}: Script '{script_name}'"
            )
            if not blueprint_valid:
                all_valid = False
            elif "use_blueprint" not in script_config and (
                "sequence" not in script_config or script_config["sequence"] is None
            ):
                self.errors.append(
                    f"{source}: Script '{script_name}' missing required "
                    f"'sequence' or 'use_blueprint'"
                )
                all_valid = False

        return all_valid

    def _check_optional_blueprint(self, config: dict, item_label: str) -> bool:
        """Validate an optional ``use_blueprint`` mapping without extra checks."""
        if "use_blueprint" not in config:
            return True
        if isinstance(config["use_blueprint"], dict):
            return True
        self.errors.append(f"{item_label} 'use_blueprint' must be a dictionary")
        return False

    def validate_automations_structure(self, file_path: Path, data: Any) -> bool:
        """Validate automations.yaml structure."""
        if file_path.name != "automations.yaml":
            return True

        if data is None:
            return True  # Empty file is valid

        if not isinstance(data, list):
            self.errors.append(f"{file_path}: Automations must be a list")
            return False

        return self.check_automations_structure(data, str(file_path))

    def validate_scripts_structure(self, file_path: Path, data: Any) -> bool:
        """Validate scripts.yaml structure."""
        if file_path.name != "scripts.yaml":
            return True

        if data is None:
            return True  # Empty file is valid

        if not isinstance(data, dict):
            self.errors.append(f"{file_path}: Scripts must be a dictionary")
            return False

        return self.check_scripts_structure(data, str(file_path))

    def _validate(self) -> bool:
        """Validate all YAML files in the config directory."""
        yaml_files = self.get_yaml_files()
        if not yaml_files:
            self.warnings.append("No YAML files found in config directory")
            return True

        all_valid = True
        err_before = len(self.errors)
        for file_path, data in self._iter_yaml_payloads(yaml_files):
            configuration_valid = self.validate_configuration_structure(file_path, data)
            automations_valid = self.validate_automations_structure(file_path, data)
            scripts_valid = self.validate_scripts_structure(file_path, data)
            all_valid = (
                all_valid
                and configuration_valid
                and automations_valid
                and scripts_valid
            )
        all_valid = all_valid and len(self.errors) == err_before
        return all_valid


def main() -> int:
    """Run YAML syntax validation from command line."""
    return YAMLValidator.run_cli(
        "Validate YAML syntax for Home Assistant configuration files."
    )


if __name__ == "__main__":
    raise SystemExit(main())
