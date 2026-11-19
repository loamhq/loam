---
name: loam-filing-a-bug-or-feature
description: How to tell the people who make Loam something - a bug, a feature request, an idea, a suggestion or any feedback ("I wish", "this confused me", "I love") - privately with no account needed, or as a public GitHub issue, always with personal details taken out and on the owner's yes; and how to fix a defect on this install and offer the fix upstream. Use when the owner says "tell the makers", "send feedback", "I have an idea for Loam", "suggest", or when something in Loam is broken or behaves unexpectedly.
---

# Telling Loam's makers: a bug, a feature, an idea

The people who make Loam read every report. An idea is worth as much as a
bug: what the owner wishes for, what confused them, what they love. Send it
whenever the owner wants it to reach them. A defect that cannot wait can
also be fixed here.

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

Drafting sends nothing. It answers with the report exactly as it would go,
a list of what was taken out (the owner's name, agents' names, paths, this
install's address, keys, e-mail and IP addresses, note names), the report's
id, and the ways to send it. Read the report again yourself for anything
personal it missed.

## Show it, then send it on their yes

1. **Show the owner every word**: the title and body as printed, and what
   was taken out. Ask whether to send it. Nothing goes without their yes.
2. **Privately, by default.** On their yes, `loam report send <id>`. It goes
   to a private inbox only Loam's makers read; no account is needed. It
   carries this install's web address so the makers' box can tell it comes
   from a Loam install; they keep a code for the install, not the address.
   Tell the owner it was sent, that the makers read every one, and that they
   may not answer.
3. **On GitHub, if they want to follow it in public.** Give them the link
   the draft printed instead. It opens GitHub's new-issue page for Loam with
   the report filled in; they need a GitHub account (free, at
   github.com/signup), and signed in, they press **Submit new issue**. A
   very long report comes with its title only; they paste the body you
   showed them. The makers may answer on the issue.

If the draft says it cannot go privately (an install older than 0.2.7, one
with no web address of its own, or one not yet claimed), the GitHub link is
the way, or updating Loam. A refusal from `send` is said in plain words
(too many today, too long); tell the owner and try later or use the link.
The owner's connected GitHub (Settings › Connections) cannot file an issue
for them: it reaches only repositories on their own account.

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
on their yes either way. A pull request would need a fork and a push from
the owner's GitHub account that Loam cannot make for them yet; the patch in
the report is what a maintainer applies.
