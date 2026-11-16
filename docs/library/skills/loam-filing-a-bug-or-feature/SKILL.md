---
name: loam-filing-a-bug-or-feature
description: How to get a defect in Loam fixed or a change made - what to collect, and how to hand it to the engineer as a brief. Use when something in Loam is broken, behaves unexpectedly, or when the owner wants Loam to do something new.
---

# Filing a bug or a feature

Loam's changes are made by its engineer, and an agent hands it the work.
The owner never needs to touch the code.

**A bug.** Before writing it up, cause it again if you can, and collect:

- what you did, what you expected, and what happened instead, word for word
  (the error, the tool result, the page's console line);
- when it happened, with the date and time, and on which provider and lane
  (a conversation, a wake, a cron);
- whether it happens every time.

**A feature.** Say what must be true when it is done and why it matters to
the owner, in their words where you have them. Say what you know of how it
works today and what you are unsure of. Do not design the mechanism: the
engineer reads the code first.

**Hand it over** with `mcp__engineer__brief`, project `Loam`, with a short
`title`, the write-up as the brief, and `involvement` as the owner's words
for this job (by default: go and do it, the owner reviews after). Anything
that is more than a minute or two, call from a background subagent and keep
talking. The receipt comes back with the evidence for every claim it makes;
read it before you tell the owner something is fixed.

If the owner would rather decide first, put it on the thread's board as an
item instead, and brief it on their word.
