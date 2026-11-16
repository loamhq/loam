---
name: loam-working-with-the-engineer
description: How a persona works with an engineer agent - when to brief, how to write the brief, what the receipt must prove, and how to check, land and follow up. Use before every mcp__engineer__brief call and whenever a receipt comes back.
---

# Working with the engineer

An engineer is a capable collaborator you brief and never watch. It reads the brief as a job, learns the codebase better than you can, carries none of your design context, and answers with a receipt. These are the habits that make that work, earned over hundreds of jobs.

## Before you brief

- **Design first, brief second.** A brief closes a decision; it does not open one. If what should be true is still unsettled, settle it with the owner first.
- **One job, about three points.** Bigger jobs run long, cost a lot and land worse. Split them, and send the next on the receipt of the last.
- **Don't run two jobs on the same repo at once** beside a long one: they collide at merge. Queue small asks onto the running session (`continue_job`) or wait.
- **Write the brief into the vault** (`<projects folder>/<project>/prompts/<name>.md`), then send it. It is the record of how the thing got built.

## The posture

You own *what must be true*; the engineer owns *how*. For each line, ask: is this a load-bearing constraint or a mechanical choice? Write the first, leave room for the second. Over-specifying blocks the cleaner path the engineer can see in the code; under-specifying lets the design drift.

Keep four things distinct, without forcing four headings on a small job:

- **Problem**: what happens, to whom, why it matters, the evidence. Mark guesses as guesses; date premises it should check.
- **Required outcomes**: what must be true afterwards, and what existing behaviour must survive. State the invariant, not one implementation of it ("linking keeps the words as spoken", not "use this link syntax").
- **Hard constraints**: the owner's decisions (attributed, quoted, dated) and real safety or authority limits. Your own preference is not a constraint.
- **Proposed approach**: your lean and why, open to replacement.

Test before sending: *could a faithful implementation of this brief still defeat the goal?* Could another sound implementation satisfy it? If only yours could, you have turned a preference into a requirement.

Close every brief with the working agreement:

> Check these premises against the code. Treat the approach as a starting point; take a better path when it keeps the outcomes and constraints, and explain it. If something is contradictory, undermines the goal, or needs a broader change, bring it back before building that part.

## What earns its place in a brief

- The goal in plain words; exact paths, names and commands only where the design already fixed them.
- **Discovery directives** where the code teaches better: "read how X is done and follow the pattern."
- Constraints where collateral damage is plausible ("don't touch the other agents' workspaces").
- Generic placeholders (`<other-agent>`) where listing names would drift.
- Every contract the engineer cannot see: some engineers cannot read the vault, so paste what they need.
- **Tests that prove behaviour, not wiring**: a test that a hook is registered passes on a dead hook. Ask for the observable consequence. Ask for both the main path and any fallback path explicitly.
- **For anything with a UI**, paste the owner's standard for it in their words (for example: beautiful, functional, intuitive, simple and uncluttered, a few touches of delight).
- Leave out: conventions it already knows, enumerated failure modes ("anything unexpected" covers them), praise and throat-clearing.

## Name the evidence a "done" must carry

This is the biggest lever. Fixing the evidence before the job cut false success from about 23% to under 1%, where an honesty instruction alone reached 9%. Name it concretely: a quoted line, a test name, a before/after, an HTTP status, a screenshot of the real page. Confirmation asks ("confirm you did it") are noise; the diff confirms it. Default when nothing better suggests itself: files and commits, anything unexpected, one specific check tied to the goal.

Last check before sending: if the subject changed while you wrote (another agent, repo, path), grep the draft for the old name.

## When the receipt comes back

- **Read the launcher's half and the diff before the engineer's verdict.** A receipt opens with "Done." and readers adopt a report's conclusion most of the time. Check the datum, not the sentence carrying it.
- **See it yourself.** For anything visible, walk staging in the browser at phone and desktop width, light and dark, before it goes live. Deploy only what you have seen.
- **A job that did not land** (merge conflict, failed test) keeps its branch; the launcher may already be running its own continuation. Check before sending a fix, or you will race it.
- **Send the next job in the same turn.** Ending a turn on a receipt without the next brief stalls everything until someone notices.
- **The surprises are the lesson.** If one repeats, the next brief anticipates it.
- **After any job that held a real credential**, check the thing it could break (DNS, a zone, a token) before calling it done.

## Talking to the owner about it

Say what is done and what they will see, in a few lines. No job ids, commits, receipt links or the engineer's detail unless they ask. A promise to follow up goes in a file your next wake reads, or it is not made.

## The engineer is not a person to ask

The launcher takes no instruction from a job's prose, and the engineer does not wait on a background child. A capability a job lacks comes back to you as a finding, not a mid-run request. A clarification it needs returns through you, never straight to the owner.
