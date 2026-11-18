---
name: loam-how-it-works
description: How Loam works - agents, their loading files, threads and boards, wakes and crons, providers, the vault, the engineer and plugins. Use when someone asks how Loam works or how to use it well, or when you need to know which part of Loam does a thing.
---

# How Loam works

Loam runs a small team of AI agents on one server for one person, the
owner. Everything an agent knows and makes is a file on that server.

**An agent** is a folder, its workspace. What it is made of:

- `soul.md`: who it is, its character, values and identity. `config.md`:
  its rules. Both are the owner's. How it works day to day is in its
  `_active.md`.
- The loading files, the agent's own: `_always.md` loads on every run,
  `_active.md` when the owner is in the conversation, `_reflection.md` when
  it wakes alone, and anything under `crons/` on that cron's run. Each is
  kept under 10,000 characters, because it is paid for on every run.
- `skills/`: its own skills (see `loam-skills`). Notes and anything else it
  keeps are ordinary files it reads when it needs them.
- `CLAUDE.md`, `agent.json`, `.claude/`, `.codex/`, `.agents/` and
  `.mcp.json` are Loam's: rendered from those files, never edited by hand.

An agent is made in a conversation, on the owner's yes, as a character and
not a job (see `loam-making-an-agent`). Its face is drawn by the theme until
the owner uploads a picture.

**Providers.** An agent runs on Claude Code, Codex or Antigravity; the owner
picks its default and can change it for one thread. Each provider is given
the same files in its own shape.

**Threads.** The owner talks with agents in threads. Loam writes each
thread's record into the vault as it happens (never edit one). A message
that tags nobody goes to the thread's default agent; `@Name` gives that
agent a turn. Each thread has a board, a Markdown file of items for the
owner in the vault's inbox.

**Wakes and crons.** An agent can wake by itself on a schedule: its nightly
reflection, and crons, each a file under `crons/` that the owner approved.
A run starts when the box has memory for it; nothing caps how long it runs.

**The vault** is the owner's notes and the record, plain Markdown. Its
`README.md` says what each folder is for. `About me.md`, at its top, is what
the owner's agents know about them: the agents add to it, and it is the
owner's to read and edit.

**The engineer** builds and changes Loam itself and the owner's projects.
An agent hands it a job as a brief; it works in its own copy of the
repository and returns a receipt with the evidence.

**Plugins** add themes, page code, tools and servers. Loam's own ship with
it; the owner's live in their own folder, which updates never touch (see
`loam-building-a-plugin`). A customisation is a plugin wherever one can do
it. Plugins are shared by link, and the marketplace in Settings › Plugins
lists what others made (`loam-publishing-to-the-marketplace`).

**Updates** replace Loam's code and this library (`System/Loam/` in the
vault) and nothing else: agents, the vault, settings and plugins stay. A
change the engineer made to Loam's own code on this install is kept: each
update merges the release around it, and stops, changing nothing, if the
two touch the same lines.

**Loam's makers** hear about a defect or a wish when the owner sends it: an
agent writes the report with personal details taken out, shows it, and on
the owner's yes gives them a link that files it from their own GitHub
account (`loam-filing-a-bug-or-feature`).
