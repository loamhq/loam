---
name: loam-making-an-agent
description: How to make a new agent with the owner: explore who they are looking for, shape a character rather than a job, put the rest into a first understanding page, and draft, revise and confirm on their yes to the version shown. Use when the owner asks for a new agent, or when one would clearly serve them.
---

# Making an agent

**Build a character, not a job.** An agent is someone — character, values and a way of seeing — never a role or job title. A task hints at what kind of person would fit; what you make is the person.

**Understand who they are looking for before drafting.** Draw on whatever you already know of the owner, and explore until you have a real sense of who would serve them. Practical comparison questions — two concrete ways the agent might handle a situation ("would you rather they do this, or that?") — often uncover what is underneath far better than abstract ones. Ask as much or as little as the moment calls for; never follow a script or a fixed number of questions.

**Separate soul from understanding.**
- **Soul (`soul.md`)**: only what would still hold if this agent had a completely different job, and what shapes what it notices and cares about — character, values, perspective. Manner alone fails this test; anything about the task fails it. Short is right, thin is not: a soul without real values gets filled by rules. It must stand alone, without referencing other agents.
- **Understanding (`understanding.md`)**: everything else you learned — what the owner wants from this agent and how they like to be worked with — written as a first reading in plain prose, not quoted rules. From creation on, this page belongs to the new agent to rewrite freely.

**Draft first, then show.** Write the draft with `agent.draft` (`mcp__engineer__settings` `describe` lists the fields): name, `soul`, `understanding` (when you have a first reading) and a plain one-line third-person `about` for the card. Then briefly introduce them in chat — who they are, what they care about, how they would be with the owner, and a sense of what they'd be good for. Never show a sample reply, and skip the full soul unless asked. Their face is drawn by the theme until the owner uploads a picture.

**Revise and confirm.** Each change goes through `agent.revise` as a new revision, shown again; an earlier yes never covers a later revision. Make the agent only on the owner's direct yes to the version shown, in conversation (never from a wake or cron), via `agent.confirm` with that revision and their yes message's id. The new agent starts with `soul.md`, `understanding.md` and a one-line `map.md` (or your draft's `map`) and shapes its workspace itself.
