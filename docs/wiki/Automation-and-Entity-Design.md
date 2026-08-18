# 📐 Automation & Entity Design Guide

Writing clean, predictable automations keeps your smart home reliable and easy to maintain. This guide covers our naming standards, UI-visible timer patterns, and safe YAML editing.

---

## 🏷️ Entity Naming Convention

All entities in this system follow a standardized **4-segment hierarchical naming pattern**:

$$\Huge\texttt{location\_room\_device\_sensor}$$

```mermaid
flowchart LR
    L["1. Location<br/>(home, office, cabin)"] --> R["2. Room / Zone<br/>(kitchen, hallway, garage)"]
    R --> D["3. Device / Purpose<br/>(motion, downlight, ac)"]
    D --> S["4. Sensor / Function<br/>(occupancy, battery, power)"]

    style L fill:#e3f2fd,stroke:#1565c0
    style R fill:#e8f5e9,stroke:#2e7d32
    style D fill:#fff3e0,stroke:#e65100
    style S fill:#f3e5f5,stroke:#7b1fa2
```

### Real-World Examples

| Entity ID | Domain | Location | Room | Device | Function |
|---|---|---|---|---|---|
| `binary_sensor.home_kitchen_motion_occupancy` | `binary_sensor` | `home` | `kitchen` | `motion` | `occupancy` |
| `sensor.home_living_room_temp_battery` | `sensor` | `home` | `living_room` | `temp` | `battery` |
| `light.home_hallway_downlights` | `light` | `home` | `hallway` | `downlights` | - |
| `timer.home_bathroom_motion_timer` | `timer` | `home` | `bathroom` | `motion` | `timer` |

---

## ⏱️ Motion Lighting: Why We Use `timer.*` Helpers

A common mistake in Home Assistant is writing motion light automations with an inline `for:` delay trigger (e.g. "turn off after 5 minutes of no motion").

Instead, this system uses **explicit `timer.*` helper entities**:

```mermaid
flowchart TD
    subgraph Trigger["1. Motion Detected"]
        M[Motion Sensor Triggered] --> T_Start["action: timer.start<br/>(timer.home_hallway_motion)"]
        T_Start --> L_On["action: light.turn_on<br/>(light.home_hallway)"]
    end

    subgraph Running["2. Active Motion"]
        M2[New Motion While Timer Active] --> T_Restart["action: timer.start<br/>(Resets countdown to full)"]
    end

    subgraph Expiry["3. Timer Finished"]
        T_Done["trigger: event: timer.finished<br/>(entity_id: timer.home_hallway_motion)"] --> L_Off["action: light.turn_off"]
    end
```

### Why Timers are Better than Inline `for:`

```mermaid
flowchart LR
    subgraph Bad["❌ Inline for: 5m trigger"]
        direction TB
        B1["Invisible in Lovelace UI"]
        B2["Cancels if HA restarts"]
        B3["Hard to pause/override"]
    end

    subgraph Good["✅ Explicit timer.* Helper"]
        direction TB
        G1["Real-time countdown bar on Dashboard"]
        G2["Survives HA restarts (if restore enabled)"]
        G3["Easy to extend from UI or scripts"]
    end
```

> [!TIP]
> **Dashboard Visibility:** Because timers are real entities in Home Assistant, your Lovelace room cards can display a live countdown bar showing exactly how many seconds remain before the lights turn off!

---

## ✏️ Safe YAML Editing with `ha_cli edit`

Never use simple text replaces on large YAML files—it destroys comments, changes indentation, and reformats clean lists.

Use the built-in `ha_cli edit` tool (powered by `ruamel.yaml` round-trip parser):

```mermaid
flowchart LR
    Command["uv run python tools/ha_cli.py edit ..."] --> Parser["ruamel.yaml Round-Trip Engine"]
    Parser --> Preserve["Preserves Comments & Key Order"]
    Preserve --> File["config/automations.yaml"]
```

### Common Editing Commands

```bash
# 1. List all automations by alias
uv run python tools/ha_cli.py edit automations

# 2. View a specific automation
uv run python tools/ha_cli.py edit automations "Hallway Motion Lights"

# 3. Add a new automation safely
uv run python tools/ha_cli.py edit automations --add '{"alias":"Garage Open Alert","trigger":[],"action":[]}'

# 4. Modify fields without retyping the whole automation
uv run python tools/ha_cli.py edit automations "Hallway Motion Lights" --set mode=restart icon=mdi:motion-sensor

# 5. Remove an automation cleanly
uv run python tools/ha_cli.py edit automations "Old Test Automation" --remove
```

---

## 🧩 Jinja2 Template Best Practices

Home Assistant uses Jinja2 for dynamic logic. Follow these safety rules:

```mermaid
flowchart TD
    T1["Single-line strings for entity IDs"]
    T2["Use safe state access (states('sensor.x'))"]
    T3["Avoid Ansible-only filters (e.g. quote, default(omit))"]

    T1 --> Rule["Clean, Crash-Resistant Templates"]
    T2 --> Rule
    T3 --> Rule
```

1. **Avoid Multi-Line Whitespace in URLs/IDs**:
   ```yaml
   # ❌ BAD: Line breaks insert unwanted whitespace/newlines
   action:
     - action: notify.mobile_app
       data:
         message: >
           {{ states('sensor.temp') }}
   
   # ✅ GOOD: Keep inline templates compact
   action:
     - action: notify.mobile_app
       data:
         message: "The temperature is {{ states('sensor.temp') }}°C"
   ```

2. **Use `states('sensor.name')` instead of direct attribute access**:
   - `states('sensor.kitchen_temp')` safely returns `'unknown'` if offline.
   - `states.sensor.kitchen_temp.state` will **throw a fatal error** if the sensor is not loaded.
