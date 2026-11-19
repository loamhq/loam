---
id: daily-notes
name: Daily notes
version: 1.0.0
author: Loam contributors
api: 0
kind: [extension]
description: "Today's note, one tap away in Files."
default_on: false
places: [files.action]
overrides: []
touches:
  record: none
  memory: none
  server: false
contributes:
  settings.entry:
    - key: folder
      label: The daily notes folder, in the vault
      default: Daily
  vault.folder:
    - setting: folder
---

Open Files and press **Open daily note**: today's note is there, ready to
write in. Each day gets its own, named by its date, in a folder of your
vault, the way Obsidian keeps them.

## What you can do

- **Start the day in one tap.** The note is made, empty, the first time you
  open it that day, in your own time zone.
- **Keep them where you like.** Choose the folder below; it is *Daily* until
  you change it.
- **Read them anywhere.** They are ordinary notes in your vault, so Obsidian
  and your agents see them too.

## How to use it

1. Turn it on here.
2. In Files, press **Open daily note**.

Turning it off takes the button away. Your notes stay where they are.

---

## Technical details

Notes are `YYYY-MM-DD.md` in the `folder` setting (`Daily` unless set),
Obsidian's own daily format. Turning it on makes the folder if the vault has
none; changing the folder makes the new one and leaves the old one as it is.
It contacts no network host, reads no conversation or memory, and runs no
server code.
