# 💾 Backups, Changelogs & Disaster Recovery

Because configuration files are deployed directly to Home Assistant rather than stored in git history, the **local backup snapshot system (`backups/`)** is your primary safety net and historical record.

---

## 📅 Smart Backup Retention Policy

Running `make backup` creates a local, timestamped `.tar.gz` archive of your configuration. Over time, `prune_backups.py` cleans up old backups automatically according to a 3-tier retention schedule:

```mermaid
flowchart LR
    subgraph Tier1["Tier 1: Days 0 – 7"]
        T1["🗓️ Keep ALL Snapshots<br/>(Full granularity for active debugging)"]
    end

    subgraph Tier2["Tier 2: Days 8 – 30"]
        T2["📅 Keep 1 Snapshot per Day<br/>(Daily milestones)"]
    end

    subgraph Tier3["Tier 3: Day 31+"]
        T3["📆 Keep 1 Snapshot per Week<br/>(Long-term archival)"]
    end

    Tier1 --> Tier2 --> Tier3
```

### Pruning Commands
```bash
# Preview pruning plan without deleting anything (dry-run)
uv run python tools/prune_backups.py

# Apply pruning plan (safe floor ensures at least 3 backups are kept)
uv run python tools/prune_backups.py --apply
```

---

## 🔍 Finding When Something Changed

When an automation stops working or an entity behaves unexpectedly, you can quickly find when it was modified:

```mermaid
flowchart TD
    Q{"What do you need to find?"}
    
    Q -->|"A recent diff / edit"| Diffs["🔎 make changelog-search PATTERN='...'<br/>Fast search through text changelogs"]
    Q -->|"A historical config value"| Archive["📦 make backup-search PATTERN='...'<br/>Searches inside compressed tarball contents"]

    Diffs --> Result["Displays exact timestamp and line diff"]
    Archive --> Result
```

### Useful Search Commands

```bash
# 1. Search text changelogs for modifications to an entity (last 7 days)
make changelog-search PATTERN='climate.living_room' DAYS=7

# 2. Search all backup archives for historical occurrences
make backup-search PATTERN='light.hallway_downlights' LIMIT=5
```

---

## 🚑 Step-by-Step Disaster Recovery Runbook

If you accidentally delete or break an automation and need to restore it from an earlier backup:

```mermaid
flowchart TD
    Find["1. Find target backup timestamp<br/>(e.g. backups/ha_config_20260810_120000.tar.gz)"] --> Extract["2. Extract old file to temporary location<br/>tar -xzOf backups/... config/automations.yaml > /tmp/old.yaml"]
    Extract --> Diff["3. Compare old vs new<br/>diff -u config/automations.yaml /tmp/old.yaml"]
    Diff --> Restore["4. Surgically copy working automation back into automations.yaml"]
    Restore --> Validate["5. make validate"]
    Validate --> Push["6. make push"]
```

### Copy-Paste Restore Commands

```bash
# Step 1: Extract the historical automations file to /tmp
tar -xzOf backups/ha_config_20260810_120000.tar.gz config/automations.yaml > /tmp/old_automations.yaml

# Step 2: Compare differences
diff -u config/automations.yaml /tmp/old_automations.yaml

# Step 3: Once restored, validate and deploy
make validate
make push
```

> [!IMPORTANT]
> **Surgical Restores Only:** Avoid blindly replacing your entire `config/` folder with an old backup archive. Instead, diff and restore only the specific automation, script, or template that broke.
