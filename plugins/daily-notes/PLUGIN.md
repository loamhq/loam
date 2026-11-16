---
id: daily-notes
name: Daily notes
version: 1.0.0
author: Loam contributors
api: 0
kind: [extension]
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

Today's note, one tap from Files: `YYYY-MM-DD.md` in the daily folder
(Obsidian's own daily format), made empty the first time it is opened that
day, in the person's own time zone.

Turning it on makes the folder (`folder`, `Daily` unless set) if the vault has
none, and puts *Open daily note* in Files. Turning it off takes the button
away; the folder and every note in it stay. Changing the folder makes the new
one; the old one is left as it is.
