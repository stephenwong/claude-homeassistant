import json

from tools.remove_lovelace_entity import main, remove_entity_from_lovelace


def test_remove_entity_from_lovelace():
    data = {
        "data": {
            "config": {
                "views": [
                    {
                        "title": "Home",
                        "cards": [
                            {
                                "type": "entities",
                                "title": "Office",
                                "entities": [
                                    {"entity": "switch.office_light"},
                                    {"entity": "input_select.office_scene_select"},
                                    "timer.storage_timer",
                                ],
                            }
                        ],
                    }
                ]
            }
        }
    }
    updated, count = remove_entity_from_lovelace(
        data, "input_select.office_scene_select"
    )
    assert count == 1
    entities = updated["data"]["config"]["views"][0]["cards"][0]["entities"]
    assert len(entities) == 2
    assert {"entity": "switch.office_light"} in entities
    assert "timer.storage_timer" in entities
    assert {"entity": "input_select.office_scene_select"} not in entities


def test_main_missing_args(monkeypatch):
    monkeypatch.setattr("sys.argv", ["remove_lovelace_entity.py"])
    assert main() == 1


def test_main_file_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        ["remove_lovelace_entity.py", str(tmp_path / "nonexistent.json"), "foo"],
    )
    assert main() == 1


def test_main_invalid_json(monkeypatch, tmp_path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("invalid json")
    monkeypatch.setattr("sys.argv", ["remove_lovelace_entity.py", str(bad_file), "foo"])
    assert main() == 1


def test_main_success(monkeypatch, tmp_path):
    json_file = tmp_path / "lovelace.json"
    data = {
        "data": {
            "config": {
                "views": [
                    {
                        "cards": [
                            {
                                "entities": [
                                    {"entity": "foo.bar"},
                                    {"entity": "target.entity"},
                                ]
                            }
                        ]
                    }
                ]
            }
        }
    }
    json_file.write_text(json.dumps(data))
    monkeypatch.setattr(
        "sys.argv", ["remove_lovelace_entity.py", str(json_file), "target.entity"]
    )
    assert main() == 0
    updated = json.loads(json_file.read_text())
    entities = updated["data"]["config"]["views"][0]["cards"][0]["entities"]
    assert len(entities) == 1
    assert entities[0] == {"entity": "foo.bar"}
