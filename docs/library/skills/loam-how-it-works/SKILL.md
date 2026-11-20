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

**Providers.** Connect an account in Settings › Connections. A provider
is ready when its CLI is installed and its sign-in is usable. One
connected provider is enough for the first hello and any agent that uses
automatic selection. With several, conversations prefer Claude Code,
then Codex, then Antigravity. An explicit choice in Settings › Agents or
a thread's Providers & models stays selected if its sign-in stops working.
A thread using the agent's default inherits it on the next turn. An
automatic choice stays empty until a provider connects. Models and
thinking levels belong to their provider. Loam uses the selected provider's
defaults when a different provider's settings do not apply. Each CLI gets
the same agent files in its own format and stays on the version pinned by
the Loam release.

**Threads.** The owner talks with agents in threads. Loam writes each
thread's record into the vault as it happens (never edit one). A message
that tags nobody goes to the thread's default agent; `@Name` gives that
agent a turn. Each thread has a board, a Markdown file of items for the
owner in the vault's inbox. Open **Board** in the thread header. In **Cards**,
tap a note to edit its words, or use **Add an idea…** under a section heading.
New cards show their date and author. Drag a card to reorder it (hold first
on touch), or open its menu to move it, set an expiry or delete it. **Doc**
opens the same file as one document. Both views preserve the file's metadata.
If an agent edits the board during a save, the app keeps the draft and shows
the latest board with a button to reapply the edit. The phone and desktop
remember their views separately in `appearance.phone.board_view` and
`appearance.desktop.board_view`.

The session host runs the provider and records its
output as it arrives. The thread worker turns that output into the reply the
app shows. Before sending a turn, the worker saves the input, the bound
session and where to read its output. A returning worker can use that
record to finish filing the same reply. The provider runs in its own service.
When only the API changes, it reconnects to that service and reads from its
saved position while the provider continues. A service may keep its loaded
revision after edits to comments or unused documentation. A planned change
to the host closes admission and proceeds only when sessions and queued
turns are idle. During a brief outage the app keeps submitted messages in this browser and delivers them
in order when the service returns.

On a fresh install the owner lands in Hello with Iris. Loam posts a greeting
as Iris using the name from setup, and Iris takes over on the first reply.
She helps with whatever the owner came to do or offers a couple of ways to
start and follows their choice. They can make an agent, change Loam by
asking, build a page or tool at their own address, or just talk. Iris can
also offer early to bring over context from another AI or service.
`About me.md` fills in as they get to know each other.

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
box has memory for it; nothing caps how long it runs. Wakes, crons and background
deliveries can use Claude Code or Codex. An automatic choice uses the first
connected provider those runs support. An explicit unsupported provider
pauses those runs. The nightly reviewer can use Claude Code or Antigravity
and waits if neither is connected and automatic selection is on.

**When something needs attention.** Loam itself posts no notices in a thread
or on General's board. A turn that is waiting for room, could not run or ended
without a reply shows so on the turn in the app. An agent whose own
scheduled run failed is told at the top of its next turn. A problem that is
no one agent's (drift, memory pressure, a check or a plugin's event, an
update) goes to the one agent the owner chose in
Settings › Agents (the first agent, unless they change it), as the input of
a run of its own, told once. That agent decides what to do: it can brief
the engineer, and it tells the owner in General, in its own words, only if
they need to know. The failure log and Activity keep the quiet record.

**Activity.** Open Activity from the desktop rail or from Settings on a phone.
Server resources shows memory, the estimated number of additional sessions that
can start, one-minute CPU load against the shared CPU count, and used and free
space on the vault disk. Open Storage breakdown to see which groups use the
space and when they were measured. Running now lists conversations and jobs.
History lets you filter earlier work by kind and agent. The Activity tab on an
agent’s profile opens that agent’s history.

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
step where there is one, and clears it when agents can run again. A fresh install gets one check period after its welcome thread is created for the first reading to arrive.

**The vault** is the owner's notes and the record, plain Markdown. Its
`README.md` says what each folder is for. `About me.md`, at its top, is what
the owner's agents know about them: the agents add to it, and it is the
owner's to read and edit.

**Settings › Plugins** shows installed themes and extensions and where each
copy came from. About opens the Market listing for a Market plugin and shows
the folder for a locally made one. Use the Market to find more themes and
extensions. Loam checks the Market listing or the linked repository’s release
tags daily. About lets you review an update and turn automatic updates on or
off. Nightly updates are on by default and appear in Activity. An update
that asks for more access waits for the owner’s yes. An agent changes the
same switch with `bin/settings set plugin.auto_update` and uses
`plugin.install` to preview and confirm a specific update on the owner’s word.

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
included, runs its next job on the provider, model and tiers set in
Settings › Agents. An unset engineer uses the connected providers, with
project engineers preferring Codex when it is connected. An existing
project record can pin a runtime. A running job or a continuation keeps
its provider. The engineer's role and scope are in
the vault's projects folder, under `Loam/engineer/` (`role.md`,
`projects/loam.md`): the owner's to read and change, and an update never
overwrites a change of theirs. A job that changes how Loam works updates
this library in the same job, so these pages stay the one description of
Loam.

**Plugins** add themes, page code, tools and servers. Loam's own ship with
it; the owner's live in their own folder, which updates never touch (see
`loam-building-a-plugin`). A customisation is a plugin wherever one can do
it. To customise someone else’s plugin, first make a copy in the owner’s
plugins folder. Keep its id to take over, or use a new id to keep both.
Tell the owner the copy no longer updates by itself and you’ll check the
original for newer versions when asked. Plugins are shared by link, and the Market (Settings › Market)
lists what others made (`loam-publishing-to-the-marketplace`).

**Updates** replace Loam's code and this library (`System/Loam/` in the
vault, all but its `data/`, where plugins keep their data), and put the
providers' CLIs at the release's versions, and nothing else: agents, the
vault, settings, sign-ins and plugins stay. A change the engineer made to
Loam's own code on this install is kept: each update merges the release
around it, and stops, changing nothing, if the two touch the same lines.
On a stock install, the updater also stops for changes in the checkout,
including stray ignored files. It skips the generated engineer harnesses.

**Loam's makers** hear a bug, a wish or any feedback ("tell the people who
make Loam…") when the owner sends it: an agent writes it with personal
details taken out, shows every word, and on the owner's yes gives them a
link that files it as an issue on Loam's GitHub from their own account
(`loam-filing-a-bug-or-feature`).
