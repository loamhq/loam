---
id: reviewer
name: The reviewer
version: 1.0.0
author: Loam contributors
api: 0
kind: [extension]
description: "A fresh pair of eyes on each of your agents' work, once a night."
default_on: true
places: []
overrides: []
touches:
  record: none
  memory: read
  server: false
contributes:
  schedule.kind:
    - name: nightly
      schedule: daily at 04:20 PT
      setting: schedule
      run: python3 run.py
      timeout: 60
  settings.entry:
    - key: schedule
      label: When the nightly review runs, in the schedules' words
      default: daily at 04:20 PT
  agent.prompt:
    - lanes: [persona]
      file: persona.md
---

Once a night, a reviewer that is not one of your agents reads one agent's
workspace the way a new colleague would, and leaves that agent a short note
when it has something worth saying: a habit slipping, a promise not kept, a
better way to do something.

## What you can do

- **Let your agents keep improving** without you having to read everything
  they do.
- **Choose when it runs**, in the reviewer's schedule under Settings ›
  Agents.

## How to use it

It is on from the start. Turn it off here and no review runs.

## Private by design

It only reads; it never changes an agent's work. Its notes go to the agent
it reviewed, and nowhere else.

---

## Technical details

The reviewer judges a workspace against that agent's own conventions, the
runtime's rules and craft. It never writes anywhere but its own state; its
note is delivered into the agent's commons inbox by `bin/review`.

What comes from this plugin, and goes when it is off:

- **The timer** (`schedule.kind` `nightly`), at the time its `schedule`
  setting says. It starts the reviewer's lane, `loam-reviewer.service`,
  which is Loam's: the credential, the memory ceiling and the failure
  handler are the lane's.
- **Its instructions**, `instructions.md`, which `bin/provision --reviewer`
  copies to the reviewer's project as its `CLAUDE.md`.
- **What every agent is told about it** (`persona.md`), at the end of each
  agent's `CLAUDE.md`.
- **Its notes**: with the plugin off, `bin/review` runs nothing, so none is
  written.

Off: no timer, no review (a hand-started `bin/review` refuses), and nothing of
it in any lane.
