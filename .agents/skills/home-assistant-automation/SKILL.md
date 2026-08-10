---
name: home-assistant-automation
description: Use when creating, modifying, or designing Home Assistant automations or scripts — also use when the user describes desired behavior like "when X happens do Y", "notify me when...", "turn on X when...", "schedule X", or any request that implies triggers, conditions, or automated actions
---

# Home Assistant Automation & Script Creation

## Overview

Structured workflow for creating and modifying Home Assistant automations and scripts with entity validation, user clarification, and safe deployment.

## CRITICAL: Context Management

**NEVER read these files directly:**
- `config/.storage/core.entity_registry` (1.7MB JSON)
- `config/.storage/core.device_registry` (96KB JSON)
- `config/automations.yaml` (large runtime snapshot — targeted grep only)

### Common mistakes

**Anti-pattern:** running `ha_search` and `uv run python tools/ha_cli.py curl /api/states/<id>` for the same entity — MCP already returns state. If MCP misses, use `grep` against the registry directly.

**Entity lookup fallback ladder (live HA):**
1. **`ha_search` MCP** — one call searches entity registry AND automation/script/scene/helper configs.
2. **`grep "exact.id" config/.storage/core.entity_registry`** — known exact ID only, last resort.
3. **`ha_get_state("<id>")`** — verify current state by known entity_id.

## When to Use

- Creating new automations or scripts
- Modifying existing automations or scripts
- Adding new triggers/conditions/actions
- User describes desired behavior in natural language:
  - "when X happens, do Y"
  - "notify/alert me when..."
  - "turn on/off X when..."
  - "schedule X to..."
  - "I want X to automatically..."
  - "play camera when doorbell rings"
  - "can you make it so the lights..."
- Editing `config/automations.yaml` or `config/scripts.yaml`

**When NOT to use:**
- Dashboard-only changes (no automation involved)
- Integration setup (`config/configuration.yaml` changes without automations)
- Debugging broken automations — use `home-assistant-debugging` skill instead

## Workflow

1. **PRE-FLIGHT** — Use `home-assistant-backup` before any configuration change
2. **DISCOVERY** — Search existing automations/scripts and find relevant entities
3. **CLARIFY** — Resolve ambiguity, confirm intent with user
4. **DESIGN** — Plan trigger/condition/action structure, check for needed helpers
5. **IMPLEMENT** — Edit YAML files with targeted changes
6. **DEPLOY** — Validate, push, verify deployment
7. **REFLECT** — Capture learnings via `reflect` skill

| Phase | Tools/Commands | Purpose |
|-------|----------------|---------|
| Pre-flight | `home-assistant-backup` skill | Protect local state before any pull or edit |
| Discovery | `ha_search` MCP, targeted `grep` | Find entities, existing automations/scripts |
| Clarify | Ask the user directly | Resolve ambiguity, confirm intent |
| Design | `Read config/configuration.yaml` | Check helpers (small file, safe to read) |
| Implement | `ha_cli edit`, `Edit` | Modify YAML files |
| Deploy | `make validate`, `make push` | Test and deploy |
| Reflect | `reflect` skill | Capture learnings (gotchas, corrections, patterns) |

## Phase 1: Discovery

Before writing ANY automation or script, complete the backup skill's pre-flight. It must run before discovery when a pull or edit could overwrite local state. Read-only audits, including `check-upgrade`, are exempt.

If changing an existing entity ID, helper type, trigger semantics, or automation structure, read the connected best-practice `safe-refactoring.md` reference first. Search every consumer, including dashboards, scripts, scenes, helpers, config-entry data, and storage-mode dashboards, before changing it; verify that no stale references remain afterward.

**Don't double-search:** MCP `ha_search` and `grep` hit different indexes. Use MCP first, then `grep` the registry if MCP misses.

1. **Find entities + existing automations/scripts/scenes/helpers by keyword:**
   `ha_search("motion")` — one call searches entity registry AND config bodies

   Check `partial`, `partial_reason`, `warnings`, and `errors` on every response. A partial or skipped result is not evidence that no match exists. Paginate entity and configuration results independently with their respective `next_offset` values.

2. **List all automations:**
   `ha_search(domain_filter="automation", limit=100)`, then paginate with `offset=next_offset` while `has_more` is true.

3. **Find a specific automation by name or keyword:**
   `ha_search("doorbell")`

4. **Find entities by area or room:**
   `ha_search(area_filter="bathroom", domain_filter="binary_sensor")`

5. **Verify a specific entity exists:**
   `ha_search("bathroom_motion")`

**Safe to read directly:**
- `config/configuration.yaml` — Small, contains helpers/integrations

**Entity naming convention:** `location_room_device_sensor`
- Example: `binary_sensor.home_basement_motion_battery`

## Phase 2: Clarify

**ALWAYS ask when:**
- Multiple sensors could work (which motion sensor?)
- Multiple locations involved (which room?)
- Timing is ambiguous (day only? always?)
- Behavior has options (toggle vs always-on?)
- Automation mode matters (see Phase 3)

**Example clarification questions:**
- "I found 3 motion sensors in the basement. Which should trigger this?"
- "Should this run only during certain hours?"
- "What automation mode: single (ignore new triggers), restart, or queued?"

**DO NOT assume.** Even if one option seems obvious, confirm with user.

## Phase 3: Design

### Automation ID Convention

Use descriptive slugs for automation IDs:
```yaml
- id: doorbell_camera_to_nest_hub
- id: basement_motion_lights
- id: nightly_porch_light_off
```

### Automation Mode

Choose `mode:` based on behavior needs (default is `single`):

| Mode | Behavior | Use When |
|------|----------|----------|
| `single` | Ignores new triggers while running | One-shot actions (notifications) |
| `restart` | Cancels current run, starts over | Long-running delay/wait sequences where a re-trigger should restart them |
| `queued` | Queues triggers, runs sequentially | Sequential processing needed |
| `parallel` | Runs multiple instances simultaneously | Independent per-trigger actions |

**Common gotcha:** `mode: restart` matters only while the automation run is still active. In a timer-helper pattern where the run ends immediately after `timer.start`, the timer service restarts the running timer; use `restart` when the automation itself contains a long-running delay or wait.

### Trigger Gotchas

**`from:` constraint drops post-restart events:** After HA restarts, entities start in `unknown`/`unavailable`. Triggers with `from: ['off']` miss the first transition (e.g., `unknown -> on`). Only use `from:` when you specifically need to ignore startup transitions. For motion sensors, omit `from:`.

**`for:` duration on triggers:** The entity must remain in the target state for the entire duration. If state flickers, HA restarts, or automations reload, the timer resets. Useful for "door open for 5 minutes" alerts, not for instant triggers.

### Purpose-specific Triggers & Conditions (HA 2026.7+)

As of HA 2026.7, **purpose-specific triggers and conditions are the new default** (graduated from Labs, introduced 2025.12). Describe *what* you want to react to, not *which entity/state*. Integrations can now ship their own triggers/conditions (e.g. a washing-machine integration offering "laundry is done" directly).

**Prefer these over raw `state`/`numeric_state` triggers for new automations** when a purpose-specific one exists. They:
- Handle `unknown`/`unavailable` states automatically (no manual guards).
- Avoid the event-entity "state didn't change the second time" trap.
- Support **area targets** — "motion in the living room" instead of one sensor entity, so swapping/adding sensors later doesn't break the automation.

**Renamed keys (HA 2026.7 — old Labs-era keys are DEAD):** Replace the old key with the new one when migrating.

| Domain | Old key (Labs) | New key (2026.7) |
|--------|----------------|-----------------|
| battery | `battery.low` | `battery.became_low` |
| battery | `battery.not_low` | `battery.no_longer_low` |
| lawn_mower | `lawn_mower.docked` | `lawn_mower.returned_to_dock` |
| schedule | `schedule.turned_off` | `schedule.block_ended` |
| schedule | `schedule.turned_on` | `schedule.block_started` |
| timer | `timer.time_remaining` | `timer.remaining_time_reached` |
| update | `update.update_became_available` | `update.became_available` |
| vacuum | `vacuum.docked` | `vacuum.returned_to_dock` |
| climate (condition) | `climate.target_humidity` | `climate.is_target_humidity` |
| climate (condition) | `climate.target_temperature` | `climate.is_target_temperature` |

**Existing automations keep working** — generic `state`/`numeric_state`/`template` triggers and all YAML are untouched. This is the better *starting point* for new automations, not a migration tax.

When a purpose-specific trigger or condition exposes `target` or `options`, inspect its integration-specific schema and set the behavior explicitly when the choice affects whether one or every matching entity must qualify. Trigger behaviors are `each` (default), `first`, or `all`; condition behaviors are `any` (default) or `all`. Do not emit old trigger values such as `any` or `last`, and do not assume that an area target or a purpose-specific trigger has the same semantics as a raw state trigger.

### Native Logic Before Templates

Use native Home Assistant constructs for control flow and comparisons whenever one exists: `state`, `numeric_state`, `time`, `sun`, `wait_for_trigger`, `choose`, and literal `target.entity_id` values. Reserve templates for dynamic data, notification text, event data, and variables. Never template an entity ID, service name, trigger/condition logic, or action target when a native field can express it.

### Common Patterns

**Motion-activated with timer:**
```yaml
# timer.start restarts a running timer; no mode override is needed when this run ends here.
triggers:
  - trigger: state
    entity_id: binary_sensor.room_motion
    to: 'on'
actions:
  - action: light.turn_on
    target:
      entity_id: light.room
  - action: timer.start
    target:
      entity_id: timer.room_timer
# Add a timer.finished trigger/branch to turn the light off.
```

**Multi-trigger with choose:**
```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.room_motion
    to: "on"
    id: motion_on
  - trigger: state
    entity_id: binary_sensor.room_motion
    to: "off"
    id: motion_off
actions:
  - choose:
    - conditions:
      - condition: trigger
        id: motion_on
      sequence:
        - action: light.turn_on
          target:
            entity_id: light.room
    - conditions:
      - condition: trigger
        id: motion_off
      sequence:
        - action: light.turn_off
          target:
            entity_id: light.room
```

For ZHA buttons/remotes, use an `event` trigger on `zha_event` with the persistent `device_ieee` and copy the actual `command` from Developer Tools > Events. Zigbee2MQTT autodiscovered device triggers are an acceptable exception; otherwise prefer readable `entity_id` state triggers over `device_id`, which changes when a device is re-added.

**Toggle-gated automation:**
```yaml
conditions:
  - condition: state
    entity_id: input_boolean.feature_toggle
    state: 'on'
```

### Helper Entities

If automation needs state tracking, first check whether a built-in or UI/config-flow helper fits (`input_boolean`, `timer`, `counter`, `schedule`, `group`, `min_max`, `threshold`, `derivative`, and similar). Create storage/config-entry helpers through the HA UI or the appropriate MCP helper tool. Add a YAML-managed helper to `config/configuration.yaml` only when the helper is already YAML-managed, the user explicitly requests YAML, or no managed creation path is available:

```yaml
input_boolean:
  feature_toggle:
    name: Feature Toggle
    icon: mdi:toggle-switch

timer:
  room_timer:
    name: Room Timer
    duration: "00:10:00"
```

**Note:** New YAML-managed helpers require `homeassistant.reload_all` (or the helper domain's specific reload action) in HA to appear. The repository reload tool now uses `homeassistant.reload_all` for `configuration.yaml` changes; verify the helper exists after `make push`. UI/storage helpers should not be hand-written into `configuration.yaml`; manage them through the HA UI or the appropriate MCP helper tool instead.

### Scripts

Scripts live in `config/scripts.yaml` and follow the same validation/deployment workflow. Use scripts for reusable action sequences called from multiple automations:

```yaml
# scripts.yaml
debug_log:
  alias: Debug Log
  fields:
    message:
      description: Log message
  sequence:
    - action: system_log.write
      data:
        message: "{{ message }}"
```

**Parameter name gotcha:** Template variable names must match the keys passed in the automation or script `data:`. `fields` documents UI inputs but does not enforce them. With `| default(omit)` patterns, mismatches silently fail.

## Phase 4: Implement

This repository's source of truth is the local YAML snapshot, so use `ha_cli edit` and deploy through `make validate`/`make push`. Use the HA config API or MCP write tools only for an explicitly requested live hotfix, then back-sync the local snapshot.

**Rules for editing:**
1. **Prefer `ha_cli edit`** for automations/scripts — it uses `ruamel.yaml` for round-trip editing that preserves comments, formatting, and key ordering. Manual `Edit` is fine for `config/configuration.yaml` or small targeted changes.
2. Make focused, targeted edits (not wholesale rewrites)
3. Preserve existing automation IDs
4. Use exact string matching for the `Edit` tool
5. One logical change per edit when possible

```bash
# List all automation aliases
uv run python tools/ha_cli.py edit automations

# Show a specific automation
uv run python tools/ha_cli.py edit automations "Turn on Alarm"

# Add a new automation from JSON. Include a stable ID for editor changes and traces.
uv run python tools/ha_cli.py edit automations --add '{"id": "new_automation", "alias": "New Automation", "triggers": [], "conditions": [], "actions": []}'

# Update fields on an existing automation
uv run python tools/ha_cli.py edit automations "Turn on Alarm" --set mode=single icon=mdi:shield
```

**Validation is NOT automatic** (post-edit hooks were removed). Always run `make validate` explicitly after editing — see Phase 5. Confirm `configuration.yaml` includes the file being edited (`automation: !include automations.yaml` and `script: !include scripts.yaml`) before deployment.

## Phase 5: Deploy & Verify

```bash
# Validate all changes
make validate

# If validation passes, deploy
make push
```

**Validation checks:**
- YAML syntax
- Entity reference existence
- Duplicate automation IDs
- Service reference validity (warns on unknown, errors on malformed)
- Jinja2 template rendering (errors on syntax, warns on runtime context)
- Stale sensor detection (warns by default; `HA_STALE_FAIL=1` or `--fail-on-stale` to fail)
- Official HA configuration validation

**Post-deploy verification:**
- Check `last_triggered` after testing with `ha_get_state("automation.X", fields=["state", "attributes"], attribute_keys=["last_triggered"])`
- For non-trivial automations, inspect the retained automation trace to confirm the matched trigger, conditions, action path, and any errors. YAML automations need an `id` for traces.
- Confirm the resulting entity state or notification with the user; `last_triggered` alone does not prove that actions succeeded.
- For time-based triggers, verify the schedule through the HA UI, a trace, or an actual test run; do not assume a `next_trigger` state field exists.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Reading entire entity_registry (1.7MB JSON) | Use `ha_search` MCP or `Grep` |
| Reading entire `config/automations.yaml` | Use `Grep` or `ha_cli edit automations` to find specific sections |
| Using entity without verifying existence | Use `ha_search` MCP to validate |
| Assuming which sensor to use | Ask user when multiple options |
| Large wholesale file rewrites | Use targeted Edit calls |
| Skipping validation | Always run `make validate` |
| Not checking for needed helpers | Review if timers/toggles needed |
| Using `camera.play_stream` for Frigate | Use `media_player.play_media` with go2rtc (see AGENTS.md) |
| Multi-line Jinja for URLs/IDs | Use single-line templates to avoid whitespace |
| Using `media_player.media_stop` for Cast | Use `turn_off` to return to ambient mode |
| Wrong `mode:` for motion timers | Use `restart` for a long-running delay/wait; `timer.start` itself restarts a running timer |
| Using `from:` on motion triggers | Omit `from:` so post-restart transitions aren't missed |
| Mismatched script parameter names | Compare automation `data:` keys with script `fields:` keys exactly |
| Rapid-fire Zigbee commands to same device | Add 250ms `delay` between each command (see AGENTS.md → Zigbee Command Timing) |
| Using raw `state`/`numeric_state` trigger where a purpose-specific one exists (2026.7+) | Prefer `battery.became_low`, area motion, etc. — handles unavailable + supports area targets |
| Using `device_id` for a regular sensor/button trigger | Prefer a stable `entity_id`; for ZHA stateless buttons use `zha_event` with `device_ieee`, while Z2M autodiscovered device triggers are acceptable |
| Adding `enabled: false` as a top-level automation key | Disable the automation with `automation.turn_off` or the entity registry; `enabled` is valid on individual triggers, conditions, and actions, not at the automation root |
| Creating a template sensor or binary sensor before checking helpers | Prefer a built-in helper or a UI/config-flow Template Helper; use YAML `template:` only when explicitly requested or no managed path exists |
| `ha_cli edit --add` writes JSON strings as bare YAML (`to: on` can be parsed as a boolean) | After `--add`, re-read with `ha_cli edit automations "Name"` and quote boolean-like state strings (`on`/`off`/`yes`/`no`/`true`/`false`) when a string is intended, then `make validate`. Preserve unquoted `to: null` or `from: null` when the state-trigger wildcard that ignores attribute-only changes is intended. |
| `camera.snapshot` to file + `allowlist_external_dirs` fails local validator (no `/config` on dev box) | The `/api/camera_proxy/camera.xxx` automatic-snapshot attachment is Android Companion-app guidance, not a universal notification path. For cross-platform notifications, write to an allowed `/config/www` path and use its `/local/...` URL; ensure the receiving device can reach it. |
| `Edit` tool fails on indentation mismatch | Always `Read` exact lines immediately before `Edit` — don't reuse a stale view |

## Red Flags - You're Doing It Wrong

- Writing automation without searching `config/automations.yaml` first
- Assuming entity exists without verification
- Not asking when multiple sensors/devices could work
- Pushing without validation
- Adding helpers without mentioning reload requirement

**All of these mean: Go back to Phase 1 and follow the workflow.**

## References

- [Automations in YAML](https://www.home-assistant.io/docs/automation/yaml/)
- [Automation triggers](https://www.home-assistant.io/docs/automation/trigger/)
- [Automation conditions](https://www.home-assistant.io/docs/automation/condition/)
- [Automation actions](https://www.home-assistant.io/docs/automation/action/)
- [Automation modes](https://www.home-assistant.io/docs/automation/modes/)
- [Testing and troubleshooting automations](https://www.home-assistant.io/docs/automation/troubleshooting/)
- [Home Assistant script integration](https://www.home-assistant.io/integrations/script/)
- [Start a timer](https://www.home-assistant.io/actions/timer.start/)
- [Home Assistant device triggers](https://www.home-assistant.io/docs/automation/trigger/#device-triggers)
- [Companion app notification attachments](https://companion.home-assistant.io/docs/notifications/notification-attachments/)
- [Reload all YAML configuration](https://www.home-assistant.io/actions/homeassistant.reload_all/)
- Connected `home-assistant-best-practices` guide: read `automation-patterns.md`, `helper-selection.md`, `device-control.md`, and `template-guidelines.md` for the corresponding decision.

## Phase 6: Reflect & Learn

After deployment, use the `reflect` skill to capture any learnings — new gotchas, corrections, or patterns discovered during this work.

**Quick self-check before completing:**
- [ ] Automation/script deployed and validated
- [ ] User confirmed behavior is correct
- [ ] Any learnings documented (if applicable)
