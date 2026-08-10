---
name: home-assistant-backup
description: Use when starting new Home Assistant feature work or before making configuration changes - protects local state and creates a verified snapshot with smart retention
---

# Home Assistant Configuration Snapshot with Smart Retention

This repository's `make backup` creates a local configuration snapshot, not a native Home Assistant backup. Before configuration work, protect any local state before pulling, then create a fresh post-pull snapshot. Read-only audits, including `check-upgrade`, are exempt.

## When to Use

- Starting new automation/script work
- Before modifying dashboards or configuration
- Before testing experimental changes
- User explicitly requests backup

**When NOT to use:** During read-only upgrade reviews or emergency recovery. For emergency recovery, use `tar -xzOf backups/ha_config_<timestamp>.tar.gz <path>` to extract specific files, but do not treat that as a Home Assistant restore.

## Workflow

| Step | Command | Purpose |
|------|---------|---------|
| 1. Protect local state | `make backup` | Snapshot the current local tree before any pull that can overwrite it |
| 2. Pull | `make pull` | Sync latest config from HA (includes validation) |
| 3. Snapshot live state | `make backup` | Create a verified `backups/ha_config_YYYYMMDD_HHMMSS.tar.gz` of the pulled tree |
| 4. Prune | `uv run python tools/prune_backups.py --dry-run`, then `--apply` | Preview, then apply retention rules and clean orphaned changelogs |

Do not run `make pull` until the pre-pull snapshot succeeds. If pull fails, preserve the pre-pull archive and treat the local tree as potentially partial; do not create or use a post-pull snapshot until the tree has been restored or re-synced successfully.

**Preview before pruning:** `uv run python tools/prune_backups.py --dry-run` to see what would be deleted. Pass `--apply` only when deletion is authorized; the default is dry-run. The default `--min-keep 3` safety floor applies during `--apply`.

**Searching backups:** `make backup-search PATTERN='text'` to find when a change was introduced.

**Backup directory:** Keep the default `backups/` directory. The Makefile and helper tools must use the same directory; if changing `BACKUP_DIR`, verify all backup, pruning, search, and changelog commands use it before relying on the result.

## Retention Rules

Applied automatically by `tools/prune_backups.py`:

| Age | Keep |
|-----|------|
| 0-7 days | All backups |
| 8-30 days | One per day (latest) |
| 31+ days | One per week (latest) |

## Safety and Recovery

- Local snapshots may contain `config/secrets.yaml`; treat archives as sensitive. The Makefile verifies the tarball and applies mode `600`, but this is not encryption.
- Changelogs intentionally exclude `secrets.yaml`; never paste archive contents or diffs containing secret values into tickets or chat.
- For system recovery, migration, or disaster recovery, use native Home Assistant backups. Official full backups can include `config`, `share`, `addons`, `ssl`, and `media`, are encrypted, and can be restored through Home Assistant with the backup emergency kit.
- Keep at least one encrypted copy on another system and ideally one off-site. A local tarball on the same host is not sufficient disaster recovery.
- Periodically verify a backup by listing it with `tar -tzf` and rehearse extraction or restoration in a disposable environment.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Pulling before protecting local state | Run `make backup` before `make pull`; `rsync --delete` can overwrite or remove local files |
| Skip retention pruning | Backups accumulate fast — preview and apply pruning after backup when authorized |
| Assume Makefile prunes | Makefile only creates — pruning is a separate step |
| Delete backups manually | Use prune script for consistent retention |
| Applying pruning without review | Preview first; `--apply` deletes redundant copies per retention rules |
| Treating a failed pull as a valid snapshot | Keep the pre-pull archive and repair or re-sync the tree before taking another snapshot |
| Treating a local tarball as a native HA backup | Use Home Assistant's backup UI or backup action for system recovery and migration |
| Searching with unquoted backup patterns | `backup-search PATTERN` uses a regular expression; quote the shell argument and escape regex metacharacters when literal matching is intended |
| Checking one file's mtime as freshness proof | Compare the whole managed tree and the pull result; one `automations.yaml` timestamp does not cover helpers, scenes, or Frigate |

## References

- [Home Assistant backups](https://www.home-assistant.io/common-tasks/general/#backups)
- [Home Assistant backup emergency kit](https://www.home-assistant.io/more-info/backup-emergency-kit/)
- [Automatic backup action](https://www.home-assistant.io/actions/backup.create_automatic/)
