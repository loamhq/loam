---
name: loam-filing-a-bug-or-feature
description: How to tell the people who make Loam something - a bug, a feature request, an idea, a suggestion or any feedback ("I wish", "this confused me", "I love") - as an issue on Loam's GitHub, always with personal details taken out and on the owner's yes; and how to fix a defect on this install and offer the fix upstream. Use when the owner says "tell the makers", "send feedback", "I have an idea for Loam", "suggest", or when something in Loam is broken or behaves unexpectedly.
---

# Telling Loam's makers: a bug, a feature, an idea

The people who make Loam read every report. An idea is worth as much as a
bug: what the owner wishes for, what confused them, what they love. Send it
whenever the owner wants it to reach them. A defect that cannot wait can
also be fixed here.

## Offer once, where it happened

When Loam itself fails, including a broken route or tool, prepare the draft
first. For a defect the engineer has fixed, use the saved patch preview at
the end of its receipt. An error in the owner's content stays with the task.
Choose a stable short topic from the component and symptom (for example
`report:threads-lost-message`). Use the same topic if it recurs. Run
`loam report offer TOPIC` just before offering. Only “Offer once” calls for
a line: “Loam lost that message. I have a report ready. Would you like to
see it?” An invitation to show the draft never authorizes sending it.

If a requested change could be a plugin, use a `market:PLUGIN-ID` topic
and the same offer check. Ask “Would you like to share this in the Market?”
Use `loam-publishing-to-the-marketplace` for the publication preview and
approval. On a no to either offer, run `loam report decline TOPIC`. Leave
it there unless the owner reopens it.

## Write it up

**A bug.** Cause it again if you can, then collect what you did, what you
expected and what happened instead, word for word (the error, the tool
result, the page's line); when, on which provider and lane (a conversation,
a wake, a cron); and whether it happens every time.

**A feature.** Say what must be true when it is done and why it matters to
the owner, in their words where you have them, and what happens today. Do
not design the mechanism. First ask whether a plugin could do it (see
`loam-building-a-plugin`): a plugin needs nobody's release.

**An idea** is anything else: "I wish…", "this confused me", "I love…".
Write the owner's thought plainly, in their words where you have them, with
what they were doing when it came up.

Never quote a note, a thread, a memory or anything else from the vault into
a report. Describe what was there instead ("a note with a long table").

## Draft it

```
loam report idea --title "The morning note could be shorter" --body "…"
loam report bug --title "Threads lose the last message after a restart" --body-file report.md
loam report feature --title "…" --body "…"
```

Nothing is sent. It answers with the report exactly as it would go, a list
of what was taken out (the owner's name, agents' names, paths, this
install's address, keys, e-mail and IP addresses, note names), and a link.
Read the report again yourself for anything personal it missed.

Bug previews include a bounded diagnostics block by default. It lists the
release and commit, local changes, OS, provider/model and lane, service
states, installed plugin versions, and recent failure codes from runtime
logs. For an earlier incident, `--at 2026-11-01T09:15:00Z` selects the
hour ending at that instant. Missing evidence is marked. Use `--no-diagnostics` to leave it out
or `--diagnostics` to add it to an idea or feature. A changed draft needs
a fresh preview and a fresh yes.

## Show every word and ask “Send it?”

1. Show the saved title, complete body (including diagnostics and any
   diff), destination, labels, and removal list. When a relay is configured,
   explain that it sends from the makers' bot and uses a random install ID
   to limit requests. The preview ID retrieves the same words with
   `loam report preview ID`. Wait for the owner's yes to that exact draft.
2. When the preview offers direct delivery, use `loam report send ID --yes`.
   No account is needed. Give the owner the returned issue link. A dry run
   is only a test. If the relay reports another privacy match, edit the
   draft locally and show it again. Retry an uncertain delivery with the
   same ID.
3. Owners who want to file from their own account can use the preview's
   GitHub link after approval. Until the makers enable the relay, explain
   plainly that filing currently needs a GitHub account. Keep the local
   draft if they have none. For a long report, paste the approved body in
   the form. After Submit, ask for the resulting issue link and use
   `loam report track ID ISSUE_URL`. Opening the form alone files nothing.

Loam checks tracked public issues nightly without signing in. When a maker
replies or a tagged release fixes a reported bug, the agent chosen for
install news tells the owner in one line with the issue link. Stay quiet
between updates.

## Fix it here, if it cannot wait

Brief the engineer with `mcp__engineer__brief`, project `Loam` (see
`loam-working-with-the-engineer`). Every install has one since Loam 0.2.2.
If a brief is refused because the engineer is not set up (an install
older than that, not yet updated), tell the owner plainly: updating Loam
sets it up, the report to Loam's makers still goes, and a plugin may still
do it. A change to Loam's
own code stays on this install: it is never sent anywhere, and every update
keeps it, merging the new release around it. If a release later changes the
same lines, the update stops before anything moves, names the files, and
asks you to sort it out with the owner; nothing of theirs is lost. So:

- **Prefer a plugin** whenever one can do it: a theme, page code, a tool, a
  timer, a server. Plugins live in the owner's plugins folder, which updates
  never touch. Change Loam's own code only for what no plugin can reach.
- Read the next release's notes for the fix; when it arrives, the local
  change may no longer be needed.

## Offer the fix upstream

When the engineer has fixed a core bug here, the fix can go with the report:

```
loam report bug --title "…" --body-file report.md --patch
```

`--patch` adds the diff of this install's own changes to Loam since the
release it runs, with the same things taken out. Prefer `--patch BASE..FIX`
to include only the fix in question. End the engineer's receipt with the
draft, patch and saved preview ID. The briefing agent makes the single
offer above. The makers decide how to implement a fix.
