---
name: loam-filing-a-bug-or-feature
description: How to get a defect in Loam fixed or a change made - reporting it to the people who make Loam (with personal details taken out, on the owner's yes), fixing it on this install, and offering a fix upstream. Use when something in Loam is broken, behaves unexpectedly, or when the owner wants Loam to do something new.
---

# Filing a bug or a feature

A defect in Loam's core is worth two things: telling the people who make
Loam, so every install gets the fix, and, if it cannot wait, fixing it here.
Do the first always, the second when the owner wants it.

## Write it up

**A bug.** Cause it again if you can, then collect what you did, what you
expected and what happened instead, word for word (the error, the tool
result, the page's line); when, on which provider and lane (a conversation,
a wake, a cron); and whether it happens every time.

**A feature.** Say what must be true when it is done and why it matters to
the owner, in their words where you have them, and what happens today. Do
not design the mechanism. First ask whether a plugin could do it (see
`loam-building-a-plugin`): a plugin needs nobody's release.

Never quote a note, a thread, a memory or anything else from the vault into
a report. Describe what was there instead ("a note with a long table").

## Tell Loam's makers

```
loam report bug --title "Threads lose the last message after a restart" --body-file report.md
loam report feature --title "…" --body "…"
```

Nothing is sent. It answers with the report exactly as it would go, a list
of what was taken out (the owner's name, agents' names, paths, this
install's address, keys, e-mail and IP addresses, note names), and a link.
Read the report again yourself for anything personal it missed.

1. **Show the owner every word**: the title and body as printed, and what
   was taken out. Ask whether to send it.
2. **On their yes, give them the link.** It opens GitHub's new-issue page
   for Loam with the report filled in. They need a GitHub account (free, at
   github.com/signup); signed in, they press **Submit new issue**, and it is
   sent from their account. A very long report comes with its title only;
   they paste the body you showed them.
3. Tell them where it went, and that the makers may answer on the issue.

The owner's connected GitHub (Settings › Connections) cannot file it for
them: it reaches only repositories on their own account.

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
release it runs, with the same things taken out. Show the owner, and send it
on their yes the same way. A pull request would need a fork and a push from
the owner's GitHub account that Loam cannot make for them yet; the patch in
the issue is what a maintainer applies.
