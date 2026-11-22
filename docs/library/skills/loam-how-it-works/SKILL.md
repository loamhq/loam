---
name: loam-how-it-works
description: How Loam works - agents, their loading files, threads, wakes and crons, providers, the vault, the engineer and plugins. Use when someone asks how Loam works or how to use it well, or when you need to know which part of Loam does a thing.
---

# How Loam works

Loam runs a small team of AI agents on one server for one person, the
owner. Everything an agent knows and makes is a file on that server.

**An agent** is a folder, its workspace. What it is made of:

- Every conversation, wake and cron loads `soul.md`, who it is (the owner's); its config, which is
  Loam's short text followed by the owner's words (one page every agent
  loads, then the agent's own `config.md`); and the agent's own `map.md` if present.
  A run adds only its own input: a fresh thread session's summary and recent messages, a
  wake's reflection instruction and vault changes, or a cron's file under `crons/`.
- The workspace is the agent's to shape. The map is paid for on every run,
  so it points to things and does not hold them; past 10,000 characters the
  agent is told, and it still loads. `skills/` holds its own skills (see
  `loam-skills`).
- `CLAUDE.md`, `agent.json`, `.claude/`, `.codex/`, `.agents/` and
  `.mcp.json` are Loam's, written by Loam for each provider and from
  Settings; never edited by hand.

An agent is made in a conversation, on the owner's yes, as a character and
not a job (see `loam-making-an-agent`). Its face is drawn by the theme until
the owner uploads a picture.

An agent's current name appears in the app, including its space in Files,
and Loam tells it that name when its session loads. A rename keeps the same
workspace address. The owner can ask an agent to carry the name into current
documents (`loam-agent-name-and-face`). Saved messages, including the first
chat's greeting, keep the words they were written with.

**Providers.** Connect an account in Settings › Connections. A provider
is ready when its CLI is installed and its sign-in is usable. One
connected provider is enough for the first hello and any agent that uses
automatic selection. With several, conversations prefer Claude Code,
then Codex, then Antigravity. An explicit choice in Settings › Agents or
a thread's Providers & models stays selected if its sign-in stops working.
A thread using the agent's default inherits it on the next turn. Changing
a member's model applies to its next turn. Claude Code's acknowledgement
stays in the session journal, and the thread waits for the agent's reply. An
automatic choice stays empty until a provider connects. Models and
thinking levels belong to their provider. Loam uses the selected provider's
defaults when a different provider's settings do not apply. Each CLI gets
the same agent files in its own format and stays on the version pinned by
the Loam release.

**Threads.** The owner talks with agents in threads. Loam writes each
thread's record into the vault as it happens (never edit one). A message
that tags nobody goes to the thread's default agent; `@Name` gives that
agent a turn. Starting from an agent gives the thread that agent. The plain
**New thread** starts without one. Your first individual tag sets the default,
with **Undo** beside the message. An unaddressed message stays as a note.
Pick an agent beneath it to have that agent answer the saved message and
future messages. Pin a thread from its row (hold on a phone) or its details.

When a thread message waits for a connection, it stays in that browser
with **Not sent yet** below it. The browser tries again after a failed send.
Use **Retry** to try sooner. You can keep writing and sending other messages
while it waits. A retry keeps the same message ID so a lost confirmation
doesn't create a second copy.
General always stays pinned. Hello can be unpinned.

Attach MP4, MOV or WebM by choosing, dropping or pasting a file in a chat.
Each video can be up to 512 MB. The composer shows upload progress and the
attachment's × cancels the upload. A failed upload leaves the message draft
in place. The original stays in Files and can play in the thread when the
browser supports its codec. Antigravity uses a smaller viewing copy for
large videos. Claude Code and Codex receive the original file path. Other
chat attachments can be up to 10 MB each.

When an agent adds a local image to a thread with `![caption](path)`,
Loam saves a copy in the vault's attachments folder and shows it where the
agent wrote it. The image stays with the thread after the working file is
removed. An unavailable image keeps its caption and shows **Image no
longer available**.

**Dictation and Read aloud.** These Market plugins work without a key in browsers that support speech recognition or speech synthesis, respectively. With a server key, they select Google first and OpenAI second. A provider error stays with that provider. Each plugin’s settings show the active provider. The Gemini key guide walks through creating, checking and saving a shared key. Dictation can suggest it once after a take that used browser recognition. Record first and Polish, spoken Briefs and generated table descriptions need a server key.

From Dictation 1.2.3, Record first shows Polish, the recording limit, microphone selection and Names and terms. Create or open the terms note in Files from that card. Add one name or term per line. Record first reads it fresh for each take and sends the terms to the transcriber. As you speak uses browser recognition and keeps your Record first preferences for when you switch back.

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

Each member keeps its own view of a thread. Reply turns are for the reply.
After 30 quiet minutes, or sooner when a resident session is due to leave
memory or a large session is ready to rotate, Loam asks privately for a
short handoff. A new message takes priority. The next fresh session gets
the last completed handoff, the recent exchange and the thread file's path.
The handoff names the last message it covers. If a member already keeps a
workspace summary, Loam uses that until a private handoff is ready and
honors later edits to it.

**Branch from a message.** Choose the agent who should answer. The new
composer opens empty with the source message attached as a quote. Write an
opening or remove the quote to start a plain thread. A sent quote goes back
to the original message, and a small link beneath that message opens the
branch. The answering agent gets the original words, author, time and IDs
with the source thread's name and file path, so it can read further back
when it needs to. The owner's opening supplies the title.

On a fresh install, the welcome link is where the owner makes a sign-in.
The page shows when the sign-in is saved and sends the owner to sign-in
once Loam is ready. Once it says the sign-in is saved, the owner can
close the page and return to the same address. Usernames are saved in
lowercase and work with any capitalization. A passkey signs in to the
same account without typing the username.

Claiming starts the owner’s first day. General starts empty, and health notices begin with checks of the claimed install. Updates refresh the original Hello until the owner replies. Each starter file keeps local edits.

On a fresh install, a phone opens Chats with an unread Hello. A wide screen
opens Hello. Iris greets the owner by name and shows how Chats, Files and Apps
fit together. Empty places explain their purpose until they fill. Details, Branch, Activity, Settings and the desktop's Hide list show a
caption until the owner uses that control. Chats, Files and Apps keep
their tab names.

In chats, Enter sends and Shift+Enter starts a new line. Settings › Chats
can switch to Command or Control + Enter to send. The owner’s choice follows
their browsers, and an agent can change it on their word with
`settings set composer.send_key` using `enter` or `modifier`. On mobile
browsers, Return starts a new line and the Send button sends.
When the owner starts designing an agent in Hello or any other thread, Iris
asks one question about what they're looking for and hears the answer
before drafting.

Iris helps with what the owner came for and
offers an idea when it fits. She can bring over context from another AI
and show the owner their About me in Files.
She can offer one small overnight job that fits what they told her, or
think over what they shared and leave a short note. The morning work lives
in Files, with a short message linking to it. Changing the app, building
something or following the owner's own idea can be the whole first visit.

On the newest starter, Iris answers when spoken to and does work the
owner leaves with her, keeping the first two weeks quiet. From the one-week
anniversary through day thirteen, Loam gives her the occasion at her
reflection to send one personal message in Hello. When Garden is on and they're looking
for a place to try making a change, Iris can offer to change it with them.

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

**When something needs attention.** The app shows a turn’s status in its thread when it is waiting for room,
could not run or ended without a reply. An agent whose own
most recent scheduled run failed also sees that failure at the top of its next turn.
Failed or refused runs (including the reviewer), failed chat turns, drift, memory pressure,
checks, plugin events and updates go to the agent the owner chose in
Settings › Agents (the first agent, unless they change it), as the input of
a run of its own, told once. That agent decides whether to act, tell the owner, or record
what happened in a thread, its memory or elsewhere. On installs with the first-weeks starter, release news
worth sharing goes to the original Hello thread. If the owner deletes Hello,
the chosen agent shares news in General. The failure log and Activity keep the quiet record.

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

**Files** brings together **Your files** (the vault: plain Markdown and files
written by the owner or their agents, for the owner), **Agent files** (each
agent's workspace and the shared Commons: theirs to write, the owner's to read),
and **Shared Docs** when Comments & suggestions is on. The vault's `README.md`
says what each folder is for, and `About me.md` at its top is what the agents
know about the owner (agents add to it, and the owner can read and edit it).
New installs seed only `About me.md`, `Projects/Loam/Start here.html`, and the
machinery folders.

Loam's machinery folders — conversations, attachments, boards and system —
sit behind one quiet **Loam files** row at the bottom of **Your files** (still
reachable by search, wiki-links, deep links and from chats). Wake failure logs
(`loam-wake-failures.md`) and thread boards live inside **Loam files** rather
than the inbox.

On the **Files** home, optional **Files quick actions** (**New note**, **Today's
note**) come from the built-in `quick-actions` plugin (off by default on new
installs; configured in Settings › Plugins › Files quick actions or through
`bin/settings set plugin.quick-actions.*`). Below the places sit **Pinned**
(items pinned by the owner from any file or folder's ⋯ menu; nothing is seeded
pinned) and **Recent** (the owner's and personas' recent changes in Your files
outside Loam files, grouped when one author writes 3+ files in a folder on one
day, filtered by Everyone / You / Agents, showing the past 48 hours up to 10
rows with Show more up to a week and 50 rows; engineer writes, receipts and job
artifacts never appear).

In Files, **Delete** is the last item in the ⋯ menu on a deletable file's row
or open header. Confirm to move the file to trash. **Undo** is available in the
status message for five seconds and restores the saved file to its original
path if the name is free. Trash stays on the vault disk until restored or
cleared by the operator. **Move…** in a vault file's ⋯ menu moves it to another
folder. Agents use `move_file`, `delete_file` and `restore_file` for the same
vault operations.

**Sharing and Comments & suggestions.** Two independent built-in plugins
extend Files for any file in the vault (never folders): **Sharing** creates
password-protected links (`/s/<token>`) with safe browser viewing (Markdown,
images, static SVG, PDF, sandboxed HTML, audio, video, text/code) or a clean
download card, and adds **Files › Shared** for files shared by you and links
shared with you. **Comments & suggestions** adds whole-file comments on any
file, plus anchored comments, suggestions, accept/reject, and history on
Markdown — usable privately between the owner and agents even with Sharing
off. When both are on, Share offers comment (and on Markdown, edit) modes and
shows comments on the share page; turning either off leaves the other clean
with no dead controls. Notes and share links follow files across renames,
moves, deletes and restores. Agents use `share_*` / `shared_*` when Sharing is
on and `file_*` / `doc_*` / `comments_appear` when Comments & suggestions is
on. `plugin.comments.appear` controls whether agent names appear to outside
visitors (`names` or `one`), with per-file overrides.

**Settings › Plugins** shows installed themes and extensions and where each
copy came from. About opens the Market listing for a Market plugin and shows
the folder for a locally made one. An installed theme's Mode and option controls
save to the owner's account. Settings › Appearance chooses the active theme,
and Loam applies the saved theme, mode and options before the page draws on
every device. Each theme keeps its own options. System mode follows the device.
An agent changes the same values with `bin/settings set appearance.desktop.theme`,
`appearance.desktop.mode` or `appearance.desktop.options.<theme>.<option>`.
The corresponding `appearance.phone` keys change the same account settings.
Use the Market to find more themes and
extensions. A Market listing shows GitHub’s release-file downloads, updated
nightly and including updates. On installation Loam verifies the release file
against the reviewed commit and uses Git if the file cannot be verified.
Loam checks the Market listing or the linked repository’s release
tags daily. About lets you review an update and turn automatic updates on or
off. Nightly updates are on by default and appear in Activity. An update
that asks for more access waits for the owner’s yes. An agent changes the
same switch for an unedited install with `bin/settings set plugin.auto_update` and uses
`plugin.install` to preview and confirm a specific update on the owner’s word.
Nightly discovery connects a built or link-installed copy to the Market when
its files exactly match a release tag in the listed repository and plugin
folder, including an older release. It keeps the switch and update preference,
then applies or offers the Market update under the usual access checks.
Loam sends personal edits to the agent when an update is available.
Loam tells the chosen attention agent about a newer original. The agent
uses `loam-building-a-plugin` to show what would change in chat, bring in
what the owner wants, or keep this version and leave it quiet.

**History and backup.** Every fifteen minutes Your files (the vault) and Agent
files (the workspaces) are each committed to their own git history on the
server, so a change anyone regrets can be got back from it; nothing leaves the
machine. First setup goes straight from connecting an AI provider to Chats or
the open Hello. Settings › Plugins shows when off-box backup is off. Open Git
backup there to turn it on and connect private repositories for Your files and
Agent files. In Files and Settings › Files, **Download as zip** on Your files,
Agent files or any folder gives the owner a copy of it as it is now, without
git's history or anything secret-shaped.

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

When a shell command takes time, its engineer gets control back in the same
job without the command being killed, and can check its output, keep waiting,
or stop it through the returned handle. Antigravity and Claude Code detach a
still-running foreground command after about ten minutes through
`engineer-bash` and keep its full output in job scratch (Antigravity also
streams output under a background task ID during those ten minutes, then hands
back the detached handle so waiting never stalls). Codex uses resumable
`unified_exec` sessions that return a terminal handle within seconds, with its
blocking clock-sleep tool turned off; a Codex engineer can tee a verbose build
into scratch to keep its full log. No timer kills an engineer’s command. A
quiet transcript can still appear as stalled while the engineer waits.

Find the nightly reviewer’s instructions and choose its provider and model in
Settings › Agents › Nightly reviewer. The reviewer plugin’s Runs on row
opens the same page. Automatic picks the connected provider used by the fewest
active agents. When only one provider is connected, the reviewer uses it. A
disconnected explicit choice stays saved and resumes when it reconnects.

Each project engineer works with one repository. Provisioning keeps the registry
in local runtime state. A project can be given write access to its own folder in
the vault's projects area. Its briefs can name deliverables there, including
when the brief is read-only for the repository. The launcher files receipts
under that project's vault folder.

Engineer job commits and landing merges carry the person named in the
project record, or the owner's identity from Settings ›
GitHub › **Your name on agents' work**. The default uses the owner's connected
GitHub account and its no-reply address when the account is unambiguous.
The checkout's identity is the fallback when Loam has no effective owner
identity. Job commits omit provider attribution.

**Temporary storage.** The launcher clears an engineer job's scratch when
the job ends. Nightly housekeeping sweeps leftovers and removes generated
Python bytecode for vanished source directories, along with bytecode left
idle for 14 days.

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
When you receive release news, use the **For your agent** notes to decide
what matters to this owner, based on what they use and have asked for. Share
one short message about the most relevant major change. A small change gets
a line only if it affects something they use. Skip contributor details and
keep the list of changes out of the conversation.

An agent that encounters a Loam fault offers once to tell its makers. You
see the exact report, including scrubbed bug diagnostics and any proposed
fix, before deciding. The preview says whether account-free sending is
available and offers a GitHub link for your own account. Your agent can
let you know when a maker replies or a fix ships
(`loam-filing-a-bug-or-feature`).

Plugins can add a single update line above the agents in Chats and draw
their own icon in Apps. New installs start with Garden enabled in Apps, pinned
to its reviewed Market release. During the first two weeks, Garden can show
a line in Chats when something grows or an agent has an idea. You can turn
Garden off or remove it in Settings › Plugins. On an existing install you
can get it from the Market.

A planned provider-host restart waits through each resident session’s normal idle window. A thread member stays warm for ten minutes after activity and a classic session for thirty. After the idle reaper retires the providers, the controller checks that only the host process remains in its service. An unreadable or inconsistent inventory defers the restart.
