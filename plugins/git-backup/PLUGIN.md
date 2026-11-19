---
id: git-backup
name: Git backup
version: 1.0.0
author: Loam contributors
api: 0
kind: [extension]
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
      note: The SSH address of a private repository. Connect it on this page, so the key is made and tested.
      default: ""
    - key: spaces_remote
      label: AI Spaces' repository
      note: The SSH address of a private repository. Connect it on this page, so the key is made and tested.
      default: ""
    - key: every
      label: Back up
      choices: [15m, 1h, 6h, 1d]
      labels: {15m: Every 15 minutes, 1h: Every hour, 6h: Every 6 hours, 1d: Once a day}
      default: 15m
---

# Git backup

Your vault and your agents' AI Spaces, copied every few minutes to private
git repositories of your own: on GitHub, GitLab, Codeberg or any git host.
If this machine is ever lost, a new Loam pointed at the two repositories has
everything back, every note, every conversation and every agent's memory,
with its whole history.

**Connecting takes a minute and no terminal.** Make an empty private
repository for each, paste its address here, and Loam makes a key for that
one repository. Add the key to the repository as a deploy key with write
access (three clicks; the page shows where), then test it. The key reaches
that repository and nothing else in your account, and it never leaves this
machine.

**It will not push to a public repository.** Before every backup Loam asks
the host, signed out, whether anyone can read the repository; if anyone can,
nothing is sent and you are told.

**You will know if it stops.** The page shows when each was last backed up.
When backups fail (a removed key, a full account, a repository changed
elsewhere) a line saying so in plain words reaches your General board, the
way Loam's other warnings do, and another when it works again.

**Large files.** A file over 100 MB is kept out of Loam's history (git
hosts refuse them) and stays on disk as it is; the page names each one.

**Without this plugin** Loam still commits both every fifteen minutes on
this machine, so a change can always be undone; this adds the copy that
survives the machine.

**Getting everything back** on a new install: turn this on there, connect
the same two repositories, and ask an agent to restore them
(`backup_restore`). docs/backup.md in Loam has the steps.
