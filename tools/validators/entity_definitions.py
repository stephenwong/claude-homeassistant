#!/usr/bin/env python3
"""Entity definition extraction from Home Assistant configuration files.

Extracts which entities are defined by the config itself (groups, input_helpers,
template entities, automations, scripts, scenes, zones) vs. the entity registry.
"""

import json
import re
from pathlib import Path
from typing import Any

import yaml

from tools.validators._storage import _load_storage_data
from tools.validators.base import HAYamlLoader

_OBJECT_ID_RE = re.compile(r"^[a-z0-9_]+$")

_INPUT_HELPER_DOMAINS = (
    "input_boolean",
    "input_number",
    "input_text",
    "input_select",
    "input_datetime",
    "input_button",
    "timer",
    "counter",
    "schedule",
)

_PLATFORM_SENSOR_DOMAINS = ("sensor", "binary_sensor")

_TEMPLATE_ENTITY_DOMAINS = (
    "sensor",
    "binary_sensor",
    "switch",
    "light",
    "number",
    "select",
    "button",
    "alarm_control_panel",
    "cover",
    "device_tracker",
    "event",
    "fan",
    "image",
    "lock",
    "update",
    "vacuum",
    "weather",
)


class EntityDefinitionExtractor:
    """Extracts entity definitions from HA configuration and storage files.

    Answers: "which entities does the config DEFINE?"

    Shares ``warnings`` and ``info`` lists by reference with the caller
    (typically ReferenceValidator), so appends land on the validator's lists.
    """

    BUILTIN_ENTITIES = {"sun.sun", "zone.home"}

    def __init__(
        self,
        config_dir: Path,
        storage_dir: Path,
        warnings: list[str],
        info: list[str],
    ):
        self.config_dir = config_dir.resolve()
        self.storage_dir = storage_dir.resolve()
        self.warnings = warnings
        self.info = info
        self._config_defined_entities: set[str] | None = None
        self._restore_entities: set[str] | None = None

    # ------------------------------------------------------------------
    # Slugify / validation helpers
    # ------------------------------------------------------------------

    @classmethod
    def _slugify_object_id(cls, value: str) -> str:
        """Best-effort HA-like slugify for deriving object_ids from names."""
        slug = value.strip().lower()
        slug = re.sub(r"[^a-z0-9_]+", "_", slug)
        slug = re.sub(r"_+", "_", slug)
        return slug.strip("_")

    @classmethod
    def _make_entity_id(
        cls,
        domain: str,
        name: str,
        *,
        explicit_id: str | None = None,
    ) -> str | None:
        """Build an entity_id by slugifying *name* (or *explicit_id*)
        and prefixing *domain*.

        Args:
            domain: HA entity domain (e.g. ``"automation"``, ``"scene"``).
            name: Human-readable name to slugify.
            explicit_id: Optional override; when truthy, slugified in place of
                *name* (used for automation ``id`` fallback).

        Returns:
            ``f"{domain}.{slug}"`` when slugify yields a non-empty slug,
            else ``None``.
        """
        source = explicit_id if explicit_id else name
        object_id = cls._slugify_object_id(str(source))
        return f"{domain}.{object_id}" if object_id else None

    @classmethod
    def _is_valid_object_id(cls, value: str) -> bool:
        """Check if a string is a valid HA object ID."""
        return bool(_OBJECT_ID_RE.fullmatch(value))

    @classmethod
    def _is_valid_entity_id(cls, value: str) -> bool:
        """Check if a string is a valid HA entity ID (domain.object_id)."""
        if "." not in value:
            return False
        domain, object_id = value.split(".", 1)
        return (
            bool(domain)
            and cls._is_valid_object_id(domain)
            and cls._is_valid_object_id(object_id)
        )

    # ------------------------------------------------------------------
    # Restore state loading
    # ------------------------------------------------------------------

    def load_restore_state_entities(self) -> set[str]:
        """Load entity_ids from restore state storage (diagnostic only).

        Restore state can contain stale entries and is not authoritative.
        Used only for diagnostic warnings.
        """
        if self._restore_entities is not None:
            return self._restore_entities

        restore_file = self.storage_dir / "core.restore_state"
        if not restore_file.exists():
            self._restore_entities = set()
            return self._restore_entities

        try:
            items = _load_storage_data(restore_file)
            if not isinstance(items, list):
                raise ValueError("restore state 'data' must be a list")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            self.warnings.append(f"Failed to load restore state: {e}")
            self._restore_entities = set()
            return self._restore_entities

        entities: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            state = item.get("state")
            if not isinstance(state, dict):
                continue
            entity_id = state.get("entity_id")
            if isinstance(entity_id, str) and self._is_valid_entity_id(entity_id):
                entities.add(entity_id)

        self._restore_entities = entities
        return self._restore_entities

    # ------------------------------------------------------------------
    # Config-defined entity extraction
    # ------------------------------------------------------------------

    def get_config_defined_entities(self) -> set[str]:
        """Extract entities defined in config files (not in entity registry)."""
        if self._config_defined_entities is not None:
            return self._config_defined_entities

        entities: set[str] = set()
        entities.update(self.BUILTIN_ENTITIES)

        # Load configuration.yaml once; share with both extraction methods that
        # need it.
        config_data = self._load_config_yaml()

        # _extract_from_configuration operates on already-loaded dict data (no I/O).
        # Run it inline instead of submitting to the thread pool.
        config_entities = self._extract_from_configuration(config_data)

        # These small extractions append diagnostics to shared lists. Keep
        # their fixed order so completion cannot reorder output.
        automation_entities = self._extract_automation_entities(config_data)
        script_entities = self._extract_script_entities(config_data)
        scene_entities = self._extract_scene_entities(config_data)
        zone_entities = self._extract_zone_entities(config_data)

        entities.update(config_entities)
        entities.update(automation_entities)
        entities.update(script_entities)
        entities.update(scene_entities)
        entities.update(zone_entities)

        self.info.append(
            "Config-defined entities: "
            f"{len(entities)} total "
            f"(builtin={len(self.BUILTIN_ENTITIES)}, "
            f"configuration={len(config_entities)}, "
            f"automations={len(automation_entities)}, "
            f"scripts={len(script_entities)}, "
            f"scenes={len(scene_entities)}, "
            f"zones={len(zone_entities)})"
        )

        self._config_defined_entities = entities
        return self._config_defined_entities

    def _load_yaml_glob(self, target: Path) -> list[tuple[Path, Any]]:
        """Load every ``*.yaml`` under *target* (sorted),
        returning ``(path, data)`` pairs."""
        loaded: list[tuple[Path, Any]] = []
        for f in sorted(target.glob("*.yaml")):
            try:
                f.resolve().relative_to(self.config_dir)
            except ValueError:
                self.warnings.append(
                    f"{f}: Include path is outside the config directory"
                )
                continue
            with open(f, encoding="utf-8") as fh:
                loaded.append((f, yaml.load(fh, Loader=HAYamlLoader)))
        return loaded

    def _resolve_include(self, tag_value: object):
        """Resolve a ``!include* <path>`` string to merged YAML data, or None.

        Supports the patterns HA users actually split with:
        ``!include``, ``!include_dir_list``, ``!include_dir_merge_list``,
        ``!include_dir_named``, ``!include_dir_merge_named``.
        Returns None if the string is not an include tag or the path is missing.
        """
        if not isinstance(tag_value, str) or not tag_value.startswith("!include"):
            return None
        parts = tag_value.split(maxsplit=1)
        if len(parts) != 2:
            return None
        tag, raw = parts[0], parts[1].strip().rstrip("/")
        target = (self.config_dir / raw).resolve()
        try:
            target.relative_to(self.config_dir)
        except ValueError:
            self.warnings.append(
                f"{target}: Include path is outside the config directory"
            )
            return None
        try:
            if tag in ("!include",):
                if target.is_file():
                    with open(target, encoding="utf-8") as f:
                        return yaml.load(f, Loader=HAYamlLoader)
                self._record_extraction_warning(target, FileNotFoundError(str(target)))
                return None
            if not target.is_dir():
                self._record_extraction_warning(target, FileNotFoundError(str(target)))
                return None
            if tag == "!include_dir_list":
                return [data for _path, data in self._load_yaml_glob(target)]
            if tag == "!include_dir_merge_list":
                items: list = []
                for _path, data in self._load_yaml_glob(target):
                    if isinstance(data, list):
                        items.extend(data)
                return items
            if tag == "!include_dir_named":
                return {path.stem: data for path, data in self._load_yaml_glob(target)}
            if tag == "!include_dir_merge_named":
                merged: dict = {}
                for _path, data in self._load_yaml_glob(target):
                    if isinstance(data, dict):
                        merged.update(data)
                return merged
        except (OSError, yaml.YAMLError, TypeError, ValueError) as e:
            self._record_extraction_warning(target, e)
            return None
        return None

    def _load_yaml_file(self, file: Path) -> Any | None:
        """Open+parse a YAML file, recording extraction warnings on parse failure.

        Returns the parsed YAML data on success, or ``None`` on failure (after
        appending a warning via :meth:`_record_extraction_warning`). Callers
        handle shape validation (``isinstance(data, list)`` etc.) and the
        missing-file policy (caller-side ``if file.exists():`` guard) themselves
        — this helper does NOT check existence, so a missing file raises
        ``FileNotFoundError`` (a subclass of ``OSError``) and gets a warning.
        """
        try:
            with open(file, encoding="utf-8") as f:
                return yaml.load(f, Loader=HAYamlLoader)
        except (OSError, yaml.YAMLError, TypeError, ValueError) as e:
            self._record_extraction_warning(file, e)
            return None

    def _load_config_yaml(self) -> dict | None:
        """Load and parse configuration.yaml once; shared across extraction methods."""
        config_file = self.config_dir / "configuration.yaml"
        if not config_file.exists():
            return None
        data = self._load_yaml_file(config_file)
        return data if isinstance(data, dict) else None

    def _record_extraction_warning(self, source: Path, error: Exception) -> None:
        """Record entity extraction errors without hiding them."""
        self.warnings.append(
            f"{source}: Failed to extract entity definitions - {error}"
        )

    def _extract_from_configuration(self, config_data: dict | None) -> set[str]:
        """Extract entities defined in configuration.yaml from pre-loaded data."""
        entities: set[str] = set()

        if not isinstance(config_data, dict):
            return entities

        # Extract group entities
        if "group" in config_data and isinstance(config_data["group"], dict):
            for group_name in config_data["group"]:
                if isinstance(group_name, str) and self._is_valid_object_id(group_name):
                    entities.add(f"group.{group_name}")

        # Extract input helpers
        for input_type in _INPUT_HELPER_DOMAINS:
            if input_type in config_data and isinstance(config_data[input_type], dict):
                for name in config_data[input_type]:
                    if isinstance(name, str) and self._is_valid_object_id(name):
                        entities.add(f"{input_type}.{name}")

        # Extract template entities
        if "template" in config_data:
            template_data = config_data["template"]
            if isinstance(template_data, list):
                for item in template_data:
                    entities.update(self._extract_template_entities(item))
            elif isinstance(template_data, dict):
                entities.update(self._extract_template_entities(template_data))

        # Extract platform-based sensors/binary_sensors
        for sensor_type in _PLATFORM_SENSOR_DOMAINS:
            if sensor_type in config_data:
                sensor_data = config_data[sensor_type]
                if isinstance(sensor_data, list):
                    for item in sensor_data:
                        if isinstance(item, dict) and (
                            "platform" in item and item["platform"] == "template"
                        ):
                            sensors = item.get("sensors") or {}
                            for name in sensors:
                                if isinstance(name, str) and self._is_valid_object_id(
                                    name
                                ):
                                    entities.add(f"{sensor_type}.{name}")

        # HA packages may be top-level or nested under homeassistant.
        packages = config_data.get("packages")
        if not isinstance(packages, dict):
            homeassistant = config_data.get("homeassistant")
            if isinstance(homeassistant, dict):
                packages = homeassistant.get("packages")
        if isinstance(packages, str):
            packages = self._resolve_include(packages)
        if isinstance(packages, dict):
            for pkg in packages.values():
                if isinstance(pkg, dict):
                    entities.update(self._extract_from_configuration(pkg))
                    if "automation" in pkg:
                        entities.update(self._extract_automation_entities(pkg))
                    if "script" in pkg:
                        entities.update(self._extract_script_entities(pkg))
                    if "scene" in pkg:
                        entities.update(self._extract_scene_entities(pkg))

        return entities

    def _extract_template_entities(self, template_config: Any) -> set[str]:
        """Extract entity names from template configuration.

        Per HA docs: default_entity_id controls automatic entity_id generation.
        unique_id exists to allow changing entity_id in the UI - it's NOT the
        entity_id.
        See: https://www.home-assistant.io/integrations/template/
        """
        entities: set[str] = set()

        if not isinstance(template_config, dict):
            return entities

        for entity_type in _TEMPLATE_ENTITY_DOMAINS:
            if entity_type in template_config:
                type_data = template_config[entity_type]
                if isinstance(type_data, list):
                    items = type_data
                elif isinstance(type_data, dict):
                    items = [type_data]
                else:
                    items = []
                for item in items:
                    if isinstance(item, dict):
                        default_entity_id = item.get("default_entity_id")
                        name = item.get("name", "")
                        if default_entity_id:
                            default_entity_id = str(default_entity_id)
                            if "." in default_entity_id:
                                if self._is_valid_entity_id(default_entity_id):
                                    entities.add(default_entity_id)
                            elif self._is_valid_object_id(default_entity_id):
                                entities.add(f"{entity_type}.{default_entity_id}")
                        elif name:
                            object_id = self._slugify_object_id(str(name))
                            if object_id:
                                entities.add(f"{entity_type}.{object_id}")

        return entities

    def _resolve_config_or_sidecar(
        self,
        config_data: dict | None,
        config_key: str,
        file_name: str,
    ) -> Any:
        """Resolve a config section from !include, inline value, or sidecar file."""
        if isinstance(config_data, dict) and config_key in config_data:
            include_value = config_data.get(config_key)
            if isinstance(include_value, str) and include_value.startswith("!include"):
                return self._resolve_include(include_value)
            return include_value

        sidecar = self.config_dir / file_name
        if sidecar.exists():
            return self._load_yaml_file(sidecar)
        return None

    def _extract_named_entities(
        self,
        config_data: dict | None,
        *,
        domain: str,
        config_key: str,
        name_field: str,
        file_name: str,
        allow_id_fallback: bool = False,
    ) -> set[str]:
        """Extract entities defined in list-of-dicts configuration files.

        Centralises the resolve-include → file-fallback loop shared by
        automation/scene extraction. When *allow_id_fallback* is True
        (automation-only), an item with empty *name_field* but a non-empty
        ``id`` falls back to slugifying the id.
        """
        raw = self._resolve_config_or_sidecar(config_data, config_key, file_name)
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            items = [raw]
        else:
            items = []

        entities: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get(name_field, "")
            if name:
                entity_id = self._make_entity_id(domain, name)
                if entity_id:
                    entities.add(entity_id)
            elif allow_id_fallback and item.get("id"):
                entity_id = self._make_entity_id(domain, "", explicit_id=item.get("id"))
                if entity_id:
                    entities.add(entity_id)
        return entities

    def _extract_automation_entities(self, config_data: dict | None = None) -> set[str]:
        """Extract automation entities from automations.yaml."""
        return self._extract_named_entities(
            config_data,
            domain="automation",
            config_key="automation",
            name_field="alias",
            file_name="automations.yaml",
            allow_id_fallback=True,
        )

    def _extract_dictionary_entities(
        self,
        config_data: dict | None,
        *,
        domain: str,
        config_key: str,
        file_name: str,
    ) -> set[str]:
        """Extract entities whose configuration is keyed by object_id."""
        raw = self._resolve_config_or_sidecar(config_data, config_key, file_name)
        if not isinstance(raw, dict):
            return set()
        return {
            f"{domain}.{object_id}"
            for object_id in raw
            if isinstance(object_id, str) and self._is_valid_object_id(object_id)
        }

    def _extract_script_entities(self, config_data: dict | None = None) -> set[str]:
        """Extract script entities from scripts.yaml."""
        return self._extract_dictionary_entities(
            config_data,
            domain="script",
            config_key="script",
            file_name="scripts.yaml",
        )

    def _extract_scene_entities(self, config_data: dict | None = None) -> set[str]:
        """Extract scene entities from scenes.yaml."""
        return self._extract_named_entities(
            config_data,
            domain="scene",
            config_key="scene",
            name_field="name",
            file_name="scenes.yaml",
            allow_id_fallback=False,
        )

    def _extract_zone_entities(self, config_data: dict | None) -> set[str]:
        """Extract zone entities from configuration and storage.

        Zones can be defined in configuration.yaml or via the UI (.storage/core.zone).
        zone.home is handled separately as a built-in entity.
        """
        entities: set[str] = set()

        # Extract from pre-loaded configuration.yaml data
        if isinstance(config_data, dict) and "zone" in config_data:
            zone_data = config_data["zone"]
            if isinstance(zone_data, list):
                for zone in zone_data:
                    if isinstance(zone, dict):
                        name = zone.get("name", "")
                        if name:
                            entity_id = self._make_entity_id("zone", name)
                            if entity_id:
                                entities.add(entity_id)

        # Extract from storage (UI-configured zones)
        zone_storage = self.storage_dir / "core.zone"
        if zone_storage.exists():
            try:
                envelope = _load_storage_data(zone_storage)
                if not isinstance(envelope, dict):
                    raise ValueError("zone storage 'data' must be an object")
                items = envelope.get("items", [])
                if not isinstance(items, list):
                    raise ValueError("zone storage 'items' must be a list")
                for item in items:
                    if isinstance(item, dict):
                        name = item.get("name", "")
                        if isinstance(name, str) and name:
                            entity_id = self._make_entity_id("zone", name)
                            if entity_id:
                                entities.add(entity_id)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
                self._record_extraction_warning(zone_storage, e)

        return entities
