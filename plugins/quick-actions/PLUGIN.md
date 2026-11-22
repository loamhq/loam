---
id: quick-actions
name: Files quick actions
version: 1.0.0
author: Loam contributors
api: 0
kind: [extension]
description: "One-tap buttons at the top of Files for a new note or today’s note."
default_on: false
places: [files.action]
overrides: []
touches:
  record: none
  memory: none
  server: false
contributes:
  settings.entry:
    - key: actions
      label: Actions to show
      choices: [both, new-note, daily-note]
      labels:
        both: "New note and Today’s note"
        new-note: "New note"
        daily-note: "Today’s note"
      note: Choose whether Files shows New note, Today’s note, or both.
      default: both
    - key: inbox_folder
      label: New note folder
      note: Where New note saves new notes, inside Your files.
      default: Inbox
    - key: daily_folder
      label: Today’s note folder
      note: Where Today’s note saves YYYY-MM-DD.md notes, inside Your files.
      default: Daily
  vault.folder:
    - setting: inbox_folder
      when:
        setting: actions
        in: [both, new-note]
    - setting: daily_folder
      when:
        setting: actions
        in: [both, daily-note]
---

Add one-tap note buttons at the top of **Files**: **New note** for quick
capture into an inbox folder, and **Today’s note** for a daily note named by
today’s date.

## What you can do

- **Capture a thought fast.** **New note** creates and opens a timestamped
  Markdown note in your chosen folder.
- **Start the day in one tap.** **Today’s note** opens today’s `YYYY-MM-DD.md`
  note (creating it the first time you tap it that day, in your time zone).
- **Show only what you want.** Choose both actions, New note only, or Today’s
  note only, and pick which folder each uses.

## How to use it

1. Turn **Files quick actions** on here.
2. Choose which actions to show in Files and which folders they write into.
3. Open **Files** to use them right at the top.

Turning it off removes the buttons from Files. Your notes stay where they are.

---

## Technical details

**New note** creates `YYYY-MM-DD HHMM.md` inside `inbox_folder` (`Inbox` unless
set). **Today’s note** opens or creates `YYYY-MM-DD.md` inside `daily_folder`
(`Daily` unless set). A folder is created only when its action is enabled or
when something writes to it. It contacts no network host, reads no conversation
or memory, and runs no server code.
