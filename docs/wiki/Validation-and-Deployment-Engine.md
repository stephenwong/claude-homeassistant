# 🛡️ Validation & Deployment Engine

To prevent broken configurations or bad syntax from crashing your Home Assistant server, this toolkit runs a comprehensive **7-layer validation engine** before every deployment.

---

## 🔄 The Complete Lifecycle

Here is what happens during a standard configuration workflow:

```mermaid
flowchart TD
    Start(["🚀 Start Work"]) --> Protect["💾 1. make backup<br/>(Protect local state)"]
    Protect --> Pull["📥 2. make pull<br/>(Download live HA state)"]
    Pull --> Edit["✏️ 3. Edit YAML / automations<br/>(ha_cli edit or IDE)"]
    Edit --> Validate["🛡️ 4. make validate<br/>(Run 7 parallel validators)"]
    
    Validate --> Check{"All 7 Pass?"}
    
    Check -->|"❌ No (Errors Found)"| Fix["🔍 Fix reported line / entity"]
    Fix --> Validate
    
    Check -->|"✅ Yes (All Pass)"| Push["🚀 5. make push<br/>(Rsync to HA & auto-reload)"]
    Push --> Done(["🎉 Deployment Complete!"])
```

---

## ⚡ The 7 Validation Layers

All 7 validators run concurrently in a multi-threaded pool for maximum speed:

```mermaid
flowchart TB
    Input["📁 Local Config Files & Registries"] --> Pool

    subgraph Pool["⚡ Parallel Execution (ThreadPoolExecutor)"]
        direction TB

        subgraph Offline["🔒 Offline & Cached by SHA256"]
            V1["1️⃣ YAML Syntax<br/>Validates tags, indentation, encoding"]
            V2["2️⃣ Entity References<br/>Verifies all entities exist in registry"]
            V3["3️⃣ Duplicate IDs<br/>Finds duplicate or missing automation IDs"]
        end

        subgraph Online["🌐 Online (Live HA Checks)"]
            V4["4️⃣ Service References<br/>Checks if services (e.g. light.turn_on) exist"]
            V5["5️⃣ Jinja2 Templates<br/>Renders {{ }} against HA template engine"]
            V6["6️⃣ Stale Sensors<br/>Warns if battery sensors are stuck/offline"]
        end

        V7["7️⃣ Official HA check_config<br/>Home Assistant Core validator"]
    end

    Offline --> Results{"Aggregate Results"}
    Online --> Results
    V7 --> Results
    Results --> Pass["✅ PASS: Green light for deployment"]
    Results --> Fail["🛑 FAIL: Blocks deployment with exact line numbers"]
```

### Deep Dive into Each Validator

| # | Validator Name | What It Checks | Why It Matters |
|---|---|---|---|
| **1** | **YAML Syntax** | Validates proper YAML structure, HA tags (`!include`, `!secret`), and UTF-8 encoding | Prevents syntax errors from crashing HA startup. |
| **2** | **Entity References** | Scans YAML and templates to ensure referenced entities (`light.kitchen`, `sensor.bedroom_temp`) exist in `.storage/` | Prevents broken automations from silently failing when an entity is renamed. |
| **3** | **Duplicate IDs** | Verifies every automation has a unique `id:` field | Duplicate IDs cause automations to overwrite each other in the UI and break execution traces. |
| **4** | **Service References** | Verifies services like `light.turn_on` or `climate.set_temperature` are valid | Catches typos like `light.turn_onn` before you deploy. |
| **5** | **Jinja2 Templates** | Evaluates template expressions (`{{ ... }}`) using Home Assistant's `/api/template` endpoint | Catches invalid Jinja filters or missing parentheses. |
| **6** | **Stale Sensors** | Compares `last_updated` timestamps on sensors against a staleness threshold | Alerts you to dead battery sensors or disconnected Zigbee devices. |
| **7** | **Official HA Validation** | Runs Home Assistant's built-in `check_config` script | Guarantees full compatibility with Home Assistant Core. |

---

## ⚡ High-Speed Caching (SHA256)

Running full validation takes time. To keep feedback instant (~0.1s), file-backed validators use a SHA256 hash cache stored in `config/.cache/validators/`.

```mermaid
flowchart LR
    File["📄 automations.yaml"] --> Hash["Compute SHA256<br/>(File Content + Validator Code)"]
    Hash --> Match{"Matches Cache?"}
    Match -->|"Yes (Unchanged)"| Hit["⚡ Instant Cache Hit (0.01s)"]
    Match -->|"No (Changed)"| Run["🔄 Re-run Validator & Update Cache"]
```

### Caching Rules:
- **Instant hits**: If `automations.yaml` hasn't changed, the YAML and Duplicate ID validators finish in under 10ms.
- **Failures never cache**: If an error is detected, it will always re-run until fixed.
- **Force refresh**: Run `uv run python tools/ha_cli.py validate --force` to bypass the cache.

---

## 🌐 Graceful Offline Fallback

If Home Assistant is temporarily restarting or your network is offline, the validation suite does **not** crash. Instead, online validators degrade gracefully:

```mermaid
flowchart TD
    API{"Is HA API Reachable?"}
    API -->|"Yes (Online)"| Full["🌐 Full Online Validation<br/>(Live template evaluation & service registry lookup)"]
    API -->|"No (Offline)"| Fallback["🛡️ Graceful Offline Fallback<br/>• Service refs fall back to regex syntax check<br/>• Templates fall back to brace-balance check<br/>• Stale sensors check is skipped"]
```

---

## 🛠️ Handy Validation Commands

```bash
# Run all validators (auto-detects summary for terminals vs pipes)
uv run python tools/ha_cli.py validate

# Force re-run all validators (ignore cache)
uv run python tools/ha_cli.py validate --force

# Quiet mode (only print errors/warnings)
uv run python tools/ha_cli.py validate --quiet
```
