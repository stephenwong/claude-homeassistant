---
name: home-assistant-debugging
description: Use when investigating Home Assistant issues - entity behavior problems, automation failures, unexpected states after restart, template sensor bugs. Also use when user says "X isn't working", "X stopped working", "why does X do Y", or "something broke".
---

# Home Assistant Debugging

## Overview

Systematic approach to debugging Home Assistant issues. Find root cause before proposing fixes.

**Core principle:** Trace the problem to its source - whether in templates, automations, or entity configuration.

## CRITICAL: Context Management

**NEVER read these files directly:**
- `config/.storage/core.entity_registry` (1.7MB JSON)
- `config/.storage/core.device_registry` (96KB JSON)
- `config/automations.yaml` (large runtime snapshot — targeted grep only)

### Common mistakes

**Anti-pattern:** checking an entity's state with BOTH `uv run python tools/ha_cli.py curl /api/states/X` AND `ha_get_state("X")`. They return the same data; pick one.
Prefer `ha_get_state` with `fields=["state","last_changed"]` for a compact response; exact token counts vary with the entity and projection.

**Shrink MCP responses to save tokens:**
- `ha_get_overview` with `fields=["system_info"]` to project one section (diagnostics are always retained).
- `ha_search` with `fields=["entities"]` and `limit=5` instead of the full payload.
- `ha_get_state` with `fields=["state","last_changed"]` instead of full attributes.
- Prefer `detail_level="minimal"` (the default) — escalate to `standard`/`full` only when you need attributes.

## When to Use

- Entity shows wrong state after HA restart
- Automation not triggering or triggering incorrectly
- Template sensor returning unexpected values
- "unavailable" → wrong state transitions
- User reports "X stopped working" or "X behaves strangely"
- User asks "why does X do Y" or "something broke"

**When NOT to use:**
- Creating new automations (use home-assistant-automation)
- Dashboard layout issues
- Pure configuration questions with no malfunction

## Workflow

1. **Identify** — Find the entity, note domain/class
2. **Locate** — Find where entity is defined in config
3. **Analyze** — Trace logic, identify failure mode
4. **Fix** — Protect configuration, propose minimal fix, validate, deploy
5. **Reflect** — Capture learnings via `reflect` skill

| Phase | Tools/Commands | Purpose |
|-------|----------------|---------|
| Identify | `ha_search` MCP, `ha_get_state` MCP | Find entity, check current state |
| Locate | `Grep` config files, `make backup-search` | Find definition and history |
| Analyze | `Read` (targeted lines), automation traces | Understand template/automation logic |
| Fix | `ha_cli edit`, `Edit`, `make validate`, `make push` | Apply and deploy fix |
| Reflect | `reflect` skill | Capture learnings (gotchas, corrections, patterns) |

## Phase 1: Identify the Entity

**Quick state checks via MCP ha_get_state:**
1. **Check current entity state and attributes:**
   `ha_get_state("sensor.entity_name")`

2. **Check automation status — look for "last_triggered":**
   `ha_get_state("automation.automation_name", fields=["state", "attributes"], attribute_keys=["last_triggered"])`

3. **Check when an entity last changed state:**
   `ha_get_state("binary_sensor.entity_name", fields=["state", "last_changed", "last_updated"])`

**`last_triggered` and `last_changed` are useful first checks:**
- If `last_triggered` is `null`, the automation has not fired since registration; if it is old, it fired previously but not recently. In either case, compare the expected event time with the trigger and trace evidence.
- If `last_triggered` is recent but nothing happened → check conditions/actions
- If `last_changed` is old, the entity state may not be updating → check source and expected reporting cadence
- If `last_changed == last_updated` and both are old, treat the sensor as a **stale candidate** — a stable entity can also have equal timestamps. Corroborate with expected reporting cadence, history, heartbeat attributes, and integration diagnostics. Confirm with `ha_get_state("<id>", fields=["state","last_changed","last_updated"])`. See Common Failure Patterns: "Sensor value frozen"

**Note:**
- Device class (moisture, occupancy, presence) hints at sensor type
- Domain (binary_sensor, sensor, input_boolean) indicates definition location

## Phase 2: Locate the Definition

1. **Find where an entity is defined or referenced:**
   `ha_search("entity_name")` — searches config bodies (`config/configuration.yaml`, `config/automations.yaml`, helpers)

2. **Narrow to a specific config type:**
   `ha_search("entity_name", search_types=["automation", "helper"])`

**Where entities are defined by type:**

| Entity Pattern | Defined In | Modifiable |
|----------------|------------|------------|
| `binary_sensor.*` / `sensor.*` (template) | `config/configuration.yaml` (`template:` section) | Yes |
| `input_boolean.*`, `timer.*`, `input_datetime.*` | `config/configuration.yaml` (helpers section) | Yes |
| Automations | `config/automations.yaml` | Yes |
| `binary_sensor.*` / `sensor.*` (integration) | Integration (e.g., Z2M, Frigate) | No* |

*Integration entities can only be modified via integration config. For debugging integration entities, see "Integration Debugging" below.

### Integration Debugging

When the problem entity comes from an integration (Zigbee2MQTT, Frigate, etc.):

```bash
# Check integration logs via SSH
ssh homeassistant "ha apps logs 45df7312_zigbee2mqtt" | tail -50   # Z2M
ssh homeassistant "ha apps logs ccab4aaf_frigate-fa-beta" | tail -50  # Frigate

# Z2M web UI: check device status, interview, reconfigure
# Frigate web UI: check camera feeds, detection zones

# Verify MQTT connectivity
ha_get_state("binary_sensor.zigbee2mqtt_bridge_connection_state")
```

### Finding When a Change Was Introduced

If the user says "this worked before" or "this broke recently", use backup search to find when it changed:

```bash
# Search all backups for a specific pattern
make backup-search PATTERN='media_player.play_media'

# Check changelogs for what changed in each backup
cat backups/ha_config_YYYYMMDD_HHMMSS.changelog
```

## Phase 3: Analyze Root Cause

### Common Failure Patterns

| Symptom | Likely Cause | Where to Look |
|---------|--------------|---------------|
| Wrong state after restart | Template doesn't handle `unavailable` | `config/configuration.yaml` template |
| Automation not triggering | Trigger condition never met | `config/automations.yaml` triggers |
| Entity always "on" | Template logic flaw | `config/configuration.yaml` template |
| "unavailable" persists | Source entity offline | Check source entity status |
| Sensor value frozen (plausible reading, never updates; battery reports healthy) | Possible stale Zigbee/mesh sensor — corroborate with cadence, history, and integration diagnostics | `ha_get_state("<id>", fields=["state","last_changed","last_updated"])`; equal old timestamps are a stale candidate, not proof. Re-interview/re-pair in Z2M only after confirming the source (see AGENTS.md → Zigbee Stale Sensors) |
| State flip-flops | Missing debounce/delay_off | Template or automation |
| User says "X on all day" but recorder shows off | Possible post-restart Zigbee actuator desync (bulb on, HA off — Z2M didn't resync) | Check the physical light and HA state; consider startup reconciliation only after confirming the mismatch (see AGENTS.md → Post-restart Zigbee actuator desync) |

### Template Sensor Debugging

**Trigger-based templates** can be vulnerable after an HA restart when the source entity transitions from `unknown`/`unavailable` to its first reading. In that case:
- `trigger.from_state` may be `none` or may contain an `unknown`/`unavailable` state
- `| float(0)` converts an unavailable string to 0
- A large delta from 0 to a real value can trigger a false positive

```yaml
# PROBLEM: Doesn't handle unavailable → available transition
- trigger:
    - trigger: state
      entity_id: sensor.humidity
  binary_sensor:
    - name: "Shower Occupancy"
      state: >
        {% set old = trigger.from_state.state | float(0) %}
        {% set new = trigger.to_state.state | float(0) %}
        # BUG: "unavailable" becomes 0, causing false triggers
```

**Fix pattern - always guard trigger-based templates:**

```yaml
state: >
  {% set current = this.state if this.state in ['on', 'off'] else 'off' %}
  {% set old_state = trigger.from_state.state if trigger.from_state is not none else 'unavailable' %}
  {% set new_state = trigger.to_state.state if trigger.to_state is not none else 'unavailable' %}
  {% if old_state in ['unavailable', 'unknown'] or new_state in ['unavailable', 'unknown'] %}
    {{ current }}
  {% else %}
    {# Normal logic here #}
  {% endif %}
```

### Automation Debugging

**Find the automation:** `ha_search("automation_name_or_keyword")`. Check `partial`, `partial_reason`, `warnings`, and `errors`; paginate each result surface independently before concluding that no match exists.

**Check automation traces** for execution history:
- HA UI: Settings > Automations > find automation > three-dot menu > Traces
- **MCP: `ha_get_automation_traces("automation.<name>")`** — preferred. Returns retained recent runs; pass a `run_id` for full step-by-step detail (trigger matched, conditions evaluated, actions executed).
- CLI: `uv run python tools/ha_cli.py trace` (no arg) — lists traces across ALL automations. `uv run python tools/ha_cli.py trace automation.<name>` — fetch a specific automation's trace.
- YAML automations need a stable `id` for debug traces to be stored. If no traces exist, check the ID and trace retention before concluding that the trigger did not fire or the trace expired.
- If traces show condition failure, read the condition values at that timestamp
- **(HA 2026.7+, when supported by the running version)** Traces include template errors, so a clean trace means templates didn't error — not that templates weren't evaluated.

**Common automation issues:**
- `for:` duration prevents quick triggers
- `condition: state` blocks when entity unavailable
- Wrong `entity_id` (typo or renamed entity)
- `mode: single` ignores triggers while running

## Phase 4: Fix and Deploy

1. **Propose minimal fix** - explain the change to user
2. **Protect local state:** run the `home-assistant-backup` pre-flight before editing
3. **Get approval** - don't fix without confirmation
4. **Make targeted edit** - smallest change that fixes the issue
5. **Validate:** `make validate`
6. **Deploy:** `make push`

## Debugging Workflow for Service Failures

When a service call doesn't work as expected:

1. **Test the service directly** via MCP `ha_call_service` only with explicit user approval and a safe target; service calls can affect real devices
2. **Check entity states** via `ha_get_state` - is the target in expected state?
3. **Check automation traces** (Settings > Automations > Traces) - did the automation run? Did each step succeed?
4. **Check HA logs** — via SSH (full logs with follow):

```bash
# Follow logs in real-time (useful for reproducing issues)
ssh homeassistant "ha core logs --follow"

# Addon-specific logs
ssh homeassistant "ha apps logs 45df7312_zigbee2mqtt"   # Z2M
ssh homeassistant "ha apps logs ccab4aaf_frigate-fa-beta"  # Frigate
```

This isolates whether the problem is:
- The service itself (500 error, not supported)
- The automation logic (service works manually but not via automation)
- Entity state conditions (automation not triggering)

## Direct HA API Access (Fallback)

**Prefer MCP for normal debugging.** Use the repository's explicit CLI form when MCP is unavailable:

| Task | MCP (preferred) | ha_cli (fallback) |
|------|-----------------|-------------------|
| Check entity state | `ha_get_state("X", fields=["state","last_changed"])` | `uv run python tools/ha_cli.py curl /api/states/X --pretty` |
| Fetch automation trace | `ha_get_automation_traces("automation.X")` | `uv run python tools/ha_cli.py trace automation.X` |
| List all traces | No MCP equivalent | `uv run python tools/ha_cli.py trace` (no arg) |
| Fetch system logs | `ha_get_logs(source="system", level="ERROR", limit=30)` | SSH `ha core logs`; `/api/logbook/...` is entity activity, not system logs |

### When New Entities Need a Reload

If validation fails because new helpers/templates do not exist yet, do not assume a raw transfer is safe:

There is no supported validation-bypass target. Do not use an unfiltered `rsync`: it would bypass `.rsync-excludes-push` and could overwrite HA-managed runtime state. Prefer fixing the validator or using a targeted, explicitly approved hotfix that reproduces the configured push exclusions, then reload the affected domains.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Reading entire entity_registry | Use `ha_search` MCP or `Grep` |
| Proposing fix before finding root cause | Complete Phase 3 first |
| Guessing the problem | Trace the actual logic |
| Fixing symptoms not cause | Ask "why does this happen?" |
| Large rewrites | Minimal targeted changes only |
| Not checking for unavailable states | Templates must handle unavailable |

### Tool Efficiency

| Inefficiency | Better path |
|--------------|-------------|
| Looping a light group's members serially | Query the group entity once for aggregate state; bulk-query members with `ha_get_state` when individual states matter |
| Post-deploy entity verification via MCP `ha_search` | Use the canonical entity ID returned by the write/search/registry lookup, then call `ha_get_state`; do not guess slugs |
| Inline `uv run python -c "..."` to filter JSON | Use the CLI's `--pick` / `--first` / `--max-chars` flags — avoids spawning a full interpreter per filter |
| Speculative MCP `/api/logbook` calls with no hypothesis | Skip unless you have a specific question; they usually return noise |
| Pulling `ha_get_history` for an entity you have no hypothesis about (e.g., the light's on/off log while diagnosing a *guard* sensor failure) | Skip it — `history` answers "when did X change," so only pull it for the entity your hypothesis targets. For a "is this sensor stale?" question, lead with `ha_get_state("<id>", fields=["state","last_changed","last_updated"])`, then use `ha_get_history` only to compare it with the expected reporting cadence |

## Red Flags - You're Doing It Wrong

- Proposing fixes without reading the entity definition
- Reading large registry files directly
- "I think the problem might be..." without evidence
- Changing multiple things at once
- Skipping validation before push
- Not explaining root cause to user

**All of these mean: Go back to Phase 2 and trace the actual code.**

## References

- [Testing and troubleshooting automations](https://www.home-assistant.io/docs/automation/troubleshooting/)
- [Automation YAML IDs and traces](https://www.home-assistant.io/docs/automation/yaml/)
- [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/)
- [Home Assistant state trigger](https://www.home-assistant.io/triggers/state/)
- [Home Assistant logs](https://www.home-assistant.io/integrations/logger/)

## Phase 5: Reflect & Learn

After fixing the issue, use the `reflect` skill to capture any learnings — new failure patterns, documentation gaps, or gotchas discovered during debugging.

**Quick self-check before completing:**
- [ ] Root cause identified and explained to user
- [ ] Fix deployed and validated
- [ ] User confirmed issue is resolved
- [ ] Any learnings documented (if applicable)
