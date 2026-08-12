"""TDD tests for tools/ha/yaml_editor.py — round-trip YAML editing."""

from pathlib import Path

import pytest


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Cycle 1: round-trip load/dump
# ---------------------------------------------------------------------------


def test_round_trip_preserves_content(tmp_path):
    """A YAML file loaded and dumped back should be semantically identical."""
    from tools.ha.yaml_editor import YAMLEditor

    yaml_content = """\
- id: abc123
  alias: Test Automation
  description: "A test"
  triggers:
    - trigger: state
      entity_id: binary_sensor.test
      to: "on"
  conditions: []
  actions:
    - action: notify.test
      data:
        message: Hello
  mode: single
"""
    path = tmp_path / "automations.yaml"
    _write_yaml(path, yaml_content)

    editor = YAMLEditor(path)
    data = editor.load()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["alias"] == "Test Automation"


def test_round_trip_preserves_comments(tmp_path):
    """Comments survive a round-trip load + dump."""
    from tools.ha.yaml_editor import YAMLEditor

    yaml_content = """\
# A header comment about this file
- id: abc123
  # This alias is important
  alias: Test Automation
  triggers: []
  conditions: []
  actions: []
  mode: single
# A footer comment
"""
    path = tmp_path / "automations.yaml"
    _write_yaml(path, yaml_content)

    editor = YAMLEditor(path)
    data = editor.load()
    editor.dump(data, path)

    roundtripped = path.read_text(encoding="utf-8")
    assert "# A header comment" in roundtripped
    assert "# This alias is important" in roundtripped
    assert "# A footer comment" in roundtripped


def test_round_trip_preserves_order(tmp_path):
    """The order of keys and list items is preserved."""
    from tools.ha.yaml_editor import YAMLEditor

    yaml_content = """\
- id: bbb
  alias: Beta
  triggers: []
  conditions: []
  actions: []
  mode: single
- id: aaa
  alias: Alpha
  triggers: []
  conditions: []
  actions: []
  mode: single
- id: ccc
  alias: Gamma
  triggers: []
  conditions: []
  actions: []
  mode: single
"""
    path = tmp_path / "automations.yaml"
    _write_yaml(path, yaml_content)

    editor = YAMLEditor(path)
    data = editor.load()
    editor.dump(data, path)

    roundtripped = path.read_text(encoding="utf-8")
    bbb_pos = roundtripped.index("Beta")
    aaa_pos = roundtripped.index("Alpha")
    gamma_pos = roundtripped.index("Gamma")
    assert bbb_pos < aaa_pos < gamma_pos, "Order should be Beta, Alpha, Gamma"


def test_load_empty_file(tmp_path):
    """An empty file loads as None."""
    from tools.ha.yaml_editor import YAMLEditor

    path = tmp_path / "empty.yaml"
    _write_yaml(path, "")

    editor = YAMLEditor(path)
    data = editor.load()
    assert data is None


def test_load_nonexistent_file(tmp_path):
    """Loading a nonexistent file raises FileNotFoundError."""
    from tools.ha.yaml_editor import YAMLEditor

    path = tmp_path / "nonexistent.yaml"
    editor = YAMLEditor(path)
    with pytest.raises(FileNotFoundError):
        editor.load()


def test_dump_creates_file(tmp_path):
    """Dump writes the file if it doesn't exist."""
    from tools.ha.yaml_editor import YAMLEditor

    path = tmp_path / "new.yaml"
    editor = YAMLEditor(path)
    editor.dump(None, path)
    assert path.exists()


def test_load_scripts_dict(tmp_path):
    """Scripts.yaml is a dict, not a list. The editor handles both."""
    from tools.ha.yaml_editor import YAMLEditor

    yaml_content = """\
notify_on_doorbell:
  alias: Notify on Doorbell
  sequence:
    - action: notify.test
      data:
        message: Ding!
"""
    path = tmp_path / "scripts.yaml"
    _write_yaml(path, yaml_content)

    editor = YAMLEditor(path)
    data = editor.load()
    assert isinstance(data, dict)
    assert "notify_on_doorbell" in data
    assert data["notify_on_doorbell"]["alias"] == "Notify on Doorbell"


def test_load_and_dump_use_path_open(tmp_path, monkeypatch):
    from tools.ha.yaml_editor import YAMLEditor

    path = tmp_path / "automations.yaml"
    _write_yaml(path, "- alias: A\n  triggers: []\n  actions: []\n")
    original_open = Path.open
    opened_paths = []

    def path_open(self, *args, **kwargs):
        opened_paths.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", path_open)
    editor = YAMLEditor(path)
    data = editor.load()
    editor.dump(data, path)

    assert opened_paths.count(path) == 2


# ---------------------------------------------------------------------------
# Cycle 2: find / add / update / remove automations by alias
# ---------------------------------------------------------------------------

AUTOMATIONS_FIXTURE = """\
- id: abc
  alias: Morning Routine
  triggers:
    - trigger: time
      at: "07:00:00"
  conditions: []
  actions:
    - action: light.turn_on
      target:
        entity_id: light.kitchen
  mode: single
- id: def
  alias: Evening Scene
  triggers:
    - trigger: sun
      event: sunset
  conditions: []
  actions:
    - action: scene.turn_on
      target:
        entity_id: scene.evening
  mode: single
- id: ghi
  alias: Doorbell Alert
  triggers:
    - trigger: state
      entity_id: binary_sensor.doorbell
      to: "on"
  conditions: []
  actions:
    - action: notify.mobile
      data:
        message: Someone at the door!
  mode: single
"""


class TestFindAutomation:
    def test_find_by_alias_returns_index(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        idx = editor.find_automation("Evening Scene")
        assert idx == 1

    def test_find_by_alias_returns_none_when_missing(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        idx = editor.find_automation("Nonexistent Automation")
        assert idx is None

    def test_find_on_empty_list(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, "[]")

        editor = YAMLEditor(path)
        idx = editor.find_automation("Anything")
        assert idx is None


class TestAddAutomation:
    def test_append_new_automation(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        new = {
            "alias": "New Automation",
            "id": "new_id",
            "triggers": [{"trigger": "time", "at": "12:00:00"}],
            "conditions": [],
            "actions": [{"action": "notify.test"}],
            "mode": "single",
        }
        editor.add_automation(new)
        editor.save()

        reloaded = YAMLEditor(path).load()
        assert len(reloaded) == 4
        assert reloaded[-1]["alias"] == "New Automation"

    def test_add_to_empty_file(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, "[]")

        editor = YAMLEditor(path)
        editor.add_automation(
            {
                "alias": "Solo",
                "id": "solo",
                "triggers": [],
                "conditions": [],
                "actions": [],
                "mode": "single",
            }
        )
        editor.save()

        reloaded = YAMLEditor(path).load()
        assert len(reloaded) == 1
        assert reloaded[0]["alias"] == "Solo"

    def test_add_duplicate_alias_raises(self, tmp_path):
        import pytest

        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)
        editor = YAMLEditor(path)
        with pytest.raises(ValueError, match="already exists"):
            editor.add_automation(
                {"alias": "Morning Routine", "triggers": [], "actions": []}
            )


class TestUpdateAutomation:
    def test_update_by_alias(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        editor.update_automation("Evening Scene", {"description": "Updated"})
        editor.save()

        reloaded = YAMLEditor(path).load()
        assert reloaded[1]["description"] == "Updated"
        # Unaffected fields remain
        assert reloaded[1]["alias"] == "Evening Scene"

    def test_update_adds_new_key(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        editor.update_automation("Doorbell Alert", {"max_exceeded": "silent"})
        editor.save()

        reloaded = YAMLEditor(path).load()
        assert reloaded[2]["max_exceeded"] == "silent"

    def test_update_missing_alias_raises(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        with pytest.raises(ValueError, match="Not Found"):
            editor.update_automation("Not Found", {"x": 1})


class TestRemoveAutomation:
    def test_remove_by_alias(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        editor.remove_automation("Evening Scene")
        editor.save()

        reloaded = YAMLEditor(path).load()
        assert len(reloaded) == 2
        aliases = [a["alias"] for a in reloaded]
        assert "Evening Scene" not in aliases
        assert "Morning Routine" in aliases
        assert "Doorbell Alert" in aliases

    def test_remove_missing_alias_raises(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        with pytest.raises(ValueError, match="Not Found"):
            editor.remove_automation("Not Found")

    def test_remove_last_automation(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(
            path,
            """\
- id: only
  alias: The Only One
  triggers: []
  conditions: []
  actions: []
  mode: single
""",
        )

        editor = YAMLEditor(path)
        editor.remove_automation("The Only One")
        editor.save()

        reloaded = YAMLEditor(path).load()
        assert reloaded == []


class TestSaveInPlace:
    def test_save_writes_back_to_same_file(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        editor.add_automation(
            {
                "alias": "Added",
                "id": "added",
                "triggers": [],
                "conditions": [],
                "actions": [],
                "mode": "single",
            }
        )
        editor.save()

        # Re-load from same path
        reloaded = YAMLEditor(path).load()
        assert len(reloaded) == 4
        assert reloaded[-1]["alias"] == "Added"


# ---------------------------------------------------------------------------
# Cycle 3: atomic write with validation gate
# ---------------------------------------------------------------------------


class FakeValidator:
    """Callable that passes or fails based on a predicate."""

    def __init__(self, *, should_pass: bool = True):
        self.should_pass = should_pass
        self.validated_path: Path | None = None
        self.call_count = 0

    def __call__(self, path: Path) -> bool:
        self.validated_path = path
        self.call_count += 1
        return self.should_pass


class TestAtomicSave:
    def test_validator_passed_saves_atomically(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        editor.add_automation(
            {
                "alias": "New",
                "id": "new",
                "triggers": [],
                "conditions": [],
                "actions": [],
                "mode": "single",
            }
        )

        validator = FakeValidator(should_pass=True)
        editor.save(validator=validator)

        assert validator.call_count == 1
        assert validator.validated_path is not None
        # Temp file should be cleaned up (moved to target)
        assert not validator.validated_path.exists()
        # Target file is updated
        reloaded = YAMLEditor(path).load()
        assert len(reloaded) == 4

    def test_validator_fails_preserves_original(self, tmp_path):
        from tools.ha.yaml_editor import ValidationError, YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        editor.add_automation(
            {
                "alias": "Bad",
                "id": "bad",
                "triggers": [],
                "conditions": [],
                "actions": [],
                "mode": "single",
            }
        )

        original_content = path.read_text(encoding="utf-8")

        validator = FakeValidator(should_pass=False)
        with pytest.raises(ValidationError, match="validation failed"):
            editor.save(validator=validator)

        assert validator.call_count == 1
        # Temp file should be cleaned up
        assert not validator.validated_path.exists()
        # Target file is unchanged
        assert path.read_text(encoding="utf-8") == original_content

    def test_save_without_validator_overwrites_directly(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)

        editor = YAMLEditor(path)
        editor.add_automation(
            {
                "alias": "Direct",
                "id": "direct",
                "triggers": [],
                "conditions": [],
                "actions": [],
                "mode": "single",
            }
        )
        editor.save()  # No validator — direct overwrite

        reloaded = YAMLEditor(path).load()
        assert len(reloaded) == 4

    def test_save_preserves_existing_permissions(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)
        path.chmod(0o600)
        editor = YAMLEditor(path)
        editor.add_automation({"alias": "Perms", "triggers": [], "actions": []})
        editor.save()
        assert path.stat().st_mode & 0o777 == 0o600

    def test_save_without_validator_keeps_original_on_dump_crash(
        self, tmp_path, monkeypatch
    ):
        """save() with no validator must not corrupt the original if dump raises
        mid-write."""
        import pytest

        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        original = "- alias: A\n  triggers: []\n  actions: []\n"
        path.write_text(original)
        editor = YAMLEditor(path)
        editor.load()
        editor.add_automation({"alias": "B", "triggers": [], "actions": []})

        def boom(self, data, target):
            target.write_text("")
            raise RuntimeError("disk full mid-write")

        monkeypatch.setattr(YAMLEditor, "dump", boom)

        with pytest.raises(RuntimeError):
            editor.save()
        assert path.read_text() == original  # original must survive the crash

    def test_save_with_validation_on_empty_data_noop(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "new.yaml"
        editor = YAMLEditor(path)
        validator = FakeValidator(should_pass=True)
        # No data loaded, save is a no-op
        editor.save(validator=validator)
        assert validator.call_count == 0


# ---------------------------------------------------------------------------
# Dict helpers (scripts.yaml support)
# ---------------------------------------------------------------------------


SCRIPTS_FIXTURE = """\
morning_routine:
  alias: Morning Routine
  sequence:
    - action: light.turn_on
      target:
        entity_id: light.kitchen
evening_scene:
  alias: Evening Scene
  sequence:
    - action: scene.turn_on
      target:
        entity_id: scene.evening
"""


class TestDictHelpers:
    def test_add_script(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "scripts.yaml"
        _write_yaml(path, SCRIPTS_FIXTURE)
        editor = YAMLEditor(path)
        editor.add_script("new_script", {"alias": "New Script", "sequence": []})
        editor.save()
        reloaded = YAMLEditor(path).load()
        assert isinstance(reloaded, dict)
        assert "new_script" in reloaded
        assert reloaded["new_script"]["alias"] == "New Script"

    def test_add_script_duplicate_key_raises(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "scripts.yaml"
        _write_yaml(path, SCRIPTS_FIXTURE)
        editor = YAMLEditor(path)
        with pytest.raises(ValueError, match="already exists"):
            editor.add_script("morning_routine", {"sequence": []})

    def test_add_script_on_list_data_raises_type_error(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)
        editor = YAMLEditor(path)
        with pytest.raises(TypeError, match="expected a dict"):
            editor.add_script("x", {"sequence": []})

    def test_update_script(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "scripts.yaml"
        _write_yaml(path, SCRIPTS_FIXTURE)
        editor = YAMLEditor(path)
        editor.update_script("morning_routine", {"description": "Updated"})
        editor.save()
        reloaded = YAMLEditor(path).load()
        assert reloaded["morning_routine"]["description"] == "Updated"

    def test_update_script_missing_key_raises(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "scripts.yaml"
        _write_yaml(path, SCRIPTS_FIXTURE)
        editor = YAMLEditor(path)
        with pytest.raises(ValueError, match="not found"):
            editor.update_script("ghost", {"x": 1})

    def test_remove_script(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "scripts.yaml"
        _write_yaml(path, SCRIPTS_FIXTURE)
        editor = YAMLEditor(path)
        editor.remove_script("evening_scene")
        editor.save()
        reloaded = YAMLEditor(path).load()
        assert "evening_scene" not in reloaded
        assert "morning_routine" in reloaded

    def test_remove_script_missing_key_raises(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "scripts.yaml"
        _write_yaml(path, SCRIPTS_FIXTURE)
        editor = YAMLEditor(path)
        with pytest.raises(ValueError, match="not found"):
            editor.remove_script("ghost")


class TestMappingUpdates:
    @pytest.mark.parametrize("kind", ["automation", "script"])
    def test_mapping_updates_apply_same_merge_contract(self, tmp_path, kind):
        from tools.ha.yaml_editor import YAMLEditor

        if kind == "automation":
            path = tmp_path / "automations.yaml"
            _write_yaml(path, AUTOMATIONS_FIXTURE)
            editor = YAMLEditor(path)
            editor.update_automation("Evening Scene", {"description": "Updated"})
            editor.save()
            data = YAMLEditor(path).load()
            target = data[1]
        else:
            path = tmp_path / "scripts.yaml"
            _write_yaml(path, SCRIPTS_FIXTURE)
            editor = YAMLEditor(path)
            editor.update_script("morning_routine", {"description": "Updated"})
            editor.save()
            data = YAMLEditor(path).load()
            target = data["morning_routine"]

        assert target["description"] == "Updated"
        assert target["sequence" if kind == "script" else "actions"] is not None

    @pytest.mark.parametrize(
        ("kind", "target", "message"),
        [
            ("automation", "Missing Automation", "Automation with alias"),
            ("script", "missing_script", "Script 'missing_script' not found"),
        ],
    )
    def test_mapping_updates_keep_missing_target_errors(
        self, tmp_path, kind, target, message
    ):
        from tools.ha.yaml_editor import YAMLEditor

        if kind == "automation":
            path = tmp_path / "automations.yaml"
            _write_yaml(path, AUTOMATIONS_FIXTURE)
            editor = YAMLEditor(path)
            with pytest.raises(ValueError, match=message):
                editor.update_automation(target, {"description": "Updated"})
        else:
            path = tmp_path / "scripts.yaml"
            _write_yaml(path, SCRIPTS_FIXTURE)
            editor = YAMLEditor(path)
            with pytest.raises(ValueError, match=message):
                editor.update_script(target, {"description": "Updated"})


class TestTypeGuard:
    def test_add_automation_on_scripts_raises_type_error(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "scripts.yaml"
        _write_yaml(path, SCRIPTS_FIXTURE)
        editor = YAMLEditor(path)
        with pytest.raises(TypeError, match="expected a list"):
            editor.add_automation({"alias": "X"})

    def test_update_automation_on_scripts_raises_type_error(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "scripts.yaml"
        _write_yaml(path, SCRIPTS_FIXTURE)
        editor = YAMLEditor(path)
        with pytest.raises(TypeError, match="expected a list"):
            editor.update_automation("x", {})

    def test_remove_automation_on_scripts_raises_type_error(self, tmp_path):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "scripts.yaml"
        _write_yaml(path, SCRIPTS_FIXTURE)
        editor = YAMLEditor(path)
        with pytest.raises(TypeError, match="expected a list"):
            editor.remove_automation("x")

    @pytest.mark.parametrize(
        "operation", ["add_script", "update_script", "remove_script"]
    )
    def test_dict_operations_on_list_raise_type_error(self, tmp_path, operation):
        from tools.ha.yaml_editor import YAMLEditor

        path = tmp_path / "automations.yaml"
        _write_yaml(path, AUTOMATIONS_FIXTURE)
        editor = YAMLEditor(path)
        with pytest.raises(TypeError, match="expected a dict"):
            if operation == "add_script":
                editor.add_script("x", {"sequence": []})
            elif operation == "update_script":
                editor.update_script("x", {})
            else:
                editor.remove_script("x")


# ---------------------------------------------------------------------------
# L15: Additional round-trip and error-propagation tests
# ---------------------------------------------------------------------------


def test_dump_to_round_trip(tmp_path):
    """L15: dump_to(target) writes valid YAML re-readable by load()."""
    from tools.ha.yaml_editor import YAMLEditor

    src = tmp_path / "a.yaml"
    dst = tmp_path / "b.yaml"
    _write_yaml(src, "- alias: A\n  triggers: []\n  actions: []\n")
    e = YAMLEditor(src)
    data = e.load()
    with open(dst, "w", encoding="utf-8") as f:
        e.dump_to(data, f)
    e2 = YAMLEditor(dst)
    e2.load()
    assert e2.find_automation("A") is not None


def test_dump_accepts_string_path(tmp_path):
    from tools.ha.yaml_editor import YAMLEditor

    src = tmp_path / "source.yaml"
    dst = tmp_path / "target.yaml"
    _write_yaml(src, "- alias: A\n")
    data = YAMLEditor(src).load()

    YAMLEditor(src).dump(data, str(dst))

    assert "alias: A" in dst.read_text()


def test_save_propagates_validator_exception(tmp_path):
    """L15: if the validator raises, save() must propagate."""
    from tools.ha.yaml_editor import YAMLEditor

    path = tmp_path / "automations.yaml"
    _write_yaml(path, "- alias: A\n")
    e = YAMLEditor(path)
    e.load()

    def bad_validator(_p):
        raise ValueError("bad")

    with pytest.raises(ValueError, match="bad"):
        e.save(validator=bad_validator)


def test_unicode_alias_round_trip(tmp_path):
    """L15: non-ASCII aliases must survive load -> save -> load."""
    from tools.ha.yaml_editor import YAMLEditor

    path = tmp_path / "automations.yaml"
    _write_yaml(path, "- alias: ☕ Coffee ☕\n  triggers: []\n  actions: []\n")
    e = YAMLEditor(path)
    e.load()
    e.save()
    e2 = YAMLEditor(path)
    e2.load()
    assert e2.find_automation("☕ Coffee ☕") is not None


def test_lazy_empty_file_is_loaded_once(tmp_path, monkeypatch):
    from tools.ha.yaml_editor import YAMLEditor

    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    editor = YAMLEditor(path)
    original_load = editor.load
    calls = 0

    def load_once():
        nonlocal calls
        calls += 1
        return original_load()

    monkeypatch.setattr(editor, "load", load_once)
    assert editor.find_automation("missing") is None
    assert editor.find_automation("missing") is None
    assert calls == 1


def test_lazy_missing_file_stays_save_noop_until_add(tmp_path, monkeypatch):
    from tools.ha.yaml_editor import YAMLEditor

    path = tmp_path / "automations.yaml"
    editor = YAMLEditor(path)
    original_load = editor.load
    calls = 0

    def load_once():
        nonlocal calls
        calls += 1
        return original_load()

    monkeypatch.setattr(editor, "load", load_once)
    assert editor.find_automation("missing") is None
    editor.save()
    assert not path.exists()
    editor.add_automation({"alias": "New"})
    editor.save()
    assert path.exists()
    assert calls == 1
