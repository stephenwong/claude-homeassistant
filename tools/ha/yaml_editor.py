"""Safe round-trip YAML editing using ruamel.yaml.

Preserves comments, formatting, and key ordering through load/dump cycles.
Works with both list-based (automations.yaml, scenes.yaml) and dict-based
(scripts.yaml) Home Assistant YAML files.
"""

from collections.abc import Callable
from pathlib import Path
from typing import cast

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from tools.common import _atomic_replace


class ValidationError(Exception):
    """Raised when a validation check fails during atomic save."""


class YAMLEditor:
    """Load and dump YAML files preserving formatting, comments, and ordering."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._yaml = YAML()
        self._yaml.preserve_quotes = True
        self._yaml.indent(mapping=2, sequence=4, offset=2)
        self._data: list | dict | None = None
        self._loaded = False

    def load(self):
        """Load a YAML file, preserving structure.

        Returns:
            The parsed YAML data (list, dict, or None for empty).
            Raises FileNotFoundError if the path does not exist.
        """
        with self.path.open(encoding="utf-8") as f:
            self._data = self._yaml.load(f)
        self._loaded = True
        return self._data

    def _ensure_loaded(self):
        """Lazy-load the file if not already loaded.

        If the file does not exist, ``self._data`` stays ``None``.
        """
        if self._loaded:
            return
        try:
            self.load()
        except FileNotFoundError:
            # A missing file is a valid lazy state for new-file additions.
            # Mark the attempted load so repeated read-only operations do not
            # keep hitting the filesystem, while explicit load() still raises.
            self._loaded = True

    def save(self, validator: Callable[[Path], bool] | None = None) -> None:
        """Save in-memory state to the original file, atomically.

        Always writes via temp-file + ``os.replace`` so a crash mid-write never
        truncates the source file. If *validator* is provided it runs on the
        temp file first; failure raises ``ValidationError`` and the original
        is untouched.
        """
        if self._data is None:
            return
        self._atomic_save(validator or (lambda _p: True))

    def _atomic_save(self, validator: Callable[[Path], bool]) -> None:
        """Write to a temp file, validate, then atomically rename."""

        def validate_temp(tmp_path: Path) -> None:
            if not validator(tmp_path):
                raise ValidationError("Atomic save aborted: validation failed")

        _atomic_replace(
            self.path,
            lambda tmp_path: self.dump(self._data, tmp_path),
            validate=validate_temp,
            temp_prefix=f".{self.path.name}.",
            temp_suffix=".tmp",
        )

    def dump(self, data, path: Path | str) -> None:
        """Write YAML data to a file path."""
        path = Path(path)
        with path.open("w", encoding="utf-8") as f:
            self._yaml.dump(data, f)

    def dump_to(self, data, stream) -> None:
        """Write YAML data to an open stream (e.g. sys.stdout)."""
        self._yaml.dump(data, stream)

    # ------------------------------------------------------------------
    # Automation list helpers (automations.yaml and scenes.yaml)
    # ------------------------------------------------------------------

    def _require_shape(self, expected_type: type, operation: str) -> list | dict:
        """Return loaded data of *expected_type* or raise the stable shape error."""
        self._ensure_loaded()
        if not isinstance(self._data, expected_type):
            raise TypeError(
                f"Cannot {operation} on {self.path.name}: "
                f"expected a {expected_type.__name__}, got "
                f"{type(self._data).__name__}"
            )
        return self._data  # type: ignore[return-value]

    def _require_list(self, operation: str) -> list:
        """Return the loaded list or raise TypeError for another shape."""
        return cast(list, self._require_shape(list, operation))

    def _require_dict(self, operation: str) -> dict:
        """Return the loaded dict or raise TypeError for another shape."""
        return cast(dict, self._require_shape(dict, operation))

    def _find_automation(self, alias: str, operation: str) -> tuple[list, int]:
        """Return the automation list and alias index, or raise its old error."""
        data = self._require_list(operation)
        idx = self.find_automation(alias)
        if idx is None:
            raise ValueError(f"Automation with alias '{alias}' not found")
        return data, idx

    def _find_script(self, key: str, operation: str) -> dict:
        """Return a script mapping, or raise its old missing-key error."""
        data = self._require_dict(operation)
        if key not in data:
            raise ValueError(f"Script '{key}' not found")
        return data

    def find_automation(self, alias: str) -> int | None:
        """Return the index of an automation by alias, or None if not found."""
        if alias is None:
            return None
        self._ensure_loaded()
        if not isinstance(self._data, list):
            return None
        for i, item in enumerate(self._data):
            if isinstance(item, dict) and item.get("alias") == alias:
                return i
        return None

    def add_automation(self, automation: dict) -> None:
        """Append an automation dict to the list. Does NOT save.

        Raises TypeError if the loaded data is not a list.
        Raises ValueError if an automation with the same alias already exists.
        """
        self._ensure_loaded()
        if self._data is None:
            self._data = CommentedSeq()
        data = self._require_list("add automation")
        alias = automation.get("alias") if isinstance(automation, dict) else None
        if alias is not None and self.find_automation(alias) is not None:
            raise ValueError(f"Automation with alias '{alias}' already exists")
        data.append(automation)

    def add_script(self, key: str, script: dict) -> None:
        """Add a script entry to a dict-based file. Does NOT save.

        Raises TypeError if the loaded data is not a dict.
        Raises ValueError if the key already exists.
        """
        self._ensure_loaded()
        if self._data is None:
            self._data = CommentedMap()
        data = self._require_dict("add script")
        if key in data:
            raise ValueError(f"Script '{key}' already exists")
        data[key] = script

    def update_automation(self, alias: str, updates: dict) -> None:
        """Merge updates into an automation found by alias. Does NOT save.

        Raises TypeError if data is not a list.
        Raises ValueError if the alias is not found.
        Raises TypeError if the target entry is not a dict.
        """
        data, idx = self._find_automation(alias, "update automation")
        target = data[idx]
        self._update_mapping(target, updates, f"Automation '{alias}'")

    def update_script(self, key: str, updates: dict) -> None:
        """Merge updates into a script entry. Does NOT save.

        Raises TypeError if data is not a dict.
        Raises ValueError if the key is not found.
        Raises TypeError if the target entry is not a dict.
        """
        data = self._find_script(key, "update script")
        target = data[key]
        self._update_mapping(target, updates, f"Script '{key}'")

    @staticmethod
    def _update_mapping(target: object, updates: dict, label: str) -> None:
        """Merge *updates* into a mapping while preserving stable errors."""
        if not isinstance(target, dict):
            raise TypeError(f"{label} is not a dict (got {type(target).__name__})")
        target.update(updates)

    def remove_automation(self, alias: str) -> None:
        """Remove an automation by alias. Does NOT save.

        Raises TypeError if data is not a list.
        Raises ValueError if the alias is not found.
        """
        data, idx = self._find_automation(alias, "remove automation")
        data.pop(idx)

    def remove_script(self, key: str) -> None:
        """Remove a script entry. Does NOT save.

        Raises TypeError if data is not a dict.
        Raises ValueError if the key is not found.
        """
        data = self._find_script(key, "remove script")
        del data[key]
