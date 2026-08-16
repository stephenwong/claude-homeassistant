"""TDD tests for tools/commands/edit.py — ha_cli edit subcommand."""

from unittest.mock import patch

import pytest
import yaml

from tests.helpers import make_command_args, make_parser, parse_command_args
from tools.commands import edit
from tools.commands import stale_sensors as stale_cmd
from tools.commands.edit import (
    _dispatch_by_filetype,
    _parse_value,
    _resolve_shape,
    _run_add,
    _run_show,
    add_parser,
    run,
)
from tools.ha.yaml_editor import YAMLEditor


def _boom(*arguments, **keywords):
    """Helper: raises TypeError. Used in monkeypatch tests."""
    raise TypeError("boom")


def make_args(**overrides):
    """Build args from edit's production parser, with test-specific overrides."""
    return make_command_args("edit", add_parser, ["automations"], **overrides)


class TestAddParser:
    def test_subparser_registered(self):
        parser, subparsers = make_parser()
        add_parser(subparsers)
        args = parser.parse_args(["edit", "automations"])
        assert args.command == "edit"
        assert args.file == "automations"

    def test_alias_positional_parsed_correctly(self):
        """edit <file> <alias> --show should parse alias as a positional."""
        args = parse_command_args(
            "edit", add_parser, ["automations", "Turn on Alarm", "--show"]
        )
        assert args.file == "automations"
        assert args.alias == "Turn on Alarm"
        assert args.show is True

    def test_config_dir_flag_defaults(self):
        """--config should default to 'config'."""
        args = parse_command_args("edit", add_parser, ["automations", "--show"])
        assert args.config == "config"
        assert args.file == "automations"

    def test_summary_flag_registered(self):
        args = parse_command_args("edit", add_parser, ["automations", "--summary"])
        assert args.summary is True

    def test_no_summary_flag_registered(self):
        args = parse_command_args("edit", add_parser, ["automations", "--no-summary"])
        assert args.no_summary is True

    def test_summary_defaults_false(self):
        args = parse_command_args("edit", add_parser, ["automations"])
        assert args.summary is False
        assert args.no_summary is False

    def test_edit_and_stale_sensors_config_namespaces_match(self):
        parser, subparsers = make_parser()
        add_parser(subparsers)
        stale_cmd.add_parser(subparsers)

        edit_args = parser.parse_args(["edit", "automations"])
        stale_args = parser.parse_args(["stale-sensors"])
        assert edit_args.config == stale_args.config == "config"

        edit_action = next(
            action
            for action in subparsers.choices["edit"]._actions
            if action.dest == "config"
        )
        stale_action = next(
            action
            for action in subparsers.choices["stale-sensors"]._actions
            if action.dest == "config"
        )
        assert (
            edit_action.option_strings
            == stale_action.option_strings
            == [
                "--config",
                "-c",
            ]
        )
        assert edit_action.default == stale_action.default == "config"

    @pytest.mark.parametrize("command", ["edit", "stale-sensors"])
    def test_config_help_documents_shared_default(self, command, capsys):
        parser, subparsers = make_parser()
        if command == "edit":
            add_parser(subparsers)
            argv = ["edit", "--help"]
        else:
            stale_cmd.add_parser(subparsers)
            argv = ["stale-sensors", "--help"]
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
        help_text = capsys.readouterr().out
        assert "--config" in help_text
        assert "-c" in help_text
        assert "default: config" in help_text


def _write_file(cfg_dir, basename, content):
    path = cfg_dir / f"{basename}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _read_yaml(config_dir, basename):
    """Read one edited YAML document for serialization assertions."""
    return yaml.safe_load((config_dir / f"{basename}.yaml").read_text(encoding="utf-8"))


class TestDispatchByFiletype:
    def test_supplied_shape_skips_shape_detection(self, tmp_path, monkeypatch):
        editor = YAMLEditor(_write_file(tmp_path, "scripts", "{}"))
        monkeypatch.setattr(
            edit,
            "_resolve_shape",
            lambda _editor: pytest.fail("shape should already be known"),
        )
        calls = []
        _dispatch_by_filetype(
            editor,
            "foo",
            shape=edit._FileShape(edit._ShapeKind.DICT, edit._ShapeKind.DICT),
            on_dict=lambda _ed, al: calls.append(f"dict:{al}"),
            on_list=lambda _ed, al: calls.append(f"list:{al}"),
        )
        assert calls == ["dict:foo"]

    def test_dispatches_to_on_dict_for_scripts(self, tmp_path):
        editor = YAMLEditor(_write_file(tmp_path, "scripts", "foo:\n  mode: single\n"))
        calls = []
        _dispatch_by_filetype(
            editor,
            "foo",
            on_dict=lambda ed, al: calls.append(f"dict:{al}"),
            on_list=lambda ed, al: calls.append(f"list:{al}"),
        )
        assert calls == ["dict:foo"]

    def test_dispatches_to_on_list_for_automations(self, tmp_path):
        editor = YAMLEditor(_write_file(tmp_path, "automations", "- alias: foo\n"))
        calls = []
        _dispatch_by_filetype(
            editor,
            "foo",
            on_dict=lambda ed, al: calls.append(f"dict:{al}"),
            on_list=lambda ed, al: calls.append(f"list:{al}"),
        )
        assert calls == ["list:foo"]

    def test_unknown_filetype_preserves_list_fallback(self, tmp_path):
        editor = YAMLEditor(_write_file(tmp_path, "empty", ""))
        calls = []
        _dispatch_by_filetype(
            editor,
            "foo",
            on_dict=lambda ed, al: calls.append("dict"),
            on_list=lambda ed, al: calls.append("list"),
        )
        assert calls == ["list"]

    def test_add_with_numeric_alias_is_saved(self, tmp_path):
        path = _write_file(tmp_path, "automations", "[]")
        result = _run_add(
            YAMLEditor(path), '{"alias": 123, "trigger": [], "action": []}', True
        )
        assert result == 0
        assert "alias: 123" in path.read_text()


class TestResolveShape:
    @pytest.mark.parametrize(
        ("file", "content", "expected_kind", "expected_editable"),
        [
            ("automations", "- alias: foo\n", "list", "list"),
            ("scripts", "foo: {}\n", "dict", "dict"),
            ("automations", "", "empty", "list"),
            ("scripts", "", "empty", "dict"),
            ("automations", "42\n", "unsupported", None),
        ],
    )
    def test_existing_shapes(
        self,
        tmp_path,
        file,
        content,
        expected_kind,
        expected_editable,
    ):
        path = _write_file(tmp_path, file, content)
        shape = _resolve_shape(YAMLEditor(path))

        assert shape.kind.value == expected_kind
        if expected_editable is None:
            assert shape.editable is None
        else:
            assert shape.editable is not None
            assert shape.editable.value == expected_editable

    @pytest.mark.parametrize(
        ("file", "expected_editable"),
        [("automations", "list"), ("scripts", "dict")],
    )
    def test_missing_file_uses_filename_default(
        self, tmp_path, file, expected_editable
    ):
        shape = _resolve_shape(YAMLEditor(tmp_path / f"{file}.yaml"))

        assert shape.kind.value == "missing"
        assert shape.editable is not None
        assert shape.editable.value == expected_editable


class TestRunShow:
    def _args(self, cfg_dir, file="automations", alias=None):
        return make_args(config=str(cfg_dir), file=file, alias=alias, show=True)

    def test_show_all_lists_aliases(self, tmp_path, capsys):
        _write_file(
            tmp_path,
            "automations",
            """\
- id: a1
  alias: First
  triggers: []
  conditions: []
  actions: []
  mode: single
- id: a2
  alias: Second
  triggers: []
  conditions: []
  actions: []
  mode: single
""",
        )
        run(self._args(tmp_path))
        out = capsys.readouterr().out
        assert "First" in out
        assert "Second" in out

    def test_show_one_displays_full_automation(self, tmp_path, capsys):
        _write_file(
            tmp_path,
            "automations",
            """\
- id: abc
  alias: Target
  triggers:
    - trigger: state
      entity_id: binary_sensor.test
      to: 'on'
  conditions: []
  actions:
    - action: notify.test
      data:
        message: Hello
  mode: single
""",
        )
        run(self._args(tmp_path, alias="Target"))
        out = capsys.readouterr().out
        assert "Target" in out
        assert "notify.test" in out

    def test_show_missing_alias_prints_error(self, tmp_path, capsys):
        _write_file(tmp_path, "automations", "[]")
        result = run(self._args(tmp_path, alias="Ghost"))
        assert result == 1
        assert "not found" in capsys.readouterr().err.lower()

    # Empty-file diagnostics

    def test_show_empty_file_emits_diagnostic(self, tmp_path, capsys):
        """--show on an empty file reports the empty-file diagnostic on stderr."""
        _write_file(tmp_path, "automations", "")
        rc = run(self._args(tmp_path))
        assert rc == 0
        err = capsys.readouterr().err
        assert "empty" in err.lower()

    def test_show_uses_public_load_result(self, capsys):

        class PublicOnlyEditor:
            def __init__(self):
                self.load_calls = 0

            def load(self):
                self.load_calls += 1
                return [{"alias": "Public API"}]

        editor = PublicOnlyEditor()
        assert _run_show(editor, None) == 0
        assert editor.load_calls == 1
        assert capsys.readouterr().out.strip() == "Public API"


class TestRunSet:
    def _args(self, cfg_dir, alias=None, kvs=None):
        return make_args(config=str(cfg_dir), alias=alias, show=False, set=kvs or [])

    def test_set_updates_automation(self, tmp_path):
        _write_file(
            tmp_path,
            "automations",
            """\
- id: abc
  alias: Target
  description: Old
  triggers: []
  conditions: []
  actions: []
  mode: single
""",
        )
        run(self._args(tmp_path, alias="Target", kvs=["description=Updated"]))
        reloaded = _read_yaml(tmp_path, "automations")
        assert reloaded[0]["description"] == "Updated"

    def test_set_missing_alias_returns_error(self, tmp_path, capsys):
        _write_file(tmp_path, "automations", "[]")
        result = run(self._args(tmp_path, alias="Ghost", kvs=["x=y"]))
        assert result == 1
        err = capsys.readouterr().err
        assert "not found" in err.lower()


class TestRunAdd:
    def _args(self, cfg_dir, json_str=None):
        return make_args(config=str(cfg_dir), show=False, add=json_str)

    def test_add_appends_automation(self, tmp_path):
        _write_file(
            tmp_path,
            "automations",
            """\
- id: existing
  alias: Existing
  triggers: []
  conditions: []
  actions: []
  mode: single
""",
        )
        run(
            self._args(
                tmp_path,
                json_str='{"alias":"New","id":"new_id","triggers":[],"conditions":[],"actions":[],"mode":"single"}',
            )
        )
        reloaded = _read_yaml(tmp_path, "automations")
        assert len(reloaded) == 2
        assert reloaded[1]["alias"] == "New"

    def test_add_detects_existing_file_shape_once(self, tmp_path, monkeypatch):
        from tools.commands import edit

        _write_file(tmp_path, "automations", "[]")
        calls = 0
        original = edit._resolve_shape

        def resolve_once(editor):
            nonlocal calls
            calls += 1
            return original(editor)

        monkeypatch.setattr(edit, "_resolve_shape", resolve_once)
        assert run(self._args(tmp_path, json_str='{"alias":"New"}')) == 0
        assert calls == 1

    def test_add_save_oserror_reports_write_failure(
        self, tmp_path, monkeypatch, capsys
    ):
        _write_file(tmp_path, "automations", "[]")

        def fail_save(_editor):
            raise OSError("disk full")

        monkeypatch.setattr(YAMLEditor, "save", fail_save)
        result = run(self._args(tmp_path, json_str='{"alias":"New"}'))
        assert result == 1
        assert "could not write" in capsys.readouterr().err.lower()


class TestRunRemove:
    def _args(self, cfg_dir, alias=None):
        return make_args(config=str(cfg_dir), alias=alias, show=False, remove=True)

    def test_remove_automation(self, tmp_path):
        _write_file(
            tmp_path,
            "automations",
            """\
- id: keep
  alias: Keep Me
  triggers: []
  conditions: []
  actions: []
  mode: single
- id: del
  alias: Delete Me
  triggers: []
  conditions: []
  actions: []
  mode: single
""",
        )
        run(self._args(tmp_path, alias="Delete Me"))
        reloaded = _read_yaml(tmp_path, "automations")
        assert len(reloaded) == 1
        assert reloaded[0]["alias"] == "Keep Me"

    def test_remove_missing_alias_returns_error(self, tmp_path, capsys):
        _write_file(tmp_path, "automations", "[]")
        result = run(self._args(tmp_path, alias="Ghost"))
        assert result == 1
        err = capsys.readouterr().err
        assert "not found" in err.lower()


# ---------------------------------------------------------------------------
# Edge case tests (from rubber-duck review)
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_conflicting_show_and_remove_rejected(self, capsys):
        result = run(make_args(show=True, remove=True, alias="X"))
        assert result == 1
        assert "conflicting" in capsys.readouterr().err.lower()

    def test_conflicting_add_and_show_rejected(self):
        result = run(make_args(show=True, add='{"x":1}'))
        assert result == 1

    def test_set_rejects_dotted_nested_key(self, tmp_path, capsys):
        _write_file(
            tmp_path,
            "automations",
            "- alias: A\n  mode: single\n  triggers: []\n  actions: []\n",
        )
        args = make_args(config=str(tmp_path), alias="A", set=["mode.parallel=1"])
        result = run(args)
        assert result == 1
        assert "nested" in capsys.readouterr().err.lower()

    def test_show_corrupt_yaml_reports_error(self, tmp_path, capsys):
        _write_file(tmp_path, "automations", "{[[[ not valid yaml\n")
        args = make_args(config=str(tmp_path), show=True)
        result = run(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "could not parse" in err.lower() or "yaml" in err.lower()
        assert "Traceback" not in err

    def test_execution_file_not_found_reports_read_error(
        self, tmp_path, capsys, monkeypatch
    ):
        _write_file(tmp_path, "automations", "[]")

        def missing(_editor):
            raise FileNotFoundError("file vanished")

        monkeypatch.setattr("tools.commands.edit.YAMLEditor.load", missing)
        result = run(make_args(config=str(tmp_path), show=True))
        assert result == 1
        err = capsys.readouterr().err.lower()
        assert "could not read" in err
        assert "could not parse" not in err

    def test_nonexistent_file_errors(self, tmp_path, capsys):
        args = make_args(config=str(tmp_path), show=True)
        result = run(args)
        assert result == 1
        assert "not found" in capsys.readouterr().err.lower()

    def test_set_on_nonexistent_file_errors(self, tmp_path, capsys):
        args = make_args(config=str(tmp_path), set=["x=y"], alias="X")
        result = run(args)
        assert result == 1
        assert "not found" in capsys.readouterr().err.lower()

    def test_add_creates_file(self, tmp_path):
        """--add creates the file if it didn't exist."""
        args = make_args(
            config=str(tmp_path),
            add='{"alias":"New","id":"n","triggers":[],"conditions":[],"actions":[],"mode":"single"}',
        )
        result = run(args)
        assert result == 0
        data = _read_yaml(tmp_path, "automations")
        assert data[0]["alias"] == "New"

    def test_empty_add_value_is_parsed_and_rejected(self, tmp_path, capsys):
        args = make_args(config=str(tmp_path), add="")

        assert run(args) == 1
        assert "json" in capsys.readouterr().err.lower()

    def test_add_to_scripts_file(self, tmp_path):
        """--add on a scripts (dict) file adds as a new key."""
        _write_file(tmp_path, "scripts", "{}")
        args = make_args(
            config=str(tmp_path),
            file="scripts",
            add='{"alias":"Notify","id":"notify","sequence":[]}',
        )
        result = run(args)
        assert result == 0
        data = _read_yaml(tmp_path, "scripts")
        assert isinstance(data, dict)
        assert "notify" in data

    def test_add_to_new_scripts_file_creates_dict(self, tmp_path):
        """--add to a non-existent scripts file creates a dict, not a list."""
        args = make_args(
            config=str(tmp_path),
            file="scripts",
            add='{"alias":"Notify","id":"notify","sequence":[]}',
        )
        result = run(args)
        assert result == 0
        data = _read_yaml(tmp_path, "scripts")
        assert isinstance(data, dict)
        assert "notify" in data

    def test_no_action_defaults_to_show(self, tmp_path, capsys):
        """No flag defaults to --show (lists aliases or errors if no file)."""
        args = make_args(config=str(tmp_path), show=False)
        result = run(args)
        assert (
            result == 1
        )  # file not found since tmp_path doesn't have automations.yaml
        err = capsys.readouterr().err
        assert "not found" in err.lower()

    def test_missing_alias_for_set_errors(self, capsys):
        result = run(make_args(set=["x=y"]))
        assert result == 1
        assert "alias required" in capsys.readouterr().err.lower()

    def test_set_on_scripts_file(self, tmp_path):
        """--set on scripts (dict) updates via update_script."""
        _write_file(
            tmp_path,
            "scripts",
            """\
morning:
  alias: Morning
  sequence: []
""",
        )
        args = make_args(
            config=str(tmp_path),
            file="scripts",
            alias="morning",
            set=["description=Updated"],
        )
        result = run(args)
        assert result == 0
        data = _read_yaml(tmp_path, "scripts")
        assert data["morning"]["description"] == "Updated"

    def test_remove_on_scripts_file(self, tmp_path):
        """--remove on scripts (dict) removes via remove_script."""
        _write_file(
            tmp_path,
            "scripts",
            """\
keep:
  alias: Keep
  sequence: []
delete:
  alias: Delete
  sequence: []
""",
        )
        args = make_args(
            config=str(tmp_path),
            file="scripts",
            alias="delete",
            remove=True,
        )
        result = run(args)
        assert result == 0
        data = _read_yaml(tmp_path, "scripts")
        assert "delete" not in data
        assert "keep" in data

    # ── Scripts --show (covers edit.py:181-190) ─────────────────────

    def test_show_scripts_lists_keys(self, tmp_path, capsys):
        _write_file(
            tmp_path,
            "scripts",
            "morning:\n  sequence: []\nevening:\n  sequence: []\n",
        )
        run(make_args(config=str(tmp_path), file="scripts", show=True))
        out = capsys.readouterr().out
        assert "morning" in out
        assert "evening" in out

    def test_show_one_script_displays_full(self, tmp_path, capsys):
        _write_file(
            tmp_path,
            "scripts",
            "morning:\n  alias: Morning\n  sequence: []\n",
        )
        run(make_args(config=str(tmp_path), file="scripts", alias="morning", show=True))
        assert "Morning" in capsys.readouterr().out

    def test_show_missing_script_prints_error(self, tmp_path, capsys):
        _write_file(tmp_path, "scripts", "morning:\n  sequence: []\n")
        result = run(
            make_args(config=str(tmp_path), file="scripts", alias="ghost", show=True)
        )
        assert result == 1
        assert "not found" in capsys.readouterr().err.lower()

    # --add and --set error branches

    def test_add_invalid_json_returns_error(self, tmp_path, capsys):
        result = run(make_args(config=str(tmp_path), add="{not json"))
        assert result == 1
        assert "invalid json" in capsys.readouterr().err.lower()

    def test_add_json_array_returns_error(self, tmp_path, capsys):
        result = run(make_args(config=str(tmp_path), add="[1,2,3]"))
        assert result == 1
        assert "json object" in capsys.readouterr().err.lower()

    def test_add_scripts_without_id_or_alias_errors(self, tmp_path, capsys):
        _write_file(tmp_path, "scripts", "{}\n")
        result = run(
            make_args(config=str(tmp_path), file="scripts", add='{"foo":"bar"}')
        )
        assert result == 1
        err = capsys.readouterr().err.lower()
        assert "id" in err or "alias" in err

    def test_set_malformed_kv_returns_error(self, tmp_path, capsys):
        _write_file(tmp_path, "automations", "[]")
        result = run(make_args(config=str(tmp_path), alias="X", set=["no_equals"]))
        assert result == 1
        assert "key=value" in capsys.readouterr().err.lower()

    def test_set_empty_key_returns_error(self, tmp_path, capsys):
        _write_file(tmp_path, "automations", "- alias: X\n")
        result = run(make_args(config=str(tmp_path), alias="X", set=["=value"]))
        assert result == 1
        assert "key" in capsys.readouterr().err.lower()

    def test_permission_error_during_read_is_controlled(
        self, tmp_path, capsys, monkeypatch
    ):
        _write_file(tmp_path, "automations", "[]")

        def denied(_editor):
            raise PermissionError("permission denied")

        monkeypatch.setattr("tools.commands.edit.YAMLEditor.load", denied)
        result = run(make_args(config=str(tmp_path), show=True))
        assert result == 1
        assert "could not read" in capsys.readouterr().err.lower()

    # TypeError handlers

    @pytest.mark.parametrize(
        ("method_name", "args_kwargs", "initial_yaml"),
        [
            ("add_automation", {"add": '{"alias":"X","id":"x"}'}, "[]"),
            (
                "update_automation",
                {"alias": "A", "set": ["x=y"]},
                (
                    "- id: a\n  alias: A\n  triggers: []\n"
                    "  conditions: []\n  actions: []\n  mode: single\n"
                ),
            ),
            (
                "remove_automation",
                {"alias": "A", "remove": True},
                (
                    "- id: a\n  alias: A\n  triggers: []\n"
                    "  conditions: []\n  actions: []\n  mode: single\n"
                ),
            ),
        ],
    )
    def test_mutation_type_error_returns_error(
        self, tmp_path, capsys, monkeypatch, method_name, args_kwargs, initial_yaml
    ):
        _write_file(tmp_path, "automations", initial_yaml)
        monkeypatch.setattr(f"tools.commands.edit.YAMLEditor.{method_name}", _boom)
        result = run(make_args(config=str(tmp_path), **args_kwargs))
        assert result == 1
        assert "boom" in capsys.readouterr().err

    # Duplicate script-key validation

    def test_add_duplicate_script_key_returns_error(self, tmp_path, capsys):
        _write_file(tmp_path, "scripts", "morning:\n  sequence: []\n")
        result = run(
            make_args(
                config=str(tmp_path),
                file="scripts",
                add='{"id":"morning","alias":"M","sequence":[]}',
            )
        )
        assert result == 1
        assert "already exists" in capsys.readouterr().err.lower()

    # Path traversal guard

    def test_path_traversal_rejected(self, tmp_path, capsys):
        result = run(
            make_args(config=str(tmp_path), file="../../../etc/passwd", show=True)
        )
        assert result == 1
        assert "inside config directory" in capsys.readouterr().err.lower()

    @pytest.mark.parametrize("file", ["configuration", "other.yaml"])
    def test_only_supported_files_are_editable(self, tmp_path, file, capsys):
        result = run(make_args(config=str(tmp_path), file=file, show=True))
        assert result == 1
        assert "automations" in capsys.readouterr().err.lower()

    def test_existing_empty_scripts_file_creates_mapping(self, tmp_path):
        _write_file(tmp_path, "scripts", "")
        result = run(
            make_args(
                config=str(tmp_path),
                file="scripts",
                add='{"id":"notify","sequence":[]}',
            )
        )
        assert result == 0
        assert _read_yaml(tmp_path, "scripts") == {
            "notify": {"id": "notify", "sequence": []}
        }

    def test_scalar_yaml_is_rejected(self, tmp_path, capsys):
        _write_file(tmp_path, "automations", "42\n")
        result = run(make_args(config=str(tmp_path), show=True))
        assert result == 1
        assert "list or mapping" in capsys.readouterr().err.lower()

    @pytest.mark.parametrize("operation", ["add", "set", "remove"])
    def test_unsupported_shape_mutation_keeps_unknown_diagnostic(
        self, tmp_path, capsys, operation
    ):
        _write_file(tmp_path, "automations", "42\n")
        if operation == "add":
            args = make_args(config=str(tmp_path), add='{"alias":"New"}')
        elif operation == "set":
            args = make_args(config=str(tmp_path), alias="A", set=["x=y"])
        else:
            args = make_args(config=str(tmp_path), alias="A", remove=True)

        assert run(args) == 1
        assert capsys.readouterr().err.endswith("got unknown\n")

    # --set argument validation

    def test_set_with_no_values_rejected(self, tmp_path, capsys):
        """--set with no KEY=VALUE pairs must be rejected."""
        parser, subparsers = make_parser()
        from tools.commands.edit import add_parser

        add_parser(subparsers)
        with pytest.raises(SystemExit):
            parser.parse_args(["edit", "automations", "X", "--set"])

    # --add argument validation

    def test_add_with_positional_alias_rejected(self, tmp_path, capsys):
        """--add with a positional alias must be rejected as ambiguous."""
        _write_file(
            tmp_path,
            "automations",
            "- alias: A\n  triggers: []\n  actions: []\n",
        )
        args = make_args(
            config=str(tmp_path),
            alias="A",
            show=False,
            add='{"alias":"B"}',
        )
        rc = run(args)
        assert rc == 1
        assert "ignores the positional alias" in capsys.readouterr().err.lower()

    # Success output

    @patch("tools.common._is_tty", return_value=True)
    def test_add_prints_success_when_verbose(self, mock_is_tty, tmp_path, capsys):
        run(make_args(config=str(tmp_path), add='{"alias":"New","id":"n"}'))
        assert "Added:" in capsys.readouterr().out

    @patch("tools.common._is_tty", return_value=True)
    def test_remove_prints_success_when_verbose(self, mock_is_tty, tmp_path, capsys):
        _write_file(
            tmp_path,
            "automations",
            "- id: a\n  alias: A\n  triggers: []\n"
            "  conditions: []\n  actions: []\n  mode: single\n",
        )
        run(make_args(config=str(tmp_path), alias="A", remove=True))
        assert "Removed:" in capsys.readouterr().out


class TestParseValue:
    """YAML-based value coercion for --set KEY=VALUE."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("true", True),
            ("false", False),
            ("yes", "yes"),  # ruamel safe=YAML 1.2 — yes is a string, not bool
            ("123", 123),
            ("3.14", 3.14),
            ("null", None),
            ("~", None),
            ("", None),  # empty string → None (documented)
            ("hello world", "hello world"),
            ("[1, 2]", [1, 2]),
            ('"true"', "true"),  # quoted — the literal-string escape hatch
            ("'123'", "123"),  # quoted — stays string
        ],
    )
    def test_coercion(self, raw, expected):
        assert _parse_value(raw) == expected
        if isinstance(expected, bool):
            assert isinstance(_parse_value(raw), bool)
        elif isinstance(expected, int) and not isinstance(expected, bool):
            assert isinstance(_parse_value(raw), int)

    def test_yaml_error_falls_back_to_raw(self):
        assert _parse_value("[1, 2") == "[1, 2"


class TestRunSetOutput:
    """--set output honors quiet mode and reports successful updates."""

    def test_run_set_suppresses_output_when_quiet(self, tmp_path, capsys):
        autos = tmp_path / "automations.yaml"
        autos.write_text("- alias: A\n  id: '1'\n")
        args = make_args(
            file="automations",
            alias="A",
            config=str(tmp_path),
            show=False,
            set=["mode=single"],
            quiet=True,
            summary=True,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Updated" not in captured.out

    def test_run_set_prints_when_verbose(self, tmp_path, capsys):
        autos = tmp_path / "automations.yaml"
        autos.write_text("- alias: A\n  id: '1'\n")
        args = make_args(
            file="automations",
            alias="A",
            config=str(tmp_path),
            show=False,
            set=["mode=single"],
            quiet=False,
            summary=False,
            no_summary=True,
        )
        rc = run(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Updated" in captured.out


# Round-trip comment preservation


def test_edit_round_trip_preserves_comments(tmp_path):
    """Loading and saving through edit preserves YAML comments."""
    auto = tmp_path / "automations.yaml"
    original = (
        "# top-level comment\n"
        "- alias: A\n"
        "  mode: single        # inline comment\n"
        "  triggers: []\n"
        "  actions: []\n"
    )

    auto.write_text(original, encoding="utf-8")
    e = YAMLEditor(auto)
    e.load()
    e.save()
    saved = auto.read_text(encoding="utf-8")
    assert "# top-level comment" in saved
    assert "# inline comment" in saved
