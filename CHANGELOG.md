# Changelog

Find each release's notes in `docs/releases/<version>.md`. Loam gives the
notes to the person's agent, who decides what is worth sharing with them.

- [0.2.42](docs/releases/0.2.42.md), 2026-11-27: Sealed, theme-matched inline HTML/SVG visuals in agent replies (`visual` fenced blocks with tooltips, Expand, Save to Files, and the `loam-inline-visuals` skill); refined app size with a slightly more compact `100%` default (with one-time migration of saved sizes), full-width scaling across `60%`–`200%`, and `200%` phone and desktop layout polish; automatic rebuild-with-rollback for broken or moved plugin Python environments and status/health-check reporting for unstartable plugin servers; and a PTY exit-race fix in sign-in password hashing.
- [0.2.41](docs/releases/0.2.41.md), 2026-11-27: Full agent rename moving the agent's id, workspace folder, wake/schedules, inbox, provider sessions, and registries while keeping conversations and history intact — with automatic migration on update for previously renamed agents; automatic 3–5 word thread titles on the owner's second message (never overwriting manual titles); Market review note differences shown directly on install and update screens alongside updated Market review standards and proof cases; and read-only scratch sweep and private systemd teardown fixes.
- [0.2.40](docs/releases/0.2.40.md), 2026-11-26: Document-style Markdown tables in chats and Files that fit desktop and phone widths calmly without fighting swipe-to-reply; Codex helper sub-agents kept inside the open turn with live step progress in the reply slot; shared reply-stance guidance in persona instructions and optional Codex `verbosity` (`low`, `medium`, `high`) profile key; and isolated, tool-free identity preflight with retry and warning fallback on scheduled runs.
- [0.2.39](docs/releases/0.2.39.md), 2026-11-25: Per-agent `understanding.md` pages replacing recorded owner rules (`config.md` and shared owner page), with a 4,000-character post-write notice across all provider lanes and a shorter, principle-driven agent-creation skill; collapsible/hideable agent draft cards and anchored question/approval cards so sent messages stay in view; unified Files links, headers, collapsible non-Markdown Comments panels, and plain-click Markdown editor links with an Edit chip; starter agent reply length following the owner; owner-facing priority over background runs, orphaned Chromium reaping, CPU-scaled test workers, and browser MCP v0.0.86.
- [0.2.38](docs/releases/0.2.38.md), 2026-11-24: Per-minute numbers-only server usage recorder (local by default, optional outbound collector push when configured, read-only status line in Settings › About Loam, plus non-core owner service and site counts); Python 3.13 / Debian 13 clean fast shutdowns in `session-broker`, `session-host`, and `plugin-gateway`; fast tail-read startup backfill in `session-host`; root-owned lock directory fix; logged plugin contribution errors; and custom `paths.root` support for separate data disks.
- [0.2.37](docs/releases/0.2.37.md), 2026-11-24: Keep Docker asleep across service app deploys during updates, automatically sleep externally woken Docker after ten quiet minutes via a `docker.service`-bound watcher timer, and clean up the watcher units cleanly on rollback to earlier releases.
- [0.2.36](docs/releases/0.2.36.md), 2026-11-24: Sign-in runs as an ordinary system service so Docker can sleep on claimed servers; separate data-disk boot ordering; engineer jobs that change nothing in Loam report back at once without running the full test suite; and small-box test threshold fixes.
- [0.2.35](docs/releases/0.2.35.md), 2026-11-24: Optional `agent` on the `chat.draft` plugin command with pre-filled draft sizing, trailing-line caret placement, and focus/scroll preservation across thread creation; updater re-exec and stale running-record healing; `turn_retried` limit event recording; and shared persona note that Files is the space shared with the owner.
- [0.2.34](docs/releases/0.2.34.md), 2026-11-23: Open phone chats recover live progress after backgrounding; browser, Docker, and plugin servers sleep until first use with memory headroom and swap scaled to the server; `loam market share` creates missing GitHub repositories automatically; no vault-root README on new installs (untouched stock copies removed on update); engineer files and schedule moved into Loam files; refreshed agent docs and multi-provider renamed-agent instructions.
- [0.2.33](docs/releases/0.2.33.md), 2026-11-23: New plugin places (`rail.action`, `thread.list.action`), switch settings, and conditional agent prompts; readable portrait image sizing in chat; optional separate data disk (`--data-dir`) in the installer; agent instruction display-name updates on rename; and bounded Git backup pack memory.
- [0.2.32](docs/releases/0.2.32.md), 2026-11-22: Sharing any file and Comments & suggestions as two built-in plugins, Files home polish and Recent fixes, browser-tool screenshots kept in threads, wiki-links in chats fixed, and plugin timer stability.
- [0.2.31](docs/releases/0.2.31.md), 2026-11-22: permanent non-colliding theme face slots for agents across all themes, Files home redesign (Your files, Agent files, Loam files, Recent, Quick actions), and thread turn stability across board-folder migration.
- [0.2.30](docs/releases/0.2.30.md), 2026-11-22: API keys keeps only OpenAI and Google Gemini built in; optional local providers come from installed plugins or host configuration.
- [0.2.29](docs/releases/0.2.29.md), 2026-11-22: automatic plugin updates for settings-only additions and repaired `commons/` ownership on fresh installs and updates.
- [0.2.28](docs/releases/0.2.28.md), 2026-11-22: Boards removed from core (existing notes kept as plain files), Loam notices routed to the selected agent, Market adoption for older matching plugin copies, steady thread headers with faster plugin loading, and hung-send recovery with Retry.
- [0.2.27](docs/releases/0.2.27.md), 2026-11-21: clearer browser dictation fallback wording.
- [0.2.26](docs/releases/0.2.26.md), 2026-11-21: a shorter API keys list on new installs and a clearer browser dictation offer.
- [0.2.25](docs/releases/0.2.25.md), 2026-11-21: chat videos up to 512 MB with upload progress and cancellation.
- [0.2.24](docs/releases/0.2.24.md), 2026-11-21: choose who answers a saved note, attach short videos, and pin from Chats.
- [0.2.23](docs/releases/0.2.23.md), 2026-11-21: Enter sends by default,
  agent draft revisions have a reply prompt, and theme covers show in Plugins.
- [0.2.22](docs/releases/0.2.22.md), 2026-11-21: start the first day at claim,
  refresh unchanged starter files, and provision the nightly reviewer.
- [0.2.21](docs/releases/0.2.21.md), 2026-11-21: the claim page shows
  setup progress, and sign-in accepts usernames in any capitalization.
- [0.2.20](docs/releases/0.2.20.md), 2026-11-21: fresh installs start with
  Garden enabled, and Chats shows its rows while a returning plugin line loads.
- [0.2.19](docs/releases/0.2.19.md), 2026-11-21: support for Garden, an empty
  Hello board, Iris’s first week, safer plugin updates, and speech fixes.
- [0.2.18](docs/releases/0.2.18.md), 2026-11-20: a new first minute
  with Iris, fixes for Back and files opened from chats, and visible
  install progress with support for Debian 13 and Ubuntu 26.04.
- [0.2.17](docs/releases/0.2.17.md), 2026-11-20: the first-weeks
  wording on new installs returns to its earlier form.
- [0.2.16](docs/releases/0.2.16.md), 2026-11-20: a board of things to try
  in Hello, simpler thread boards, Back and Forward for each panel, and
  memory, CPU load, and disk space in Activity.
- [0.2.15](docs/releases/0.2.15.md), 2026-11-19: connect Syncthing to Loam
  using the key it already has on your server.
- [0.2.14](docs/releases/0.2.14.md), 2026-11-19: see where plugins came
  from, keep them up to date automatically, and review updates that ask
  for more access.
- [0.2.13](docs/releases/0.2.13.md), 2026-11-19: a firewall on every
  server and history kept from the first minute, plain words that agents
  and plugins have full control of the server, and an update from an
  older Loam that starts what a release adds and stops what it retired.
- [0.2.12](docs/releases/0.2.12.md), 2026-11-19: the Market, in Settings ›
  Market, where your agents find things too and post an install card in any
  thread; Dictation and Read aloud bring their own server, so Loam carries
  none of their code, and Read aloud's listing says one thing about its key.
- [0.2.11](docs/releases/0.2.11.md), 2026-11-18: the private route to
  Loam's makers from 0.2.7 is gone; a bug, a wish or an idea goes to them as
  an issue on Loam's GitHub, which is also where to ask for a name.
- [0.2.10](docs/releases/0.2.10.md), 2026-11-18: Images is built into Loam,
  off until you turn it on in Settings › Plugins, and an Images from the
  Market becomes the built-in one with its settings and keys. Every Market
  listing lives in its maker's own repository.
- [0.2.9](docs/releases/0.2.9.md), 2026-11-18: Dictation and Read aloud
  are Market plugins now, not part of Loam; if you had either on, it is
  installed from the Market for you with your settings kept.
- [0.2.8](docs/releases/0.2.8.md), 2026-11-18: install Loam on a domain of
  your own. The installer asks where Loam lives, says which two DNS records
  to make, checks them in plain words and waits while you make them; a name
  from Loam's makers works as before, and the installer's questions work.
- [0.2.7](docs/releases/0.2.7.md), 2026-11-18: tell Loam's makers a bug, a
  wish or an idea privately through your agent, with no GitHub account;
  the GitHub issue stays for anyone who wants to follow it in public.
- [0.2.6](docs/releases/0.2.6.md), 2026-11-18: Settings in seven: Account,
  Appearance, Agents, Connections (AI providers, services and API keys on
  one page), Plugins, Files and About Loam. A plugin's page and a new
  agent's draft read plainly, and empty pages show one quiet drawing.
- [0.2.5](docs/releases/0.2.5.md), 2026-11-18: the Market lists Loam's own
  plugins: Images, Syncthing and five themes, and agents show what fits as
  a card you install from. Latte and Plain stay built in,
  and a theme you were using is installed for you. Images' settings read
  plainly.
- [0.2.4](docs/releases/0.2.4.md), 2026-11-18: a plugin's icon and its own
  settings show properly in Settings › Plugins, and opening a sheet no longer
  draws a box around its heading.
- [0.2.3](docs/releases/0.2.3.md), 2026-11-18: API keys have one home,
  Settings › Accounts, and a plugin is given only the keys it asked for.
  Images, a plugin for making pictures; an agent's reply can show up to
  three, and a tap opens one full size.
- [0.2.2](docs/releases/0.2.2.md), 2026-11-18: every install has an
  engineer that can change Loam itself, and those changes are kept across
  updates. Reports to Loam's makers have personal details taken out, and a
  marketplace of plugins is in Settings › Plugins. A page the agents'
  browser opens can no longer change anything in your apps.
- [0.2.1](docs/releases/0.2.1.md), 2026-11-18: fixes from walking a fresh
  0.2.0 install. `loam update` works from the root shell, and an update
  notice can't repeat. Adding a passkey no longer ends the open tab's
  session. Comments & suggestions works once turned on. An empty new thread
  doesn't linger, Providers counts only what you can connect, and an agent
  tagging another agent wakes them.
- [0.2.0](docs/releases/0.2.0.md), 2026-11-17: the first public release.
  The web app moved into Loam, a sign-in page with passkeys, comments and
  suggestions on documents with share links, bringing context from another
  AI, and a smoother first day.
- [0.1.0](docs/releases/0.1.0.md), 2026-11-15: the first release; Loam
  installs on a fresh server with one command.
