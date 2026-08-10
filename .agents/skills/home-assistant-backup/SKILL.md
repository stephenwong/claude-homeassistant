---
name: home-assistant-backup
description: Use when starting new Home Assistant feature work or before making configuration changes - ensures latest config backed up with smart retention
---

# Home Assistant Backup with Smart Retention

Before starting Home Assistant configuration work, always pull the latest config and create a backup with retention pruning. Read-only audits, including `check-upgrade`, are exempt.

## When to Use

- Starting new automation/script work
- Before modifying dashboards or configuration
- Before testing experimental changes
- User explicitly requests backup

**When NOT to use:** During read-only upgrade reviews or emergency recovery. For emergency recovery, use `tar -xzOf backups/ha_config_<timestamp>.tar.gz <path>` to extract specific files.

## Workflow

| Step | Command | Purpose |
|------|---------|---------|
| 1. Pull | `make pull` | Sync latest config from HA (includes validation) |
| 2. Backup | `make backup` | Create `backups/ha_config_YYYYMMDD_HHMMSS.tar.gz` and attempt changelog generation |
| 3. Prune | `uv run python tools/prune_backups.py --dry-run`, then `--apply` | Preview, then apply retention rules and clean orphaned changelogs |

**If pull fails** (SSH down, network issue): backup the stale local copy anyway — a stale backup is better than none.

**Preview before pruning:** `uv run python tools/prune_backups.py --dry-run` to see what would be deleted. Pass `--apply` only when deletion is authorized; the default is dry-run.

**Searching backups:** `make backup-search PATTERN='text'` to find when a change was introduced.

**Backup directory:** The helper tools assume the default `backups/` directory. Do not override `BACKUP_DIR` when using pruning, search, or changelog helpers unless those tools are updated to honor the override.

## Retention Rules

Applied automatically by `tools/prune_backups.py`:

| Age | Keep |
|-----|------|
| 0-7 days | All backups |
| 7-30 days | One per day (latest) |
| 30+ days | One per week (latest) |

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Backup without pulling first | Always `make pull` before backup — you want latest state |
| Skip retention pruning | Backups accumulate fast — preview and apply pruning after backup when authorized |
| Assume Makefile prunes | Makefile only creates — pruning is a separate step |
| Delete backups manually | Use prune script for consistent retention |
| Applying pruning without review | Preview first; `--apply` deletes redundant copies per retention rules |
| Skipping pull because "user already did it" | Check file freshness first (`ls -lt config/automations.yaml`), then skip only if recent |
