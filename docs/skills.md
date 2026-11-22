# Skills, the library and the starter vault

*Since 2026-11-14 (first release, L7). The code is `lib/loam_skills.py`; the
command is `bin/library`; the tests are `tests/test_skills.py`; the library
itself is `docs/library/`.*

## What was decided

- **A blank install's vault starts small** (decision 2a): `Inbox/` with
  `Conversations/`, `Projects/`, `Attachments/`, `System/`, and a
  `README.md` saying what each is for. Plain names; every one is read from
  `[vault]` in the configuration (`docs/config.md`), so a vault with other
  names (numbered ones, say) keeps them.
- **`System/Loam/` is the one corner of the vault an update owns.** It is a
  copy of this repository's `docs/library/`: skills in the Agent Skills format,
  and the docs written as skills, so every agent finds them the same way.
  An update replaces it whole.
- **`System/skills/` is the person's shared shelf.** Their own skills, ones
  an agent writes for every agent, and marketplace installs. Updates never
  touch it.
- **Every agent sees both shelves, on every provider**, and one line in its
  always-loading instructions says so.
- **Personal wins by name.** A skill in an agent's own `skills/` with the
  name of a shared one is the one that agent uses; a copy is not updated by
  its original.

Two calls made in building it, not by decision:

- **The person's shelf wins a name over Loam's.** The person's own version
  of one of Loam's skills should not need copying into every agent.
- **Loam renders the pointer in `CLAUDE.md`.** Codex and Antigravity also
  use its instructions. Each agent keeps control of its own map.

## The starter vault

`bin/library seed-vault [DIR]` makes it at `DIR` (default `paths.vault`),
with the folder names of the configuration it runs under (`LOAM_CONFIG`
names another). It writes only what is missing, and installs the library;
over a vault that exists it moves nothing. The installer (L12) runs it.

## The library

`bin/library install` copies `docs/library/` over `<vault>/<system>/Loam/` and
prints the difference it replaced, file by file, so an edit someone made
there is named before it is gone (L13's update shows the same diff first).
The copy is made beside the folder and swapped in, so a reader never sees
half of one. `check-drift` reports the folder when it differs from
`docs/library/`: the library is the generator and the vault's copy is the live
file.

## What each provider is shown

The providers disagree on two same-named skills, so Loam decides which one
an agent sees rather than leaving it to them. Each line below was measured
on 2026-11-14 (Claude Code 2.1.325, Codex 0.169.0):

| Provider | Reads | Two of one name |
|---|---|---|
| Claude Code | `.claude/skills` in the working directory and every parent above it, and `--add-dir` folders. **Not** `permissions.additionalDirectories`. | The closer one, alone. Through `--add-dir` the other is listed too, renamed `<dir>:<name>`. |
| Codex | `.codex/skills` and `.agents/skills` in the working directory, `$CODEX_HOME/skills`; parents only when `project_root_markers` finds a root (the broker sets none) | **Both, side by side.** One skill reached by two links is listed once. |
| Antigravity | `.agents/skills` in the workspace, and its own global folder | Not observable: no signed-in Antigravity on the box it was built on. |

So:

- **Claude Code.** The persona's `.claude/skills` stays a link to
  `../skills`. The shared shelves are one folder of links,
  `<workspaces>/.claude/skills/`, the parent of every persona's workspace;
  Claude Code finds it there and keeps the persona's own skill of a name.
- **Codex and Antigravity.** `.agents/skills` in each workspace is a folder
  Loam renders: a link per personal skill (`../../skills/<name>`) and a
  link per shared skill no personal one shadows. `.codex/skills` stays a
  link to `../skills`, so a personal skill made during a Codex thread shows
  at once.

A skill's name, for shadowing, is its folder's name and its `SKILL.md`'s
`name`; a single-file skill (`skills/<name>.md`) takes its stem.

**When the views are rendered.** At provisioning; at every session start
in the broker, on all three providers; at every wake and cron; and by
`bin/library refresh`. Between two, a skill added to a shelf is not yet
listed, and on Codex a personal copy made mid-thread is listed beside the
original until the next start. `check-drift` names a view that is behind.
A refresh never fails a start, and never removes anything but a link: a
real folder found in a view is left alone and its name counts as taken.

## Not covered

- The platform engineer and project engineers run outside the workspaces
  folder and see neither shelf. Nothing they do needs one today.
- Installing a `kind: skill` package from a link is not built
  (`docs/plugins.md`); a shared skill is a folder copied onto the shelf.
