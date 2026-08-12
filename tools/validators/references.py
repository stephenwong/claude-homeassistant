#!/usr/bin/env python3
"""Entity and device reference validator for Home Assistant configuration files.

Validates that all entity references in configuration files actually exist.
"""

import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

from tools.common import add_summary_args, resolve_summary
from tools.validators._storage import load_storage_registry
from tools.validators._templates import is_jinja_template
from tools.validators.base import ValidatorBase, format_diagnostics
from tools.validators.entity_definitions import EntityDefinitionExtractor

_TEMPLATE_PATTERNS = [
    re.compile(p)
    for p in [
        r"states\('([^']+)'\)",
        r'states\("([^"]+)"\)',
        r"states\.([a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*)",
        r"is_state\('([^']+)'",
        r'is_state\("([^"]+)"',
        r"state_attr\('([^']+)'",
        r'state_attr\("([^"]+)"',
    ]
]


class DomainSummary(TypedDict):
    """Type definition for domain summary dictionary."""

    count: int
    enabled: int
    disabled: int
    examples: list[str]


def _print_entity_summary(summary: dict[str, DomainSummary]) -> None:
    """Print the detailed available-entity summary to stderr."""
    if not summary:
        return
    print("AVAILABLE ENTITIES BY DOMAIN:", file=sys.stderr)
    for domain, info in sorted(summary.items()):
        enabled_count = info["enabled"]
        disabled_count = info["disabled"]
        print(
            f"  {domain}: {enabled_count} enabled, {disabled_count} disabled",
            file=sys.stderr,
        )
        if info["examples"]:
            print(f"    Examples: {', '.join(info['examples'])}", file=sys.stderr)
    print(file=sys.stderr)


class RegistrySpec(NamedTuple):
    """Configuration for loading and caching a HA ``.storage/`` registry.

    Bundles the six parameters ``ReferenceValidator._load_registry`` previously
    took positionally, so call sites read as named specs rather than
    positional argument lists.
    """

    filename: str
    list_key: str
    key_field: str
    cache_attr: str
    missing_bucket: str
    label: str


_ENTITY_REGISTRY_SPEC = RegistrySpec(
    filename="core.entity_registry",
    list_key="entities",
    key_field="entity_id",
    cache_attr="_entities",
    missing_bucket="errors",
    label="Entity registry",
)

_DEVICE_REGISTRY_SPEC = RegistrySpec(
    filename="core.device_registry",
    list_key="devices",
    key_field="id",
    cache_attr="_devices",
    missing_bucket="errors",
    label="Device registry",
)

_AREA_REGISTRY_SPEC = RegistrySpec(
    filename="core.area_registry",
    list_key="areas",
    key_field="id",
    cache_attr="_areas",
    missing_bucket="warnings",
    label="Area registry",
)


class ReferenceValidator(ValidatorBase):
    """Validates entity and device references in Home Assistant config."""

    validator_name = "Entity/device references"

    # Special keywords that are not entity IDs
    SPECIAL_KEYWORDS = {"all", "none"}

    def __init__(
        self, config_dir: str = "config", quiet: bool = False, summary: bool = False
    ):
        """Initialize the ReferenceValidator."""
        super().__init__(config_dir, quiet=quiet, summary=summary)
        self.storage_dir = self.config_dir / ".storage"

        # Cache for loaded registries
        self._entities: dict[str, Any] | None = None
        self._devices: dict[str, Any] | None = None
        self._areas: dict[str, Any] | None = None
        self._entity_registry_id_mapping: dict[str, str] | None = None
        self._entity_registry_id_mapping_source: dict[str, Any] | None = None
        self._registry_load_failures: set[str] = set()

        self._definitions = EntityDefinitionExtractor(
            self.config_dir, self.storage_dir, self.warnings, self.info
        )

    def file_deps(self) -> list[str]:
        return [
            "*.yaml",
            "*.yml",
            *[
                f".storage/{spec.filename}"
                for spec in (
                    _ENTITY_REGISTRY_SPEC,
                    _DEVICE_REGISTRY_SPEC,
                    _AREA_REGISTRY_SPEC,
                )
            ],
            ".storage/core.zone",
            ".storage/core.restore_state",
        ]

    def load_restore_state_entities(self) -> set[str]:
        """Load entity_ids from restore state storage (diagnostic only).

        Delegates to EntityDefinitionExtractor.
        """
        return self._definitions.load_restore_state_entities()

    def get_config_defined_entities(self) -> set[str]:
        """Extract entities defined in config files (not in entity registry).

        Delegates to EntityDefinitionExtractor.
        """
        return self._definitions.get_config_defined_entities()

    def _load_registry(self, spec: RegistrySpec) -> dict[str, Any]:
        """Load and cache a HA registry JSON file.

        Thin wrapper over :func:`tools.validators._storage.load_storage_registry`
        that adds instance caching and routes missing/parse failures to the
        appropriate diagnostics bucket.
        """
        cached = getattr(self, spec.cache_attr)
        if cached is not None:
            return cached
        if spec.cache_attr == _ENTITY_REGISTRY_SPEC.cache_attr:
            self._entity_registry_id_mapping = None
            self._entity_registry_id_mapping_source = None
        registry_file = self.storage_dir / spec.filename
        bucket = getattr(self, spec.missing_bucket)

        if not registry_file.exists():
            bucket.append(f"{spec.label} not found: {registry_file}")
            if spec.missing_bucket == "errors":
                self._registry_load_failures.add(spec.filename)
            setattr(self, spec.cache_attr, {})
            return {}

        try:
            result = load_storage_registry(
                registry_file, list_key=spec.list_key, key_field=spec.key_field
            )
        except (OSError, KeyError, TypeError, ValueError) as e:
            bucket.append(f"Failed to load {spec.label.lower()}: {e}")
            if spec.missing_bucket == "errors":
                self._registry_load_failures.add(spec.filename)
            setattr(self, spec.cache_attr, {})
            return {}

        setattr(self, spec.cache_attr, result)
        return result

    def load_entity_registry(self) -> dict[str, Any]:
        """Load and cache entity registry."""
        return self._load_registry(_ENTITY_REGISTRY_SPEC)

    def load_device_registry(self) -> dict[str, Any]:
        """Load and cache device registry."""
        return self._load_registry(_DEVICE_REGISTRY_SPEC)

    def load_area_registry(self) -> dict[str, Any]:
        """Load and cache area registry."""
        return self._load_registry(_AREA_REGISTRY_SPEC)

    def get_entity_registry_id_mapping(
        self, entities: dict[str, Any] | None = None
    ) -> dict[str, str]:
        """Lazily map entity-registry UUIDs to entity IDs."""
        registry = self._entities if entities is None else entities
        if (
            self._entity_registry_id_mapping is not None
            and self._entity_registry_id_mapping_source is registry
        ):
            return self._entity_registry_id_mapping

        if entities is None:
            entities = self.load_entity_registry()

        mapping = self._entity_registry_id_mapping
        if mapping is None or self._entity_registry_id_mapping_source is not entities:
            mapping = {
                entity_data["id"]: entity_data["entity_id"]
                for entity_data in entities.values()
                if isinstance(entity_data.get("id"), str)
                and isinstance(entity_data.get("entity_id"), str)
            }
            self._entity_registry_id_mapping = mapping
            self._entity_registry_id_mapping_source = entities
        return mapping

    _UUID_RE = re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    _UUID_NAKED_RE = re.compile(r"^[0-9a-fA-F]{32}$")

    def is_uuid_format(self, value: str) -> bool:
        """Check if a string is a dashed or naked 32-hex-character UUID."""
        return bool(self._UUID_RE.match(value) or self._UUID_NAKED_RE.match(value))

    def is_template(self, value: str) -> bool:
        """Check if value is a Jinja2 template expression."""
        return is_jinja_template(value)

    def _should_skip_id_validation(self, value: str) -> bool:
        """True if value is an HA tag or Jinja template — skip device/area validation.

        Distinct from ``should_skip_entity_validation``: entity refs also
        skip UUIDs and special keywords ("all", "none"), but device/area
        IDs legitimately use UUID format and don't have keyword aliases.
        """
        return value.startswith("!") or self.is_template(value)

    def should_skip_entity_validation(self, value: str) -> bool:
        """Check if entity reference should be skipped during validation."""
        return (
            self._should_skip_id_validation(value)
            or self.is_uuid_format(value)
            or value in self.SPECIAL_KEYWORDS
        )

    @staticmethod
    def _collect_string_values(value: Any, *, skip: Callable[[str], bool]) -> set[str]:
        """Flatten *value* (str/list/dict-keys) into a set of strings.

        - str: ``{value}`` if not skipped.
        - list: union over items that are non-skipped strings.
        - dict: union over keys that are non-skipped strings.
        """
        out: set[str] = set()
        if isinstance(value, str):
            if not skip(value):
                out.add(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and not skip(item):
                    out.add(item)
        elif isinstance(value, dict):
            for key in value:
                if isinstance(key, str) and not skip(key):
                    out.add(key)
        return out

    def extract_entity_references(self, data: Any) -> set[str]:
        """Extract entity references from configuration data."""
        return self._walk_references(
            data,
            keys={"entity_id", "entity_ids", "entities"},
            skip=self.should_skip_entity_validation,
            template_callback=self.extract_entities_from_template,
            skip_keys={"device_id", "device_ids", "area_id", "area_ids"},
        )

    def extract_entities_from_template(self, template: str) -> set[str]:
        """Extract entity references from Jinja2 templates."""
        entities = set()

        for pattern in _TEMPLATE_PATTERNS:
            for match in pattern.findall(template):
                if "." in match and len(match.split(".")) == 2:
                    entities.add(match)

        return entities

    def _extract_id_references(
        self,
        data: Any,
        keys: set[str],
        *,
        skip: Callable[[str], bool],
    ) -> set[str]:
        """Extract references for any of *keys* (e.g. {'device_id', 'device_ids'})."""
        return self._walk_references(data, keys=keys, skip=skip)

    def _walk_references(
        self,
        data: Any,
        *,
        keys: set[str],
        skip: Callable[[str], bool],
        template_callback: Callable[[str], set[str]] | None = None,
        skip_keys: set[str] | None = None,
    ) -> set[str]:
        """Recursively collect string references from matching configuration keys."""
        refs: set[str] = set()
        skip_keys = skip_keys or set()
        if isinstance(data, dict):
            children = data.items()
        elif isinstance(data, list):
            children = ((None, item) for item in data)
        else:
            return refs

        for key, value in children:
            if key in keys:
                refs.update(self._collect_string_values(value, skip=skip))
            elif key in skip_keys:
                continue
            elif (
                template_callback is not None
                and isinstance(value, str)
                and is_jinja_template(value)
            ):
                refs.update(template_callback(value))
            else:
                refs.update(
                    self._walk_references(
                        value,
                        keys=keys,
                        skip=skip,
                        template_callback=template_callback,
                        skip_keys=skip_keys,
                    )
                )
        return refs

    def extract_device_references(self, data: Any) -> set[str]:
        """Extract device references from configuration data."""
        return self._extract_id_references(
            data,
            {"device_id", "device_ids"},
            skip=self._should_skip_id_validation,
        )

    def extract_area_references(self, data: Any) -> set[str]:
        """Extract area references from configuration data."""
        return self._extract_id_references(
            data,
            {"area_id", "area_ids"},
            skip=self._should_skip_id_validation,
        )

    def extract_entity_registry_ids(self, data: Any) -> set[str]:
        """Extract dashed or naked entity-registry UUID references."""
        return self._extract_id_references(
            data,
            {"entity_id", "entity_ids"},
            skip=lambda s: not self.is_uuid_format(s),
        )

    @staticmethod
    def _is_disabled(entity_data: dict) -> bool:
        """True when the entity/device is disabled (``disabled_by`` is set)."""
        return entity_data.get("disabled_by") is not None

    @staticmethod
    def _is_hidden(entity_data: dict) -> bool:
        """True when the entity is hidden (``hidden_by`` is set)."""
        return entity_data.get("hidden_by") is not None

    def _check_entity_refs(
        self,
        file_path: Path,
        entity_refs: set[str],
        entities: dict[str, Any],
        config_entities: set[str],
        restore_entities: set[str],
    ) -> bool:
        """Validate normal-format entity references.

        Skips UUID-format refs (handled by :meth:`_check_registry_uuid_refs`).
        Unknown entities are errors; disabled entities are warnings; hidden
        entities are info; entities found only in restore state get a
        diagnostic warning but still fail validation.

        Returns:
            ``True`` when every entity ref resolves (registry, config, or
            restore state-with-warning); ``False`` when any unknown entity
            triggers an error.
        """
        all_valid = True
        for entity_id in sorted(entity_refs):
            if self.is_uuid_format(entity_id):
                continue

            if entity_id in entities:
                if self._is_disabled(entities[entity_id]):
                    self.warnings.append(
                        f"{file_path}: References disabled entity '{entity_id}'"
                    )
                if self._is_hidden(entities[entity_id]):
                    self.info.append(
                        f"{file_path}: References hidden entity '{entity_id}'"
                    )
                continue

            if entity_id in config_entities:
                continue

            if entity_id in restore_entities:
                self.warnings.append(
                    f"{file_path}: Entity '{entity_id}' not in registry "
                    "but found in restore state"
                )

            self.errors.append(f"{file_path}: Unknown entity '{entity_id}'")
            all_valid = False
        return all_valid

    def _check_registry_uuid_refs(
        self,
        file_path: Path,
        registry_ids: set[str],
        entity_id_mapping: dict[str, str],
        entities: dict[str, Any],
    ) -> bool:
        """Validate entity-registry UUID references.

        Each UUID must map to a known entity via *entity_id_mapping*. When
        the mapped entity is disabled, a warning is appended (but the check
        still passes — the UUID is valid).

        Returns:
            ``True`` when every UUID resolves; ``False`` when any unknown
            UUID triggers an error.
        """
        all_valid = True
        for registry_id in sorted(registry_ids):
            if registry_id not in entity_id_mapping:
                self.errors.append(
                    f"{file_path}: Unknown entity registry ID '{registry_id}'"
                )
                all_valid = False
                continue

            actual_entity_id = entity_id_mapping[registry_id]
            entity_data = entities.get(actual_entity_id, {})
            if self._is_disabled(entity_data):
                self.warnings.append(
                    f"{file_path}: Entity registry ID '{registry_id}' "
                    f"references disabled entity '{actual_entity_id}'"
                )
        return all_valid

    def _check_device_refs(
        self,
        file_path: Path,
        device_refs: set[str],
        devices: dict[str, Any],
    ) -> bool:
        """Validate device references.

        Unknown devices are errors; disabled devices are warnings (but still
        pass — the device exists).

        Returns:
            ``True`` when every device ref resolves; ``False`` when any
            unknown device triggers an error.
        """
        all_valid = True
        for device_id in sorted(device_refs):
            if device_id not in devices:
                self.errors.append(f"{file_path}: Unknown device '{device_id}'")
                all_valid = False
                continue

            if self._is_disabled(devices[device_id]):
                self.warnings.append(
                    f"{file_path}: References disabled device '{device_id}'"
                )
        return all_valid

    def _check_area_refs(
        self,
        file_path: Path,
        area_refs: set[str],
        areas: dict[str, Any],
    ) -> None:
        """Validate area references.

        Unknown areas are warnings only (never errors) — area registry is
        advisory and may be incomplete.

        Returns:
            ``None`` — area checks never fail validation.
        """
        for area_id in sorted(area_refs):
            if area_id not in areas:
                self.warnings.append(f"{file_path}: Unknown area '{area_id}'")

    def validate_file_references(self, file_path: Path) -> bool:
        """Validate all references in a single file.

        Loads YAML, extracts entity/device/area/UUID references, then
        delegates each ref-type check to its private helper. Returns
        ``False`` if any check found an unknown entity/UUID/device; area-ref
        unknowns are warnings only and never fail validation.
        """
        if file_path.name == "secrets.yaml":
            return True

        data, ok = self.load_yaml_checked(file_path)
        if not ok:
            return False

        if data is None:
            return True

        entity_refs = self.extract_entity_references(data)
        device_refs = self.extract_device_references(data)
        area_refs = self.extract_area_references(data)
        entity_registry_ids = self.extract_entity_registry_ids(data)

        entities = self.load_entity_registry()
        devices = self.load_device_registry()
        areas = self.load_area_registry()
        entity_id_mapping = (
            self.get_entity_registry_id_mapping(entities) if entity_registry_ids else {}
        )
        config_entities = self.get_config_defined_entities()
        restore_entities = self.load_restore_state_entities()

        entity_ok = self._check_entity_refs(
            file_path,
            entity_refs,
            entities,
            config_entities,
            restore_entities,
        )
        uuid_ok = self._check_registry_uuid_refs(
            file_path,
            entity_registry_ids,
            entity_id_mapping,
            entities,
        )
        device_ok = self._check_device_refs(file_path, device_refs, devices)
        self._check_area_refs(file_path, area_refs, areas)

        registry_ok = not self._registry_load_failures
        return entity_ok and uuid_ok and device_ok and registry_ok

    def _validate(self) -> bool:
        """Validate all references in the config directory."""
        yaml_files = self.get_yaml_files()
        if not yaml_files:
            self.warnings.append("No YAML files found in config directory")
            return True

        all_valid = True

        for file_path in yaml_files:
            if not self.validate_file_references(file_path):
                all_valid = False

        return all_valid

    def get_entity_summary(self) -> dict[str, DomainSummary]:
        """Get summary of available entities by domain."""
        entities = self.load_entity_registry()

        summary: dict[str, DomainSummary] = {}
        for entity_id, entity_data in entities.items():
            domain = entity_id.split(".")[0]
            if domain not in summary:
                summary[domain] = {
                    "count": 0,
                    "enabled": 0,
                    "disabled": 0,
                    "examples": [],
                }

            summary[domain]["count"] += 1
            if self._is_disabled(entity_data):
                summary[domain]["disabled"] += 1
            else:
                summary[domain]["enabled"] += 1

            # Add some examples
            if len(summary[domain]["examples"]) < 3:
                summary[domain]["examples"].append(entity_id)

        return summary

    def print_results(self):
        """Print validation results with entity summary."""
        if self.quiet and not self.errors:
            return

        if self.summary:
            if self.errors:
                status = "FAIL Entity/device references"
            elif self.warnings:
                status = "PASS Entity/device references (with warnings)"
            else:
                status = "PASS Entity/device references"
            print(status)
            diagnostics = format_diagnostics(self.errors, self.warnings)
            if diagnostics:
                for line in diagnostics.splitlines():
                    print(f"  {line}", file=sys.stderr)
            return

        super().print_results()

        # Print entity summary
        _print_entity_summary(self.get_entity_summary())


def _add_reference_args(parser):
    add_summary_args(parser)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress output on success",
    )


def _reference_kwargs(args):
    return {"summary": resolve_summary(args), "quiet": args.quiet}


def main() -> int:
    """Run entity and device reference validation from command line."""
    return ReferenceValidator.run_cli(
        "Validate entity and device references in Home Assistant config.",
        add_args=_add_reference_args,
        build_validator_kwargs=_reference_kwargs,
    )


if __name__ == "__main__":
    raise SystemExit(main())
