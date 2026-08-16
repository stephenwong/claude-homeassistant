"""Unit tests for config/fix_entity_registry.py."""

import json
from pathlib import Path

from config.fix_entity_registry import (
    fix_entity_registry_data,
    main,
)


def test_fix_entity_registry_data_prunes_and_renames():
    raw_data = {
        "version": 1,
        "key": "core.entity_registry",
        "data": {
            "entities": [
                {
                    "entity_id": "automation.old_letter_box",
                    "unique_id": "1638430041429",
                },
                {
                    "entity_id": "automation.notification_letter_box_opened_4",
                    "unique_id": "good_uid_1",
                },
                {
                    "entity_id": "automation.other",
                    "unique_id": "good_uid_2",
                },
            ],
            "deleted_entities": [
                {
                    "entity_id": "automation.deleted_stale",
                    "unique_id": "pantry_light_automation_001",
                },
                {
                    "entity_id": "automation.control_pantry_light_via_door_sensor_2",
                    "unique_id": "good_uid_3",
                },
            ],
        },
    }

    result, removed, renamed = fix_entity_registry_data(raw_data)

    assert removed == 2
    assert renamed == 2

    assert len(result["data"]["entities"]) == 2
    assert (
        result["data"]["entities"][0]["entity_id"]
        == "automation.notification_letter_box_opened"
    )
    assert result["data"]["entities"][0]["unique_id"] == "good_uid_1"
    assert result["data"]["entities"][1]["entity_id"] == "automation.other"

    assert len(result["data"]["deleted_entities"]) == 1
    assert (
        result["data"]["deleted_entities"][0]["entity_id"]
        == "automation.control_pantry_light_via_door_sensor"
    )


def test_fix_entity_registry_data_no_changes():
    raw_data = {
        "version": 1,
        "key": "core.entity_registry",
        "data": {
            "entities": [{"entity_id": "sensor.clean", "unique_id": "uid_clean"}],
            "deleted_entities": [],
        },
    }
    result, removed, renamed = fix_entity_registry_data(raw_data)
    assert removed == 0
    assert renamed == 0
    assert len(result["data"]["entities"]) == 1


def test_main_file_not_found(tmp_path: Path, capsys):
    missing_file = tmp_path / "missing.json"
    exit_code = main(missing_file)
    assert exit_code == 1
    captured = capsys.readouterr()
    assert f"Registry file not found at {missing_file}" in captured.err


def test_main_invalid_json(tmp_path: Path, capsys):
    bad_file = tmp_path / "corrupted.json"
    bad_file.write_text("{broken json")
    exit_code = main(bad_file)
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Failed to read" in captured.err


def test_main_no_changes_needed(tmp_path: Path, capsys):
    clean_file = tmp_path / "clean.json"
    payload = {"data": {"entities": [{"entity_id": "sensor.temp", "unique_id": "123"}]}}
    clean_file.write_text(json.dumps(payload))

    exit_code = main(clean_file)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "No changes needed." in captured.out


def test_main_applies_changes_atomically(tmp_path: Path, capsys):
    target_file = tmp_path / "core.entity_registry"
    payload = {
        "data": {
            "entities": [
                {
                    "entity_id": "automation.notification_letter_box_opened_4",
                    "unique_id": "good_uid",
                },
                {"entity_id": "stale.ent", "unique_id": "1638430041429"},
            ]
        }
    }
    target_file.write_text(json.dumps(payload))

    exit_code = main(target_file)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Total: 1 removed, 1 renamed" in captured.out
    assert "Done." in captured.out

    saved_data = json.loads(target_file.read_text())
    assert len(saved_data["data"]["entities"]) == 1
    assert (
        saved_data["data"]["entities"][0]["entity_id"]
        == "automation.notification_letter_box_opened"
    )
