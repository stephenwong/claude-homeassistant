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

**Anti-pattern:** running `ha_search` and `ha_cli curl /api/states/<id>` for the same
entity — MCP already returns state. If MCP misses, use `grep` against the registry
directly.

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

1. **DISCOVERY** — Search existing automations/scripts and find relevant entities
2. **CLARIFY** — Resolve ambiguity, confirm intent with user
3. **DESIGN** — Plan trigger/condition/action structure, check for needed helpers
4. **IMPLEMENT** — Edit YAML files with targeted changes
5. **DEPLOY** — Validate, push, verify deployment
6. **REFLECT** — Capture learnings via `reflect` skill

| Phase | Tools/Commands | Purpose |
|-------|----------------|---------|
| Discovery | `ha_search` MCP, `grep` registry | Find entities, existing automations/scripts |
| Clarify | Ask the user directly | Resolve ambiguity, confirm intent |
| Design | `Read config/configuration.yaml` | Check helpers (small file, safe to read) |
| Implement | `ha_cli edit`, `Edit` | Modify YAML files |
| Deploy | `make validate`, `make push` | Test and deploy |
| Reflect | `reflect` skill | Capture learnings (gotchas, corrections, patterns) |

## Phase 1: Discovery

**Always start here.** Before writing ANY automation or script:

**Always** use `home-assistant-backup` skill first (pull, backup, preview/apply prune) for configuration changes — even small ones. Read-only audits, including `check-upgrade`, are exempt.

**Don't double-search:** MCP `ha_search` and `grep` hit different indexes. Use MCP first, then `grep` the registry if MCP misses.

1. **Find entities + existing automations/scripts/scenes/helpers by keyword:**
   `ha_search("motion")` — one call searches entity registry AND config bodies

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
| `restart` | Cancels current run, starts over | Motion-activated timers (re-trigger extends) |
| `queued` | Queues triggers, runs sequentially | Sequential processing needed |
| `parallel` | Runs multiple instances simultaneously | Independent per-trigger actions |

**Common gotcha:** Motion-activated lights with timers almost always want `mode: restart` so re-triggering extends the timer instead of being ignored.

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

### Common Patterns

**Motion-activated with timer:**
```yaml
mode: restart  # Re-trigger extends timer
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
  - trigger: device
    device_id: xxx
    subtype: single
    id: single_press
  - trigger: device
    device_id: xxx
    subtype: double
    id: double_press
actions:
  - choose:
    - conditions:
      - condition: trigger
        id: single_press
      sequence:
        - action: light.toggle
          target:
            entity_id: light.room
    - conditions:
      - condition: trigger
        id: double_press
      sequence:
        - action: scene.turn_on
          target:
            entity_id: scene.room
```

**Toggle-gated automation:**
```yaml
conditions:
  - condition: state
    entity_id: input_boolean.feature_toggle
    state: 'on'
```

### Helper Entities

If automation needs state tracking, add YAML-managed helpers to `config/configuration.yaml`:

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

**Note:** New YAML-managed helpers require "Reload all YAML configuration" in HA to appear. UI/storage helpers should be managed through the HA UI or the appropriate MCP helper tool instead.

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

# Add a new automation from JSON
uv run python tools/ha_cli.py edit automations --add '{"alias": "New Automation", "trigger": [], "action": []}'

# Update fields on an existing automation
uv run python tools/ha_cli.py edit automations "Turn on Alarm" --set mode=single icon=mdi:shield
```

**Validation is NOT automatic** (post-edit hooks were removed). Always run `make validate` explicitly after editing — see Phase 5.

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
- Confirm expected behavior with user
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
| Wrong `mode:` for motion timers | Use `restart` so re-triggers extend the timer |
| Using `from:` on motion triggers | Omit `from:` so post-restart transitions aren't missed |
| Mismatched script parameter names | Compare automation `data:` keys with script `fields:` keys exactly |
| Rapid-fire Zigbee commands to same device | Add 250ms `delay` between each command (see AGENTS.md → Zigbee Command Timing) |
| Using raw `state`/`numeric_state` trigger where a purpose-specific one exists (2026.7+) | Prefer `battery.became_low`, area motion, etc. — handles unavailable + supports area targets |
| `ha_cli edit --add` writes JSON strings as bare YAML (`to: on` parsed as bool, `to: null` as None) | After `--add`, re-read with `ha_cli edit automations "Name"` and quote YAML 1.1 booleans/nulls (`on`/`off`/`yes`/`no`/`true`/`false`/`null`), then `make validate` |
| `camera.snapshot` to file + `allowlist_external_dirs` fails local validator (no `/config` on dev box) | Use mobile-app notification `data.image: /api/camera_proxy/camera.xxx` for an automatic snapshot; no file management needed |
| `Edit` tool fails on indentation mismatch | Always `Read` exact lines immediately before `Edit` — don't reuse a stale view |

## Red Flags - You're Doing It Wrong

- Writing automation without searching `config/automations.yaml` first
- Assuming entity exists without verification
- Not asking when multiple sensors/devices could work
- Pushing without validation
- Adding helpers without mentioning reload requirement

**All of these mean: Go back to Phase 1 and follow the workflow.**

## Phase 6: Reflect & Learn

After deployment, use the `reflect` skill to capture any learnings — new gotchas, corrections, or patterns discovered during this work.

**Quick self-check before completing:**
- [ ] Automation/script deployed and validated
- [ ] User confirmed behavior is correct
- [ ] Any learnings documented (if applicable)
