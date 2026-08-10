---
name: reflect
description: Use after every non-trivial task and before finishing work; capture learnings in AGENTS.md or the relevant skill to prevent recurrence
---

# Reflect

Capture learnings before they're lost. Two modes depending on context.

## When to Use

After every non-trivial task, run the Quick Check. Use Full Reflection only when the check identifies a learning worth documenting.

- **Before committing or creating PRs** (quick check)
- After debugging a non-trivial issue
- When something unexpected happened (wrong assumption, surprising behavior)
- When user corrects your approach
- After a validation or deployment failure
- When you discover a new HA platform gotcha

**Skip for:** trivial typos, external failures, user-initiated requirement changes.

## Mode 1: Quick Check (before commits/PRs)

Ask yourself: "Did anything unexpected happen? Any new gotcha or pattern worth capturing?"

- **If no** — done. Move on.
- **If yes** — switch to Mode 2.

## Mode 2: Full Reflection (after mistakes, debugging, corrections)

### Reflect → Abstract → Document

| Phase | Key Questions | Output |
|-------|--------------|--------|
| Reflect | What happened? What assumption failed? | Root cause statement |
| Abstract | Is this a pattern? What's the general rule? | Generalized learning |
| Document | Where should this live? Is it already documented? | Updated docs |

## Where Learnings Live

Before documenting, check these locations — slot into the right place:

| Learning Type | Location | Examples |
|--------------|----------|----------|
| HA platform gotchas | `AGENTS.md` → Critical Gotchas | Template whitespace, required_zones format, shell_command subprocess |
| Camera/streaming patterns | `AGENTS.md` → Streaming/Frigate sections | go2rtc config, play_stream vs play_media |
| Session-specific context | `AGENTS.md` or the relevant skill | Entity refs, historical decisions, transition notes |
| Automation workflow pitfalls | `home-assistant-automation` skill → Common Mistakes | Entity discovery, validation, deployment |
| Debugging patterns | `home-assistant-debugging` skill → Common Mistakes/Failure Patterns | Template issues, restart behavior |
| Backup/deployment process | `home-assistant-backup` skill | Retention, sync issues |

**Dedup rule:** Grep existing docs for the topic first. If already covered, update the existing entry or stop. Don't create duplicates.

## Pitfalls

| Pitfall | Why it fails | Fix |
|---------|-------------|-----|
| Superficial reflection ("I made an error") | Symptoms treated, not cause — recurs | Ask "why" 3x to reach root cause |
| Too specific (only fixes this instance) | Next occurrence isn't prevented | Abstract to a general rule |
| Documenting without grepping first | Creates duplicates, fragments context | Check existing entries; update, don't add |
| Vague updates ("be more careful") | Not actionable, can't follow consistently | Express as a concrete rule ("Always X before Y") |
| Moving on without a quick check | Learnings lost permanently | Pause before committing — even 30 seconds counts |
