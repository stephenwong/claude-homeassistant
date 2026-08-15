#!/usr/bin/env python3
"""Duplicate automation ID detector for Home Assistant configuration files.

Checks automations.yaml for duplicate ``id`` values (which silently break
triggering and UI customization) and missing ``id`` fields.
Also checks scripts.yaml for duplicate top-level keys (M10b).
"""

import yaml

from tools.validators.base import HAYamlLoader, ValidatorBase


class _DupKeyLoader(HAYamlLoader):
    """SafeLoader that raises on duplicate mapping keys.

    Used to detect duplicate top-level keys in scripts.yaml where PyYAML's
    ``safe_load`` silently keeps the last entry on collision.
    """

    def construct_mapping(self, node, deep=False):
        seen: set = set()
        for key_node, _ in node.value:
            if key_node is not None and key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep) if key_node else None
            try:
                duplicate = key in seen
            except TypeError:
                continue
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    f"duplicate key: {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


class DuplicateIDValidator(ValidatorBase):
    """Validates that no two automations share the same ``id``."""

    validator_name = "Duplicate automation IDs"

    def file_deps(self) -> list[str]:
        return ["automations.yaml", "scripts.yaml"]

    def _check_automations(self, automations: list, source: str) -> bool:
        """Walk a parsed automations list; flag duplicates and missing IDs.

        Args:
            automations: Parsed list of automation dictionaries.
            source: Label for error messages (e.g. file path).

        Returns:
            True when there are no duplicate-ID errors. Missing IDs are
            warnings and do not change the return value.
        """
        all_valid = True
        seen: dict[str, list[int]] = {}
        missing = 0

        for i, automation in enumerate(automations):
            if not isinstance(automation, dict):
                self.errors.append(f"{source}: Automation {i} must be a dictionary")
                all_valid = False
                continue

            aid = automation.get("id")
            if aid is None:
                missing += 1
                alias = automation.get("alias", f"#{i}")
                self.warnings.append(f"{source}: Automation '{alias}' missing 'id'")
            else:
                seen.setdefault(str(aid), []).append(i)

        for aid, indices in seen.items():
            if len(indices) > 1:
                aliases = [automations[i].get("alias", "<no alias>") for i in indices]
                self.errors.append(
                    f"{source}: Duplicate automation id '{aid}' at positions {indices} "
                    f"(aliases: {aliases})"
                )
                all_valid = False

        if missing:
            self.info.append(f"{source}: {missing} automation(s) missing 'id'")

        return all_valid

    def _check_scripts_dup_keys(self) -> bool:
        """Check scripts.yaml for duplicate top-level keys.

        PyYAML ``safe_load`` silently dedupes, so we use a key-aware loader.
        """
        scripts_file = self.config_dir / "scripts.yaml"
        if not scripts_file.exists():
            return True
        try:
            with open(scripts_file, encoding="utf-8") as f:
                yaml.load(f, Loader=_DupKeyLoader)
        except yaml.constructor.ConstructorError as e:
            self.errors.append(f"{scripts_file}: {e.problem}")
            return False
        except (OSError, UnicodeDecodeError, TypeError, yaml.YAMLError) as e:
            self.errors.append(f"{scripts_file}: failed to parse: {e}")
            return False
        return True

    def _validate(self) -> bool:
        """Run duplicate-ID detection on automations.yaml and scripts.yaml."""
        automations_file = self.config_dir / "automations.yaml"
        ok_auto = True
        if automations_file.exists():
            data, ok = self.load_yaml_checked(automations_file)
            if not ok:
                ok_auto = False
            elif data is None:
                pass  # empty file
            elif not isinstance(data, list):
                self.errors.append(
                    f"{automations_file}: Automations must be a list, "
                    f"got {type(data).__name__}"
                )
                ok_auto = False
            else:
                ok_auto = self._check_automations(data, str(automations_file))

        ok_scripts = self._check_scripts_dup_keys()
        return ok_auto and ok_scripts


def main() -> int:
    """Run duplicate automation ID validation from command line."""
    return DuplicateIDValidator.run_cli("Detect duplicate and missing automation IDs.")


if __name__ == "__main__":
    raise SystemExit(main())
