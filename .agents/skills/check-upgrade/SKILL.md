---
name: check-upgrade
description: Use when the user provides a target Home Assistant version and wants an upgrade-readiness, breaking-change, or release-feature review before upgrading
---

# Home Assistant Upgrade Check

Produce a sourced, instance-specific report before a Home Assistant Core upgrade. This skill is read-only: do not upgrade Home Assistant, change configuration, reload YAML, or deploy anything while running it.

Do not automatically run `make pull`, `make backup`, `make push`, or other commands that rewrite the local configuration or Home Assistant state. `make pull` can overwrite dirty local files even though it does not change live Home Assistant. If the user already ran a pull or backup, record that as user-provided context and do not repeat it without explicit permission.

## Required Input

The user must provide a stable target version in `YYYY.M.P` form, such as `2026.8.1`. Resolve that exact version against the official Home Assistant Core releases/tags before calculating the range; an unpublished or nonexistent target is `BLOCKER`, not merely an unresolved low-confidence review.

If no target is supplied, ask for it. Do not silently use the latest release.

The current version is determined in this order:

1. Read the live Home Assistant version with `ha_get_overview(fields=["system_info"])`.
2. Fall back to the read-only REST endpoint with `uv run python tools/ha_cli.py curl /api/config --pick version` if MCP is unavailable.
3. If both sources exist and disagree, use the live version for the comparison and report the mismatch prominently.
4. If neither source is available, ask the user for the current version and record it as user-provided evidence. Do not infer it from a target, an unverified local snapshot, or silently use the latest release.

Reject prerelease targets and malformed versions. If the current installation is itself a prerelease, state that the stable-release range is approximate and ask whether prerelease-specific notes should also be reviewed. If the target is not newer than the current version, report that there is no upgrade range instead of pretending to review one.

Record the evidence source and retrieval timestamp for the current version using ISO 8601 with an explicit offset or UTC. If only user-provided evidence is available, record that fact and its uncertainty.

## Version Range

Review the open interval `(current_version, target_version]`.

Home Assistant uses calendar versions, so enumerate every minor release and patch release in that interval:

- Include later patches in the current minor release, if any.
- Include the base release and all patches for each intermediate minor release.
- Include only patches up to the requested target patch for the target minor release.
- If a release blog contains patches newer than the target, exclude those bullets.

For example, a move from `2026.7.3` to `2026.8.1` requires the remaining `2026.7` patches, if published, plus `2026.8.0` and `2026.8.1`.

## Official Sources

Use official Home Assistant sources only for release facts:

- Release blog: `https://www.home-assistant.io/blog/`
- Core changelog: `https://www.home-assistant.io/changelogs/core-YYYY.M`
- Official Core stable releases/tags: `https://github.com/home-assistant/core/releases`
- Integration documentation linked from the release blog
- Home Assistant developer blog links included in the release blog

Release blog URLs generally look like `/blog/YYYY/MM/DD/release-YYYYM/`, while the date is not safely derivable from the version. Resolve each URL from the Home Assistant blog index or another official Home Assistant link. Do not guess a date and treat a failed fetch as a successful lookup.

For each release family:

1. Find the release blog from the official blog index by its `YYYY.M` title or `release-YYYYM` slug.
2. Fetch the blog and its linked core changelog.
3. Record the canonical URL, publication date when resolved (otherwise `unavailable/unverified`), release title, and retrieval date.
4. If the blog or changelog cannot be fetched, mark that release as unresolved and lower the confidence of the report. Do not claim that no breaking changes exist for an unresolved release.

## Release-Note Parsing

The release blog has a stable structure, but the extraction often contains a duplicated table of contents. Parse actual section boundaries, not matching text in the table of contents.

Extract these sections when present:

- `Backward-incompatible changes`: the primary breaking-change source. Items are often plain integration-name paragraphs rather than headings. Read until `All changes` or the next top-level section.
- `Farewell to the following`: removed integrations. Keep this separate from backward-incompatible changes, then correlate overlaps.
- `Patch releases`: parse each `YYYY.M.P - Month day` subsection and include only versions inside the range.
- `New integrations` and `Noteworthy improvements to existing integrations`.
- `Other noteworthy changes`.
- `(breaking-change)` and other compatibility markers in the core changelog as a secondary discovery signal; the dedicated release-blog section remains primary.
- Developer-blog links listed near the backward-incompatible section.
- `All changes`: use the core changelog to find relevant details, not as a substitute for the dedicated backward-incompatible section.

For every breaking or potentially breaking item, capture:

- Release version and integration or feature
- What changed or was removed
- Whether migration is automatic
- Required user action, replacement, or prerequisite version
- Affected entities, properties, services, options, or event data
- Issue/PR and documentation links when provided

Do not label a bug fix as a breaking change merely because it changes behavior. Patch releases are normally fixes, but flag a patch item if its text indicates a compatibility requirement or behavior change relevant to this instance.

Treat documented migrations, automatic device/entity moves, changed defaults, and removed attributes as potentially breaking behavior even when the release blog does not place them under `Backward-incompatible changes`. Put them in the same applicability/risk/evidence table as formal breaking changes so the report has one auditable record for every upgrade risk.

## Instance Matching

Build an evidence-backed profile before deciding whether a release item affects this Home Assistant instance.

Use the live instance first:

- `ha_get_overview` for system information and domain summaries
- `ha_search` for affected integration names, entity IDs, properties, services, and configuration references
- `ha_search_tools` to discover tools for entities, devices, config entries, HACS repositories, add-ons, or integrations when the overview/search results are insufficient. Inspect annotations and use only tools/actions marked read-only; do not call install, update, skip, service, or other state-changing actions in this skill.
- `ha_get_entity` to resolve an entity's platform and config entry
- `ha_get_device` to resolve the owning device's integration, manufacturer, model, and related entities
- `ha_get_integration` to confirm the state and options of the owning config entry

Use the repository second:

- Targeted `Grep` in `config/*.yaml`, `config/blueprints/`, `frigate/config.yml`, and other managed configuration files
- `ha_search` for automation, script, scene, helper, and dashboard references
- Existing `AGENTS.md` and README hardware/integration notes as leads, not proof of current enablement

Never read the entire `config/.storage/core.entity_registry` or `config/.storage/core.device_registry`. Do not read all of `config/automations.yaml`; search for exact affected terms or use `ha_cli edit` to inspect a named automation.

### Resolve Ownership Before Classifying

Do not infer an entity's owning integration from its domain, friendly name, or the fact that the integration is loaded. When a release item names an entity, device, platform, or changed property:

1. Find the exact entity IDs with `ha_search` or a targeted state/entity query.
2. Resolve each affected entity with `ha_get_entity` and, when it belongs to a device, `ha_get_device(entity_id=...)`.
3. Use the fields actually returned by the tool, such as `platform`, `device_id`, and singular `config_entry_id`, as applicability evidence; do not assume plural or deprecated registry fields.
4. If necessary, inspect the owning config entry with `ha_get_integration(entry_id=...)`.

For example, a Matter integration and two cover entities do not prove that the covers are Matter entities. Resolve both covers before attributing a Matter fix to them.

Capture the live evidence timestamp, or the report-generation timestamp when the tool does not provide one. Record relevant installed versions and pending updates for HACS repositories and add-ons separately from Home Assistant Core; do not silently treat an add-on update as part of the Core upgrade.

### Handle Partial Search Results

Every `ha_search` response must be checked immediately for `partial`, `partial_reason`, `warnings`, and `errors`. An empty result with `partial: True` is not evidence that no match exists. Paginate entity and configuration surfaces independently using their respective offsets.

When configuration search is partial or skips YAML-defined automations:

- Run targeted `Grep` for every exact affected identifier across `config/automations.yaml`, `config/scripts.yaml`, `config/scenes.yaml`, `config/blueprints/`, `frigate/config.yml`, and other managed configuration files.
- Search dashboards and helpers explicitly when the changed item can be referenced there.
- Record the skipped surface and remaining uncertainty in the report.
- Classify the finding as `Unknown` unless the fallback searches and live ownership checks provide enough evidence for a narrower conclusion.

For each release item, classify applicability as one of:

- `Confirmed`: live entity/config/integration evidence matches
- `Likely`: repository or hardware evidence matches but live confirmation is unavailable
- `Not applicable`: the relevant integration or feature was checked and is absent
- `Unknown`: the required instance data could not be obtained

Search both the integration and the changed identifier. For example, a removed `battery_level` property requires checking automation, script, scene, helper, and dashboard consumers, not just whether the integration is installed. A renamed entity or event requires checking all consumers before declaring it safe.

 Repository hardware and integration notes are leads only. Verify every lead against the live instance before using it as applicability evidence. Do not hardcode a particular home's integration list into the reusable workflow.

## Risk Classification

Use these labels in the report:

- `BLOCKER`: upgrade or dependency prerequisite must be completed first, or an actively used integration/function will be removed or stop working
- `ACTION REQUIRED`: configuration, automation, dashboard, or external service must be updated, but the upgrade can proceed after that preparation
- `ATTENTION`: automatic migration or a behavior change may affect results and needs post-upgrade verification
- `INFORMATIONAL`: relevant improvement or removal with no current impact or action
- `UNKNOWN`: impact could not be determined because evidence was unavailable

Aggregate overall risk by precedence: `BLOCKER > ACTION REQUIRED > ATTENTION > UNKNOWN > INFORMATIONAL`. Any `UNKNOWN` evidence prevents an unqualified "safe" conclusion. Do not call the upgrade safe solely because the dedicated breaking-change section has no matching item. State the coverage limits, especially for custom integrations and external add-ons.

## Feature Relevance

Review new features and improvements against the instance profile. Highlight a feature as `HIGH VALUE` only when there is evidence that it benefits this setup or removes an existing workaround. Examples include:

- A new capability in an integration that is installed and used
- A reliability or state-reconciliation fix affecting an integration in use
- A feature that directly improves the repository's automation, dashboard, camera, Zigbee, Frigate, or energy workflows

Use `RELEVANT`, `POSSIBLY RELEVANT`, or `NOT RELEVANT` for every feature. `HIGH VALUE` may be added as a secondary highlight, such as `HIGH VALUE / RELEVANT`, but must not replace the applicability label. Explain why each highlighted feature matters, quote the relevant release text when practical, and link to the official source for that row.

## Report

Write one report at:

`working-docs/upgrades/YYYY-MM-DD-check-upgrade-<target-version>.md`

Create `working-docs/upgrades/` if needed. If that filename already exists, append a numeric suffix before `.md`, starting with `-2` and incrementing until an unused filename is found (e.g. `YYYY-MM-DD-check-upgrade-<target-version>-2.md`). The report must contain:

1. `Upgrade summary`: current version, target version, evidence source/timestamp, overall risk, and a short recommendation qualified by any incomplete evidence.
2. `Coverage`: every release version reviewed, source URL, publication date (or explicitly `unavailable/unverified`), retrieval date, and unresolved sources.
3. `Breaking changes`: a table with release, item, applicability, risk, evidence, required action, a short release-note quote, and a source URL on every row.
4. `Removed or deprecated items`: a distinct section covering integrations, properties, services, options, and replacements, even when the same item also appears in the breaking-changes table.
5. `Relevant features`: instance-specific improvements, with `HIGH VALUE` highlights and ownership evidence for entity-specific features.
6. `Patch fixes`: only fixes relevant to this instance or worth calling out for upgrade confidence.
7. `Upgrade checklist`: preparation, backup reminder, upgrade, repairs/log review, and targeted post-upgrade verification.
8. `Unknowns and limitations`: missing live data, partial searches, custom integrations, HACS components, add-ons, or unresolved release pages.

Use direct links and quote enough release-note text to make the conclusion auditable. Distinguish facts from inference. Do not hide an unknown behind a reassuring summary.

## Completion Checks

Before completing the skill:

- Confirm every version in the calculated range was attempted.
- Confirm each resolved release in `Coverage` has a publication date and retrieval date. For an unresolved release, write `publication date: unavailable/unverified`, retain the retrieval timestamp, and lower report confidence; never infer a date.
- Confirm the report includes a source URL and a release-note quote for every breaking-change finding.
- Confirm the report has a distinct `Removed or deprecated items` section.
- Confirm all `Confirmed` and `Likely` findings have evidence and a concrete action or verification.
- Confirm every `ha_search` response was checked for partial results; if any search was partial, ensure the fallback search and resulting uncertainty are documented.
- Confirm every entity-specific feature was matched to its owning platform/config entry before classification.
- Confirm no local sync or deployment command was run by the skill; disclose any user-run `make pull` or backup used as context.
- Confirm no configuration, `.storage` file, Home Assistant state, or deployment was modified.
- Mention if the report is based on REST or user-provided version evidence rather than live HA.
- Use the `reflect` skill if the review uncovers a new Home Assistant compatibility gotcha.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Reviewing only the target release blog | Enumerate every minor and patch release after the current version |
| Guessing release-blog dates from the version | Resolve URLs from the official blog index |
| Treating the table of contents as the breaking-change section | Parse actual headings and section boundaries |
| Assuming no matching BIC item means safe | Report coverage limits and inspect custom integrations/add-ons |
| Checking only installed integrations | Search entity and configuration consumers for changed properties/events |
| Inferring an entity's integration from its domain or name | Resolve the exact entity with `ha_get_entity`/`ha_get_device` first |
| Treating an empty partial search as no match | Check `partial`/`warnings` and run targeted local fallbacks |
| Calling every new feature relevant | Require evidence from the instance profile and explain the value |
| Omitting required report sections because findings overlap | Keep removed/deprecated items distinct and link each row to its source |
| Reading entire `.storage` registries or automations | Use MCP search and targeted local searches |
| Modifying HA while checking an upgrade | Keep this workflow read-only; produce a report only |
