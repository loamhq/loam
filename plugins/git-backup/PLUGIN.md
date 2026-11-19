---
id: git-backup
name: Git backup
version: 1.0.0
author: Loam contributors
api: 0
kind: [extension]
description: "A copy of your notes and your agents' memory, kept safe off this machine."
default_on: false
icon: assets/icon.svg
places: [settings.section]
server:
  run: python3 server/main.py
touches:
  network:
    - host: the git host you connect (GitHub, GitLab, Codeberg, your own)
      why: Sends your vault's and AI Spaces' history to the private repositories you connect, over SSH with a key that reaches only that repository; asks the same host, signed out, whether each repository is public
  record: write
  memory: write
  server: true
contributes:
  agent.tool:
    - name: git-backup
      run: python3 tools/backup_mcp.py
      lanes: [persona]
  agent.prompt:
    - lanes: [persona]
      text: "Your owner's vault and AI Spaces can be backed up to private git repositories of theirs (the Git backup plugin). backup_status says whether each is backed up, when it last was, and in plain words what is wrong when it is failing; read it before saying anything about backups. To connect one, ask the owner for an empty private repository's address and, on their word, call backup_connect; give them its public key and steps as they are, then backup_test once they say the key is added. backup_restore brings a tree back onto a new install, on their word only."
  schedule.kind:
    - name: push
      calendar: "*:02/05:30"
      run: python3 tools/backup.py push
      timeout: 3600
  settings.entry:
    - key: vault_remote
      label: Vault's repository
      note: The SSH address of a private repository, or a folder on this machine. Connect it on this page, so it is checked and tested.
      default: ""
    - key: spaces_remote
      label: AI Spaces' repository
      note: The SSH address of a private repository, or a folder on this machine. Connect it on this page, so it is checked and tested.
      default: ""
    - key: every
      label: Back up
      choices: [15m, 1h, 6h, 1d]
      labels: {15m: Every 15 minutes, 1h: Every hour, 6h: Every 6 hours, 1d: Once a day}
      default: 15m
---

Every few minutes, your vault and your agents' AI Spaces are copied to
private repositories of your own: on GitHub, GitLab, Codeberg or any git
host, on a server of your own, or in a folder on this machine or a drive
attached to it. If this machine is ever lost, a new Loam pointed at them has
everything back: every note, every conversation and every agent's memory,
with its whole history.

## What you can do

- **Set it up in a minute, no terminal.** Paste the address of an empty
  private repository; Loam makes a key for that one repository and shows
  you where to add it. A folder or a drive needs no key at all.
- **Know it is working.** This page shows when each was last backed up. If
  backups stop, the agent you chose to hear of Loam's problems is told
  why, and again when they work again; it tells you if you need to know.
- **Choose how often.** Every 15 minutes, every hour, every 6 hours or once a
  day.
- **Get everything back.** On a new Loam, connect the same two repositories
  and ask an agent to restore them.

## How to use it

1. Make two empty **private** repositories (one for your vault, one for your
   AI Spaces), or choose two folders.
2. Paste each address below. For a repository, add the key Loam makes as a
   deploy key with write access (the page shows where).
3. Press **Test**. That's it.

## Private by design

It only ever sends to the places you connect, with a key that reaches
nothing else in your account and never leaves this machine. It refuses to
send anything to a repository anyone can read.

---

## Technical details

Before every backup Loam asks the host, signed out, whether the repository
is public; if it is, nothing is sent and you are told. Pushes are over SSH
with a deploy key per repository, kept in the plugin's private folder. On a
server of your own, add the key the same way if it runs a git host, or to
the account's `~/.ssh/authorized_keys` if it is plain SSH. A folder on this
machine needs no key: paste its path (Loam makes the folder a repository
when it is new or empty) and test it.

Pushes are never forced: a repository that another machine has written to
reports "diverged". A file over 100 MB is kept out of the history (git hosts
refuse them), stays on disk, and is named on this page.

Without this plugin Loam still commits both every fifteen minutes on this
machine, so a change can always be undone; this adds the copy that survives
the machine. Agents have `backup_status`, `backup_connect`, `backup_test`
and, on your word only, `backup_restore`. The schedule is the `every`
setting; `docs/backup.md` in Loam has the restore steps.
