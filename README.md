# Loam

Loam runs AI agents on one person's own server. Each agent has a soul, a
workspace and a memory made of plain files; the person talks with them in
threads in a web app, they keep a shared vault of notes, and they can brief
an engineer agent to change code, Loam's own included. Claude Code, Codex
and Antigravity are the providers an agent runs on; each person signs in to
their own accounts.

## Install

On a fresh Debian 12 or Ubuntu 24.04 server with a public IPv4 address, at
least 4 GB of memory and 15 GB of free disk, as root:

```sh
curl -fsSL https://raw.githubusercontent.com/loamhq/loam/main/install.sh | sudo bash -s -- --name <name>
```

It takes a few minutes, can be run again at any time, and ends with
a one-time link that claims the server. While this repository is private,
the fetch needs an install token: `docs/install.md` says how.

## What is here

| Folder | What |
|---|---|
| `install.sh`, `bin/loam-install` | the one command and its eleven steps (`docs/install.md`) |
| `bin/`, `lib/` | the runtime: the wake wrapper, the session broker, the engineer launcher, the generators, `loam status` and `loam update` |
| `mcp/`, `hooks/` | the agents' MCP servers and Claude Code hooks |
| `systemd/` | unit templates, rendered per install (`bin/gen-units`) |
| `deploy/` | the edge: Caddy, Authelia, the agents' browser and its wall (`deploy/README.md`) |
| `broker/` | the session broker's design (`broker/README.md`) |
| `app/` | the web app: Node's standard library, no dependencies (`app/README.md`) |
| `plugins/` | first-party plugins (the reviewer, daily notes) |
| `docs/` | how each part works and why; `docs/library/` becomes every install's `System/Loam/` |
| `docs/starter/` | Iris, the agent every install starts with, and her hello thread |
| `tests/` | `python3 tests/run-all` for the runtime; `cd app && npm test` for the app |
| `engineer/memory/` | what the people (and engineers) building Loam have learned |

`manifest.json` holds the version; every release is a tag equal to it.
What an update replaces and what is the person's is `docs/update.md`.

## Working on it

Read `docs/config.md` first: one file, `/etc/loam/loam.toml`, says where
everything of an install is, and nothing in the code names a place on a
particular machine. Run the tests before and after a change. Plugins are
the way to add something without changing Loam itself (`docs/plugins.md`).

## License

Loam is free software under the GNU Affero General Public License,
version 3 (AGPL-3.0-only); the full text is in `LICENSE`.
