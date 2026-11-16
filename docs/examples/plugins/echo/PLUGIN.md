---
id: echo
name: Echo
version: 1.0.0
author: Loam
api: 0
kind: [app]
icon: assets/icon.svg
server:
  run: python3 server/main.py
touches:
  network: []
  record: none
  memory: none
  server: true
---

# Echo

The smallest server plugin: a first-party example of the shape, and the
proof that Loam runs a plugin's server as its own program beside it.

It answers every request at its address, `/x/echo/`, with what it was
asked and what Loam gave it, as JSON: the path it saw (with `/x/echo` taken
off), the prefix, its id, its port and its process id. It counts the
requests it has answered in `hits.json` in its own data folder, so what it
keeps is an ordinary file that makes sense without it.

`/x/echo/crash` makes it exit with an error, on purpose: Loam restarts it
within two seconds, and after five crashes in a minute stops trying and
shows it as failing in Settings › Plugins, with its last log lines, until
someone restarts it.

## How it is put together

- `server/main.py` is the whole server: Python's standard library, no
  dependency. It listens on `127.0.0.1` at `$PORT`.
- Loam starts it because the manifest says `server: {run: …}` and the plugin
  is on; turning it off stops it.
- It is `kind: [app]`, so it is also listed in Apps, with `assets/icon.svg`.

## How to change it

Edit `server/main.py` in your layer and restart its server (Settings ›
Plugins, or `bin/settings do plugin.restart echo`). Nothing else restarts.
