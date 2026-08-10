---
name: reflect
description: Use after every non-trivial task and before finishing work; capture learnings in AGENTS.md or the relevant skill to prevent recurrence
---

# Reflect

Capture learnings before they're lost. Two modes depending on context.

## When to Use

After work that changes behavior, exposes a reusable learning, or encounters a meaningful failure, run the Quick Check. Use Full Reflection only when the check identifies a learning worth documenting.

- **Before committing or creating PRs** (quick check)
- After debugging a non-trivial issue
- When something unexpected happened (wrong assumption, surprising behavior)
- When user corrects your approach
- After a validation or deployment failure
- When you discover a new HA platform gotcha

**Skip for:** trivial typos and work with no behavior, process, or reusable-learning change. Do not blanket-skip external failures or requirement changes; record a learning when they expose a preventable process or information gap. For a read-only audit, report proposed documentation changes rather than modifying project files unless the task explicitly includes documentation edits.

## Mode 1: Quick Check (before commits/PRs)

Ask yourself: "Did anything unexpected happen? Any new gotcha or pattern worth capturing?"

- **If no** — done. Move on.
- **If yes** — switch to Mode 2.

## Mode 2: Full Reflection (after mistakes, debugging, corrections)

### Reflect → Abstract → Document → Follow Up

| Phase | Key Questions | Output |
|-------|--------------|--------|
| Reflect | What happened? What evidence supports it? Which assumptions, contributing factors, and uncertainties matter? | Evidence-backed finding |
| Abstract | Is this a pattern? What's the general rule? | Generalized learning |
| Document | Where should this live? Is it already documented? | Updated docs or a proposed documentation change |
| Follow up | Does the learning require a concrete change? Who owns it and how will completion be verified? | Tracked action with owner and verification criterion |

Do not force a single root cause or use "ask why three times" as a proof method. Capture contributing factors and uncertainty, then continue until the analysis supports a reusable system or process improvement.

Keep reflection blameless: describe system, process, tooling, and information gaps rather than assigning fault or prescribing that people "be more careful."

## Where Learnings Live

Before documenting, check these locations — slot into the right place:

| Learning Type | Location | Examples |
|--------------|----------|----------|
| HA platform gotchas | `AGENTS.md` → Critical Gotchas | Template whitespace, required_zones format, shell_command subprocess |
| Camera/streaming patterns | `AGENTS.md` → Streaming/Frigate sections | go2rtc config, play_stream vs play_media |
| Durable reusable learning | `AGENTS.md` or the relevant skill | Entity refs, compatibility gotchas, workflow rules |
| Session-specific context | Task, issue, or PR record | Temporary entity refs, historical decisions, transition notes |
| Automation workflow pitfalls | `home-assistant-automation` skill → Common Mistakes | Entity discovery, validation, deployment |
| Debugging patterns | `home-assistant-debugging` skill → Common Mistakes or Common Failure Patterns | Template issues, restart behavior |
| Backup/deployment process | `home-assistant-backup` skill | Retention, sync issues |

**Dedup rule:** Grep existing docs for the topic first. If already covered, update the existing entry or stop. Don't create duplicates. For behavior, entity, or workflow changes in this repository, also update `README.md`, `AGENTS.md`, and the relevant skill when the repository workflow requires it.

Never write secrets, access tokens, private URLs, raw Home Assistant state, or other sensitive session details into committed guidance. Keep temporary context in the task/issue/PR record.

## Pitfalls

| Pitfall | Why it fails | Fix |
|---------|-------------|-----|
| Superficial reflection ("I made an error") | Symptoms treated, not cause — recurs | Record evidence, contributing factors, uncertainty, and the system/process change that prevents recurrence |
| Too specific (only fixes this instance) | Next occurrence isn't prevented | Abstract to a general rule |
| Documenting without grepping first | Creates duplicates, fragments context | Check existing entries; update, don't add |
| Vague updates ("be more careful") | Not actionable, can't follow consistently | Express as a concrete rule ("Always X before Y") |
| Moving on without a quick check | Learnings lost permanently | Pause before committing — even 30 seconds counts |
| Documentation without ownership | Improvements remain untracked | Create a follow-up with one owner, priority, and a measurable verification criterion |

## References

- [Google SRE Workbook: Postmortem Culture](https://sre.google/workbook/postmortem-culture/)
- [Google SRE: Postmortem Culture](https://sre.google/sre-book/postmortem-culture/)
