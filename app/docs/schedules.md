# Agent schedules

The agent profile shows a compact Schedule group with labels, plain schedules,
next runs and enabled switches. Tapping a row opens its schedule and last result;
changes use `PATCH /api/schedules/<agent>/<name>`. **Manage schedules** opens the
full file list and creation form described below.

The full schedule list is a list of files. Each row shows the name, next run in PT,
relative last edit and actor role, file state, and a dot when the host says
`changed_since_seen`. Live is quiet, off is muted, and broken/refused use the
error colour and say whether the previous version still runs. State words
are shortened from `state`, `running` and `problem.line`; the full broker
`state_words` remains the accessible description. Text wraps on narrow screens.
The supplied [contract](broker-schedules-and-activity-api.md#a-schedule-is-its-file-the-one-line-row-and-the-files-save-2026-10-15)
is the wire reference. Its 2026-10-15 section supersedes the older schedule
editor and [historical closure checklist](schedules-contract-checklist.md).

Tapping the row opens its exact `file` resource in the existing Files editor.
The full list opens Files; the compact profile group edits timing inline. Files uses the host's `schedule`
authority and `capabilities.write`, including the instruction in
`crons/reflection.md`. Folder creation
capabilities stay with the host. New schedules still use the creation form
and `POST /api/schedules/<agent>` with `name`, `schedule` or `at`, `body`,
and one-off `note`. The broker validates PT grammar and timing limits, and
refusals keep the creation draft.

The switch PATCHes only `{enabled}`. A broken-file refusal is shown verbatim
beside the row, without an optimistic flip. Run now still requests a run,
reports “Run requested.” and invents no outcome. Off/completed one-offs cannot
run; other refusals come from the broker. A completed one-off disappears on
the next list read.

Files saves through its existing revisioned `PUT /api/files/text`, with its
usual 409 conflict handling. A `schedule` in the save response produces a
quiet message beside the editor: “Saved · runs …” for live, the broker's
state words for off, broken, refused or pending, plus the problem line and
its text when supplied. A broken schedule is still saved; it is not an HTTP
save failure. The previous accepted version's execution is only stated when
the broker says so. `provenance: false` adds no stamp; the browser always
holds the returned bytes. A new draft clears the old save result.

Opening the Schedule tab POSTs seen for its changed rows, then re-reads the
host list. Background profile loads and the existing five-second visible-tab
refresh do not mark seen: a later change can raise a fresh dot. Opening a
schedule in Files also marks it seen, including direct URLs and reloads.
Files identifies that route only for the host's `schedule` authority at the
contracted `spaces/<agent>/crons/<name>.md` path. A seen failure is visible
without hiding a successfully read file. State and seen marks have no browser
storage. Activity retains the shared change IDs, diff, reason and Undo.

The old Schedule-local delete and Undo controls have been removed with the
detail form, as requested by “keep only” switch/run/create. Undo remains in
Activity. DELETE remains in the relay contract, but this page now offers no
schedule-delete button. The file remains the editing surface; no new file
removal mechanism was added.

The supplied new broker contract was described as available after the next
broker restart. Tests use its example responses, not a claim about a running
host. `npm test` covers the relay, controller, Files save responses, conflicts,
seen, controls and phone DOM. The browser driver paints the real app against
those fixtures at 390 and 1440 in light and dark:

```sh
timeout 180s env LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs \
  LOAM_CHROME=/path/to/chromium SCHEDULE_FILE_EVIDENCE=/tmp/schedules-file-first \
  node test/schedules-file-first-browser.mjs
```

It checks host seen marks, later-change dots, keyboard switches/focus,
Run now, broken switch refusals, Files navigation, exact saved bytes and
problem-line feedback, error colour, overflow and page errors. Eight paints
and `checks.json` are written outside the repository. It starts no agent or
timer and requires no new production dependency or configuration.
