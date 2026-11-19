---
name: loam-how-it-works
description: How Loam works - agents, their loading files, threads and boards, wakes and crons, providers, the vault, the engineer and plugins. Use when someone asks how Loam works or how to use it well, or when you need to know which part of Loam does a thing.
---

# How Loam works

Loam runs a small team of AI agents on one server for one person, the
owner. Everything an agent knows and makes is a file on that server.

**An agent** is a folder, its workspace. What it is made of:

- Three files load on every run, in a conversation, at a wake and in a
  cron alike: `soul.md`, who it is (the owner's); its config, which is
  Loam's short text followed by the owner's words (one page every agent
  loads, then the agent's own `config.md`); and `map.md`, the agent's own.
  A run adds only its own input: a thread's summary and recent messages, a
  wake's list of what changed in the vault, a cron's file under `crons/`.
- The workspace is the agent's to shape. The map is paid for on every run,
  so it points to things and does not hold them; past 10,000 characters the
  agent is told, and it still loads. `skills/` holds its own skills (see
  `loam-skills`).
- An agent made before the map loads `_always.md` on every run,
  `_active.md` in a conversation and `_reflection.md` at its wake, until it
  writes a `map.md`; from then on those are ordinary files of its own.
- `CLAUDE.md`, `agent.json`, `.claude/`, `.codex/`, `.agents/` and
  `.mcp.json` are Loam's, written by Loam for each provider and from
  Settings; never edited by hand.

An agent is made in a conversation, on the owner's yes, as a character and
not a job (see `loam-making-an-agent`). Its face is drawn by the theme until
the owner uploads a picture.

**Providers.** An agent runs on Claude Code, Codex or Antigravity; the owner
picks its default and can change it for one thread. Its model and thinking
level are set the same way, in Settings › Agents: every run of the agent (a
thread turn, a wake, a cron, a notice, a chat) uses that model unless a
thread chose another for itself. Each provider is given
the same files in its own shape. Each Loam release carries the version of
each provider's CLI it was tested with, and installs exactly that one; a
newer CLI arrives with a later Loam release, never by itself overnight.

**Threads.** The owner talks with agents in threads. Loam writes each
thread's record into the vault as it happens (never edit one). A message
that tags nobody goes to the thread's default agent; `@Name` gives that
agent a turn. Each thread has a board, a Markdown file of items for the
owner in the vault's inbox.

**Wakes and crons.** An agent also runs by itself on a schedule, and each
schedule is a file under its `crons/` saying when it runs (or once, at a set
time) and what it is told. Every agent wakes once a day by its daily wake,
`reflection.md`: told by default to keep learning from the owner's life and
what is happening, keep its workspace up and do what might need doing, or
nothing, with what changed in the vault since its last wake. The agent moves
its time and changes what it says; only the owner turns it off, and
archiving an agent stops it. Its other schedules are crons. A file is live
while it says `enabled: true`, and a change to it takes effect within a
minute: the agent writes its own, giving a `reason:` for each change, and
the owner can open and edit any of them in the app. A run starts when the
box has memory for it; nothing caps how long it runs.

**When something needs attention.** Loam itself posts nothing in a thread
or on General's board: a thread's record holds only what the owner and the
agents wrote, and a turn that is waiting for room, could not run or ended
without a reply shows so on the turn in the app. An agent whose own
scheduled run failed is told at the top of its next turn. A problem that is
no one agent's (drift, memory pressure, the account refusing runs, a check
or a plugin's event, an update) goes to the one agent the owner chose in
Settings › Agents (the first agent, unless they change it), as the input of
a run of its own, told once. That agent decides what to do: it can brief
the engineer, and it tells the owner in General, in its own words, only if
they need to know. The failure log and Activity keep the quiet record.

**How Loam watches itself.** One check runs every fifteen minutes, and it is
the only thing in Loam that watches Loam on a clock. It reads a fixed list
of facts: every agent that is not archived finished its daily reflection in
the last 26 hours; the session host answers; no provider login an agent runs
on is gone or within three days of expiring; an agent is awake to hear; no
memory pressure; the memory history is still written; every disk Loam
writes to has room; no unit of Loam's own machinery (the sweeps, the off-box
backup, the host's history) has failed; GitHub's authorization is not about
to lapse. Once a day, at 01:40, it also runs the drift check and each
agent's file check. When every fact is true it writes one file and does
nothing else: no run, no cost. A fact that is false, or that cannot be read,
goes to the agent that hears when Loam needs attention, told once like any
other problem. If a run handed facts fails, they are handed over again
later, never dropped. The file (`check.json` in Loam's state, `bin/loam-check
--status`) says each fact with where to look, whether agents can run and
why not, and if the check itself crashed. When no agent can run or Loam's
check has stopped, the app shows one status line with the reason and a next
step where there is one, and clears it when agents can run again.

**The vault** is the owner's notes and the record, plain Markdown. Its
`README.md` says what each folder is for. `About me.md`, at its top, is what
the owner's agents know about them: the agents add to it, and it is the
owner's to read and edit.

**History and backup.** Every fifteen minutes the vault and AI Spaces are
each committed to their own git history on the server, so a change anyone
regrets can be got back from it; nothing leaves the machine. The Git backup
plugin (Settings › Plugins, off until the owner turns it on) sends that
history to a private repository of the owner's own. In Files, **Download as
zip** on the vault, AI Spaces or any folder gives the owner a copy of it as
it is now, without git's history or anything secret-shaped.

**The engineer** builds and changes Loam itself and the owner's projects.
An agent hands it a job as a brief, which returns at once; the engineer
works in its own copy of the repository, and when it is done its receipt,
with the evidence, comes back to that agent as its next input. Every
install has one for Loam (project `Loam`). Work on Loam's web app alone
is a brief to `Loam` with `scope: "app"`, which any agent may send: the
engineer then changes only the `app/` folder, without the server's admin
rights, runs the app's tests, and a deploy restarts the app alone; work
that also touches the server is a `Loam` brief with no scope. Each engineer, Loam's
included, runs on the provider, model and tiers set for it in Settings ›
Agents (Loam's is Claude Code until someone changes it), from its next
job; a job already running keeps what it started on. Its role and what it owns are in
the vault's projects folder, under `Loam/engineer/` (`role.md`,
`projects/loam.md`): the owner's to read and change, and an update never
overwrites a change of theirs. A job that changes how Loam works updates
this library in the same job, so these pages stay the one description of
Loam.

**Plugins** add themes, page code, tools and servers. Loam's own ship with
it; the owner's live in their own folder, which updates never touch (see
`loam-building-a-plugin`). A customisation is a plugin wherever one can do
it. Plugins are shared by link, and the Market (Settings › Market)
lists what others made (`loam-publishing-to-the-marketplace`).

**Updates** replace Loam's code and this library (`System/Loam/` in the
vault, all but its `data/`, where plugins keep their data), and put the
providers' CLIs at the release's versions, and nothing else: agents, the
vault, settings, sign-ins and plugins stay. A change the engineer made to
Loam's own code on this install is kept: each update merges the release
around it, and stops, changing nothing, if the two touch the same lines.

**Loam's makers** hear a bug, a wish or any feedback ("tell the people who
make Loam…") when the owner sends it: an agent writes it with personal
details taken out, shows every word, and on the owner's yes gives them a
link that files it as an issue on Loam's GitHub from their own account
(`loam-filing-a-bug-or-feature`).
