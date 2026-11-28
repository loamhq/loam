---
name: loam-skills
description: How skills work in Loam - your own skills, the shared shelf, Loam's shelf, which one wins on a name, making your own version of a shared skill, and writing a new one. Use when making, changing, copying or sharing a skill, or when two skills have the same name.
---

# Skills in Loam

A skill is a folder holding a `SKILL.md`: frontmatter with `name` (lower
case, digits and dashes, the same as the folder's name) and `description`
(what it does and when to use it, which is what you see before you open
it), then the instructions. It may hold scripts and files it refers to by
relative path. This is the open Agent Skills format, so every provider
reads it.

**Three shelves**, all listed to you on every provider:

1. **Yours**: `skills/` in your workspace. Yours to make, change and
   retire; the reviewer reads it.
2. **The shared shelf**: `skills/` in the vault's system folder. The
   owner's own, ones an agent writes for every agent, and shared skills
   copied from the Market. Updates never touch it.
3. **Loam's shelf**: `Loam/skills/` in the vault's system folder, beside
   it, where this page is. Replaced whole by every update.

**A name is one skill.** Your own skill wins over a shared one with the
same name, and the shared shelf wins over Loam's. To make your own version
of a shared skill, copy its folder into your `skills/` and change it: from
then on it is yours, and changes to the original do not reach it. To retire
your version, delete your copy and the shared one is back.

**Write a skill for every agent** on the shared shelf, for yourself in your
own `skills/`. Never edit Loam's shelf: the next update replaces it.

A new or removed skill is listed from your next session (on Antigravity,
the shared shelves are passed with `--add-dir` so `.agents/skills/` links
resolve across the workspace boundary on fresh and resumed conversations
alike). On Codex a copy you make during a thread is listed beside the
original until the next session.
