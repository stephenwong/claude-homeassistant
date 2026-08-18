#!/usr/bin/env python3
"""Fix automation and scene entity IDs in the HA entity registry.

Removes stale entries that block base entity names and renames
suffixed entries to their correct base names.
"""

import json
import sys
from pathlib import Path

REGISTRY_PATH = Path("/config/.storage/core.entity_registry")

# Stale unique_ids to remove from entities (and deleted_entities)
STALE_UNIQUE_IDS = {
    # Automations
    "1638430041429",  # old letter_box_opened
    "letter_box_opened_notification",  # old letter_box_opened_2
    "letterbox_notification",  # old letter_box_opened_3
    "pantry_light_automation_001",  # old pantry_light
    "garage_fan_timer_logic",  # old fan switch automation
    "laundry_motion_automation_001",  # old motion_laundry_light
    "garage_motion_automation_001",  # old motion_garage_lights
    "1767942231798",  # old alert_garage_door_left_open_actionable
    "1768028919630",  # old motion_kids_bedroom
    "1662720384853",  # old appliance_finished_theragun_charging
    "garage_safety_load_shed_001",  # old garage load shedding
    "outside_downlights_v3",  # old motion_outside_downlights_anyone_outside
    "1767223433879",  # old control_media_room_entertainment_environment
    "1767237768110",  # old schedules_study_bedroom_day_night_sync
    "1642218135971",  # old turn_off_alarm
    "1642219529910",  # old turn_on_alarm
    "1767237461369",  # old climate_8am_master_bedroom_heat_alert
    "security_alert_flash_control",  # old security_alert_flash_control
    "1662375313583",  # old turn_off_storage_light_timer
    "1667710833039",  # old reboot_ha_box
    "ensuite_motion_automation_001",  # old motion_ensuite_light
    "1767219922160",  # old control_office_scene_cycle
    "1767223647003",  # old family_room_tv_environment_sync
    "1767489568371",  # old change_kitchen_scene_based_on_input_select
    "1767574575392",  # old motion_ensuite_toilet_light
    "1767749256568",  # old notification_garage_doors_activity_with_timestamp
    "1767770716320",  # old motion_office_lights_new
    "1767777037725",  # old motion_bathroom
    "garage_door_opened_too_long_warning",  # old alert_garage_door_left_open
    "garage_door_notification",  # old garage_door_notification
    "single_garage_door_notification",  # old notification_single_garage_door
    "double_garage_door_notification",  # old notification_double_garage_door
    "single_garage_door_left_open",  # old alert_single_garage_door_left_open
    "double_garage_door_left_open",  # old alert_double_garage_door_left_open
    # Scenes
    "1635884454096",  # old office_play
    "1635884545073",  # old office_night
    "1637730279312",  # old pantry_rainbow
    "1637730427771",  # old pantry_white
    "1637733168361",  # old kitchen_pendants_day
    "1637734676765",  # old kitchen_pendants_night
    "1637735869707",  # old alert_lights
    "1637733168366",  # old kitchen_pendants_disco
    "1644882637614",  # old bed_night
    "1644882699687",  # old bed_day
    "1645594461055",  # old kitchen_pendants_white
}

# Renames: current entity_id -> desired entity_id
RENAMES = {
    # Automations
    "automation.notification_letter_box_opened_4": (
        "automation.notification_letter_box_opened"
    ),
    "automation.control_pantry_light_via_door_sensor_2": (
        "automation.control_pantry_light_via_door_sensor"
    ),
    "automation.control_kitchen_scene_master_button_2": (
        "automation.control_kitchen_scene_master_button"
    ),
    "automation.control_garage_fan_switch_fan_toggle_light_override_2": (
        "automation.control_garage_fan_switch_fan_toggle_light_override"
    ),
    "automation.motion_laundry_light_2": "automation.motion_laundry_light",
    "automation.motion_garage_lights_2": "automation.motion_garage_lights",
    "automation.motion_bathroom_downlight_2": "automation.motion_bathroom_downlight",
    "automation.alert_garage_door_left_open_actionable_2": (
        "automation.alert_garage_door_left_open_actionable"
    ),
    "automation.motion_kids_bedroom_2": "automation.motion_kids_bedroom",
    "automation.appliance_finished_theragun_charging_2": (
        "automation.appliance_finished_theragun_charging"
    ),
    "automation.safety_garage_load_shedding_hardware_protection_2": (
        "automation.safety_garage_load_shedding_hardware_protection"
    ),
    "automation.motion_outside_downlights_anyone_outside_2": (
        "automation.motion_outside_downlights_anyone_outside"
    ),
    "automation.security_alert_flash_control_2": (
        "automation.security_alert_flash_control"
    ),
    "automation.control_media_room_entertainment_environment_2": (
        "automation.control_media_room_entertainment_environment"
    ),
    "automation.schedules_study_bedroom_day_night_sync_2": (
        "automation.schedules_study_bedroom_day_night_sync"
    ),
    "automation.turn_off_alarm_2": "automation.turn_off_alarm",
    "automation.turn_on_alarm_2": "automation.turn_on_alarm",
    "automation.climate_8am_master_bedroom_heat_alert_2": (
        "automation.climate_8am_master_bedroom_heat_alert"
    ),
    # Scenes
    "scene.pantry_rainbow_2": "scene.pantry_rainbow",
    "scene.pantry_white_2": "scene.pantry_white",
    "scene.kitchen_pendants_day_2": "scene.kitchen_pendants_day",
    "scene.kitchen_pendants_night_2": "scene.kitchen_pendants_night",
    "scene.alert_lights_2": "scene.alert_lights",
    "scene.bed_night_2": "scene.bed_night",
    "scene.bed_day_2": "scene.bed_day",
}


def fix_entity_registry_data(data: dict) -> tuple[dict, int, int]:
    """Prune stale unique_ids and apply renames across entities and deleted_entities."""
    removed = 0
    renamed = 0

    data_payload = data.setdefault("data", {})

    for section in ["entities", "deleted_entities"]:
        items = data_payload.get(section, [])
        before = len(items)
        items[:] = [
            item for item in items if item.get("unique_id") not in STALE_UNIQUE_IDS
        ]
        removed += before - len(items)

    for section in ["entities", "deleted_entities"]:
        for item in data_payload.get(section, []):
            old_eid = item.get("entity_id", "")
            if old_eid in RENAMES:
                new_eid = RENAMES[old_eid]
                item["entity_id"] = new_eid
                renamed += 1

    return data, removed, renamed


def main(path: Path | None = None) -> int:
    registry_file = path if path is not None else REGISTRY_PATH
    if not registry_file.is_file():
        print(f"Registry file not found at {registry_file}", file=sys.stderr)
        return 1

    try:
        data = json.loads(registry_file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Failed to read registry JSON: {exc}", file=sys.stderr)
        return 1

    _, removed, renamed = fix_entity_registry_data(data)

    if removed == 0 and renamed == 0:
        print("No changes needed.")
        return 0

    print(f"\nTotal: {removed} removed, {renamed} renamed")
    print(f"Writing {registry_file}...")
    registry_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
