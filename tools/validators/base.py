"""Shared validator base classes for Home Assistant configuration tools."""

import sys
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import yaml


class HAYamlLoader(yaml.SafeLoader):
    """Custom YAML loader that handles Home Assistant specific tags."""

    pass


_HA_TAGS = [
    "!include",
    "!include_dir_named",
    "!include_dir_merge_named",
    "!include_dir_merge_list",
    "!include_dir_list",
    "!input",
    "!secret",
]

_YAML_GLOB_PATTERNS = ("*.yaml", "*.yml")


def format_diagnostics(
    errors: Iterable[str],
    warnings: Iterable[str],
    info: Iterable[str] = (),
) -> str:
    """Format validator diagnostics in their compact, stable order.

    The compact representation is shared by the in-process validator runner
    and validators that provide their own summary output.  The rich headings
    and icons remain the responsibility of :meth:`ValidatorBase.print_results`.
    """
    lines = [*(f"ERROR: {entry}" for entry in errors)]
    lines.extend(f"WARN: {entry}" for entry in warnings)
    lines.extend(f"INFO: {entry}" for entry in info)
    return "\n".join(lines)


def _make_tag_constructor(tag: str):
    """Return a constructor that round-trips a HA YAML tag as a plain string."""

    def constructor(loader, node):
        return f"{tag} {loader.construct_scalar(node)}"

    return constructor


for _tag in _HA_TAGS:
    HAYamlLoader.add_constructor(_tag, _make_tag_constructor(_tag))


class ValidatorBase(ABC):
    """Base class for Home Assistant configuration validators."""

    validator_name = "Configuration"

    def __init__(
        self, config_dir: str = "config", quiet: bool = False, summary: bool = False
    ):
        """Initialize the validator with config directory."""
        self.config_dir = Path(config_dir).resolve()
        self.quiet = quiet
        self.summary = summary
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def file_deps(self) -> list[str]:
        """Glob patterns (relative to config_dir) for files this validator reads.

        Used by the caching layer to determine when a cached result is stale.
        Subclasses override this to declare their file dependencies.
        """
        return list(_YAML_GLOB_PATTERNS)

    def get_yaml_files(self) -> list[Path]:
        """Get all top-level YAML files in the config directory."""
        yaml_files: list[Path] = []
        for pattern in _YAML_GLOB_PATTERNS:
            yaml_files.extend(self.config_dir.glob(pattern))
        return sorted(yaml_files)

    def iter_yaml_payloads(self) -> Iterator[tuple[Path, Any]]:
        """Yield ``(path, data)`` for each non-secrets YAML file in ``config_dir``.

        Centralises the secrets-skip policy. Load failures (recorded to
        ``self.errors`` by ``load_yaml_checked``) and empty documents
        (``data is None``) are silently skipped — callers should snapshot
        ``len(self.errors)`` before iteration to detect load failures.
        """
        yield from self._iter_yaml_payloads(self.get_yaml_files())

    def _iter_yaml_payloads(
        self, yaml_files: Iterable[Path]
    ) -> Iterator[tuple[Path, Any]]:
        """Yield payloads from a precomputed YAML file list."""
        for fp in yaml_files:
            if fp.name == "secrets.yaml":
                continue
            data, ok = self.load_yaml_checked(fp)
            if not ok or data is None:
                continue
            yield fp, data

    def _try_live(
        self,
        info_prefix: str,
        fn: Callable[[Any], Any],
    ) -> Any | None:
        """Run *fn* against a live HA client, degrading to None on network failure.

        Constructs ``HAClient.from_env()``, passes it to *fn*, and returns
        the result. On ``HARequestError`` or ``OSError`` (DNS, socket, .env
        read, etc.), appends an ``f"{info_prefix} skipped: {e}"`` info line
        and returns None. Centralises the degrade-on-offline policy shared by
        services/templates/stale-sensors validators.
        """
        from tools.common import HARequestError
        from tools.ha.client import HAClient

        try:
            return fn(HAClient.from_env())
        except (HARequestError, OSError) as e:
            self.info.append(f"{info_prefix} skipped: {e}")
            return None

    def load_yaml_checked(self, file_path: Path) -> tuple[Any, bool]:
        """Load YAML, recording any error to ``self.errors``.

        Returns:
            (data, ok). On failure, data is None, ok is False, and an error
            message has been appended to self.errors. On success ok is True;
            data may be None for an empty document.
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                return yaml.load(f, Loader=HAYamlLoader), True
        except yaml.YAMLError as e:
            self.errors.append(f"{file_path}: YAML syntax error - {e}")
        except UnicodeDecodeError as e:
            self.errors.append(f"{file_path}: Encoding error - {e}")
        except OSError as e:
            self.errors.append(f"{file_path}: Could not read file - {e}")
        return None, False

    def validate_all(self) -> bool:
        """Template method: ensure config_dir exists, then run _validate()."""
        if not self.config_dir.is_dir():
            self.errors.append(f"Config directory {self.config_dir} does not exist")
            return False
        return self._validate()

    @abstractmethod
    def _validate(self) -> bool:
        """Subclass hook. Override instead of validate_all()."""

    def print_results(self):
        """Print validation results."""
        if self.quiet and not self.errors:
            return

        if self.info:
            print("INFO:", file=sys.stderr)
            for info in self.info:
                print(f"  \U0001f5c8\ufe0f  {info}", file=sys.stderr)
            print(file=sys.stderr)

        if self.errors:
            print("ERRORS:", file=sys.stderr)
            for error in self.errors:
                print(f"  \u274c {error}", file=sys.stderr)
            print(file=sys.stderr)

        if self.warnings:
            print("WARNINGS:", file=sys.stderr)
            for warning in self.warnings:
                print(f"  \u26a0\ufe0f  {warning}", file=sys.stderr)
            print(file=sys.stderr)

        if not self.errors and not self.warnings:
            print(f"\u2705 {self.validator_name} is valid!")
        elif not self.errors:
            print(f"\u2705 {self.validator_name} is valid (with warnings)")
        else:
            print(f"\u274c {self.validator_name} validation failed", file=sys.stderr)

    @classmethod
    def run_cli(
        cls,
        description: str,
        *,
        add_args=None,
        build_validator_kwargs=None,
    ) -> int:
        """Run a validator from CLI arguments and return an exit code.

        Encapsulates the standard validator-script contract: argparse setup,
        construct, ``validate_all()``, ``print_results()``, return ``0`` or ``1``.

        Args:
            description: ``argparse`` description shown in ``--help``.
            add_args: Optional callback ``f(parser) -> None`` to register extra
                arguments (e.g. ``--summary``, ``--quiet``) before ``parse_args``.
            build_validator_kwargs: Optional callback ``f(args) -> dict`` that
                translates parsed args into validator constructor kwargs
                (e.g. ``{"summary": resolve_summary(args), "quiet": args.quiet}``).
                When omitted, no kwargs are passed.

        Returns:
            ``0`` when validation passed, ``1`` when it failed.
        """
        import argparse

        parser = argparse.ArgumentParser(description=description)
        parser.add_argument(
            "config_dir",
            nargs="?",
            default="config",
            help="Path to the config directory (default: config)",
        )
        if add_args is not None:
            add_args(parser)
        args = parser.parse_args()
        kwargs = (
            build_validator_kwargs(args) if build_validator_kwargs is not None else {}
        )
        validator = cls(args.config_dir, **kwargs)
        is_valid = validator.validate_all()
        validator.print_results()
        return 0 if is_valid else 1
