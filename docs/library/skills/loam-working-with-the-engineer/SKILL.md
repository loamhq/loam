---
name: loam-working-with-the-engineer
description: The essentials of handing work to the engineer - whether to, at which tier, what the brief says, and how to check what comes back. Use before every mcp__engineer__brief call and whenever a receipt comes back.
---

# Working with the engineer

1. **Do it yourself, or hand it over?** A settings change or a small file edit, do yourself. The engineer is for changing code.

2. **Pick the tier** (the brief's `tier`: `quick`, `standard` or `hard`). Quick: the job is fully spelled out and there is one obvious way to do it. Standard: most jobs, and whenever you are unsure; it is the default. Hard: design judgment, a bug nobody understands yet, or work across many parts where getting it wrong is expensive. If a quick job comes back wrong, run it again at standard rather than explaining it harder.

3. **Say what must be true, not how to build it:** the problem and its evidence; what must be true afterwards, including what must keep working; the owner's decisions, quoted; your suggestion, which the engineer may improve on.

4. **Decide the proof up front.** Name what "done" has to come with: a test, a quoted line, a before and after, a screenshot of the real page.

5. **Check before trusting it.** Read what actually changed, not just the summary, and look at it yourself before it goes live.

6. **Nothing waits on a job.** `brief` returns at once with the job id. The result comes back to you as your next input when the job ends: a turn in the thread you briefed from, or, from a wake or a cron, a new run that resumes that session. Nothing is posted; say something only if the owner needs to hear it.

7. **Run jobs side by side**: send each one when it is ready. Hold one back only when both change the same feature or screen, or one depends on the other, and then say in the brief which one wins.

8. **Tell the owner the outcome in a few lines:** what is done and what they will see.
