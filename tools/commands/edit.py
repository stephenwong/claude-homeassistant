"""``edit`` subcommand: safe round-trip YAML editing for HA config files.

Supports automations.yaml (list) and scripts.yaml (dict) with --show, --set, --add,
and --remove operations.  All writes use atomic save via YAMLEditor.
"""

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from ruamel.yaml import YAML, YAMLError

from tools.common import (
    add_config_dir_arg,
    add_summary_args,
    fail_stderr,
    resolve_summary,
)
from tools.ha.yaml_editor import YAMLEditor

_SAFE_YAML = YAML(typ="safe")
_ALLOWED_FILES = frozenset({"automations.yaml", "scripts.yaml"})


class _ShapeKind(StrEnum):
    """Loaded YAML shape, including the states that need a filename default."""

    LIST = "list"
    DICT = "dict"
    EMPTY = "empty"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"


type _EditableShape = Literal[_ShapeKind.LIST, _ShapeKind.DICT]


@dataclass(frozen=True, slots=True)
class _FileShape:
    """Source shape and the list/dict shape used by edit operations."""

    kind: _ShapeKind
    editable: _EditableShape | None


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Wire the ``edit`` subparser."""
    parser = subparsers.add_parser(
        "edit",
        help="Edit automations/scripts with safe round-trip YAML.",
        description="View, add, update, or remove automations/scripts.",
    )
    parser.add_argument(
        "file",
        help="Target file basename (automations, scripts).",
    )
    parser.add_argument(
        "alias",
        nargs="?",
        default=None,
        help="Automation alias or script name to operate on.",
    )
    add_config_dir_arg(parser, help="Path to the config directory (default: config)")
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display the automation/script (or list all aliases if no alias).",
    )
    parser.add_argument(
        "--set",
        nargs="+",
        metavar="KEY=VALUE",
        help="Set top-level KEY=VALUE pairs (at least one required). "
        "Values are parsed as YAML.",
    )
    parser.add_argument(
        "--add",
        metavar="JSON",
        help="Add a new entry from a JSON string.",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove the entry identified by alias.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress success messages.",
    )
    add_summary_args(parser)
    parser.set_defaults(func=run)


def _resolve_target(config_dir: Path, file_basename: str) -> Path:
    """Resolve the target file path, guarding against path traversal."""
    if "." not in file_basename:
        file_basename += ".yaml"
    target = (config_dir / file_basename).resolve()
    try:
        target.relative_to(config_dir.resolve())
    except ValueError:
        raise ValueError(f"'{file_basename}' must be inside config directory") from None
    if target.name not in _ALLOWED_FILES:
        raise ValueError("file must be automations or scripts")
    return target


def _check_exclusive(args: argparse.Namespace) -> str | None:
    """Return an error message if mutually exclusive flags are combined."""
    actions = []
    if args.show:
        actions.append("--show")
    if args.set:
        actions.append("--set")
    if args.add is not None:
        actions.append("--add")
    if args.remove:
        actions.append("--remove")
    if len(actions) > 1:
        return f"Conflicting flags: {' '.join(actions)}"
    return None


def run(args: argparse.Namespace) -> int:
    """Entry point for the ``edit`` subcommand. Returns exit code."""
    quiet = args.quiet or resolve_summary(args)
    config_dir = Path(args.config)
    try:
        target_file = _resolve_target(config_dir, args.file)
    except ValueError as e:
        return fail_stderr(str(e))

    error = _check_exclusive(args)
    if error:
        return fail_stderr(error)

    if args.add is not None and args.alias is not None:
        return fail_stderr(
            f"--add ignores the positional alias '{args.alias}' — "
            "drop the alias or use --set instead"
        )

    if args.alias is None and (args.set or args.remove):
        return fail_stderr("alias required for --set or --remove")

    if not target_file.exists() and args.add is None:
        return fail_stderr(f"file not found: {target_file}")

    editor = YAMLEditor(target_file)

    try:
        if args.add is not None:
            # Keep JSON parsing and add-specific validation ahead of shape
            # resolution and the shared mutation boundary.
            return _run_add(editor, args.add, quiet)

        # --show (or default), --set, --remove
        if args.show or not any([args.set, args.remove]):
            return _run_show(editor, args.alias)

        shape = _resolve_shape(editor)

        alias: str = args.alias  # type: ignore[assignment]

        if args.set:
            return _run_set(editor, alias, args.set, quiet, shape=shape)

        if args.remove:
            return _run_remove(editor, alias, quiet, shape=shape)

        return 1  # pragma: no cover  # unreachable; satisfies type checker

    except FileNotFoundError as e:
        return fail_stderr(f"could not read {target_file}: {e}")
    except OSError as e:
        return fail_stderr(f"could not read {target_file}: {e}")
    except YAMLError as e:
        return fail_stderr(f"could not parse {target_file}: {e}")


def _resolve_shape(editor: YAMLEditor) -> _FileShape:
    """Resolve a file's source and effective edit shape in one public-load path."""
    default_shape: _EditableShape = (
        _ShapeKind.DICT if editor.path.name == "scripts.yaml" else _ShapeKind.LIST
    )
    if not editor.path.exists():
        return _FileShape(_ShapeKind.MISSING, default_shape)

    data = editor.load()
    if isinstance(data, list):
        return _FileShape(_ShapeKind.LIST, _ShapeKind.LIST)
    if isinstance(data, dict):
        return _FileShape(_ShapeKind.DICT, _ShapeKind.DICT)
    if data is None:
        return _FileShape(_ShapeKind.EMPTY, default_shape)
    return _FileShape(_ShapeKind.UNSUPPORTED, None)


def _dispatch_by_filetype[T](
    editor: YAMLEditor,
    alias: str,
    *,
    shape: _FileShape | None = None,
    on_dict: Callable[[YAMLEditor, str], T],
    on_list: Callable[[YAMLEditor, str], T],
) -> T:
    """Run the mapping callback, or the list fallback for other file shapes."""
    resolved_shape = shape if shape is not None else _resolve_shape(editor)
    if resolved_shape.editable is _ShapeKind.DICT:
        return on_dict(editor, alias)
    if resolved_shape.editable is _ShapeKind.LIST:
        return on_list(editor, alias)
    raise TypeError(
        f"Cannot edit {editor.path.name}: expected a list or mapping, got unknown"
    )


def _run_show(editor: YAMLEditor, alias: str | None) -> int:
    data = editor.load()
    if data is None:
        print("(empty file)", file=sys.stderr)
        return 0
    if isinstance(data, list):
        if alias is not None:
            idx = editor.find_automation(alias)
            if idx is None:
                return fail_stderr(f"automation '{alias}' not found")
            editor.dump_to(data[idx], sys.stdout)
        else:
            for item in data:
                if isinstance(item, dict) and "alias" in item:
                    print(item["alias"])
    elif isinstance(data, dict):
        if alias is not None:
            if alias in data:
                editor.dump_to({alias: data[alias]}, sys.stdout)
            else:
                return fail_stderr(f"script '{alias}' not found")
        else:
            for key in data:
                print(key)
    else:
        return fail_stderr(
            f"Cannot show {editor.path.name}: expected a list or mapping, "
            f"got {type(data).__name__}"
        )
    return 0


def _run_add(
    editor: YAMLEditor,
    json_str: str,
    quiet: bool,
    *,
    shape: _FileShape | None = None,
) -> int:
    try:
        entry = json.loads(json_str)
    except json.JSONDecodeError as e:
        return fail_stderr(f"invalid JSON: {e}")
    if not isinstance(entry, dict):
        return fail_stderr("--add value must be a JSON object")

    resolved_shape = shape if shape is not None else _resolve_shape(editor)
    if resolved_shape.editable is None:
        return fail_stderr(
            f"Cannot add to {editor.path.name}: expected a list or mapping, got unknown"
        )

    def add_script(ed: YAMLEditor) -> str:
        key = str(entry.get("id") or entry.get("alias") or "")
        if not key:
            raise ValueError("--add requires 'id' or 'alias' key for script files")
        ed.add_script(key, entry)
        return key

    def add_automation(ed: YAMLEditor) -> str:
        ed.add_automation(entry)
        return str(entry.get("alias") or entry.get("id") or "(no alias)")

    add_entry: Callable[[YAMLEditor], str] = (
        add_script if resolved_shape.editable is _ShapeKind.DICT else add_automation
    )

    return _run_mutation(
        editor,
        lambda: add_entry(editor),
        lambda result: f"Added: {result}",
        quiet,
    )


def _run_set(
    editor: YAMLEditor,
    alias: str,
    kvs: list[str],
    quiet: bool,
    *,
    shape: _FileShape | None = None,
) -> int:
    updates: dict = {}
    for kv in kvs:
        if "=" not in kv:
            return fail_stderr(f"--set value must be KEY=VALUE, got '{kv}'")
        key, _, value = kv.partition("=")
        key = key.strip()
        if not key:
            return fail_stderr("--set key must not be empty")
        if "." in key:
            return fail_stderr(
                f"--set does not support nested paths; got '{key}' "
                "(set a flat top-level key)"
            )
        updates[key] = _parse_value(value.strip())

    return _run_mutation(
        editor,
        lambda: _dispatch_by_filetype(
            editor,
            alias,
            shape=shape,
            on_dict=lambda ed, al: ed.update_script(al, updates),
            on_list=lambda ed, al: ed.update_automation(al, updates),
        ),
        f"Updated '{alias}': {list(updates.keys())}",
        quiet,
    )


def _run_mutation(
    editor: YAMLEditor,
    operation: Callable[[], object],
    success_message: str | Callable[[object], str],
    quiet: bool,
) -> int:
    """Execute, save, and report a mutating edit operation."""
    try:
        result = operation()
    except (ValueError, TypeError) as e:
        return fail_stderr(str(e))

    message = success_message(result) if callable(success_message) else success_message
    return _save_and_report(editor, message, quiet)


def _save_and_report(editor: YAMLEditor, success_message: str, quiet: bool) -> int:
    """Save an edit and translate expected write failures to CLI diagnostics."""
    try:
        editor.save()
    except OSError as e:
        return fail_stderr(f"could not write {editor.path}: {e}")
    if not quiet:
        print(success_message)
    return 0


def _run_remove(
    editor: YAMLEditor,
    alias: str,
    quiet: bool,
    *,
    shape: _FileShape | None = None,
) -> int:
    return _run_mutation(
        editor,
        lambda: _dispatch_by_filetype(
            editor,
            alias,
            shape=shape,
            on_dict=lambda ed, al: ed.remove_script(al),
            on_list=lambda ed, al: ed.remove_automation(al),
        ),
        f"Removed: {alias}",
        quiet,
    )


def _parse_value(raw: str):
    """Parse a single key=value string using YAML for booleans/ints etc.

    Coercion table (pinned by test_parse_value_coercion):
        true/false -> bool    123 -> int     3.14 -> float
        null/~/"" -> None     hello -> str   yes/no -> str (YAML 1.2)
        [1,2] -> list         '"true"' -> literal str "true"
    To force a literal string that looks like another type, wrap it in
    single or double quotes (e.g. --set foo='"true"').
    """
    try:
        return _SAFE_YAML.load(raw)
    except YAMLError, ValueError, TypeError:
        return raw
