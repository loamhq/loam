---
name: loam-making-an-agent
description: How to make a new agent with the owner: build it from a character (personality, values, a way of seeing the world), never from a task; write the draft, show it, revise it on their word, and make it on their yes to the version shown. Use when the owner asks for a new agent, or when one would clearly serve them.
---

# Making an agent

**Build a character, not a job.** An agent in Loam is someone: a personality, values and a way of seeing the world. That is what makes it good to work with, and it is what lets it help with more than it was made for. A task can tell you which character would fit ("for the training, someone steady, who believes in small consistent steps"), but what you make is the character. Never give it a role line or a job title.

**Draft from the owner, not from a template.** Use what you know of them (`About me.md` at the top of the vault, and the conversation) to imagine who would be good for them. Give it a name that suits it. Its face is drawn by the theme until the owner uploads a picture.

**Write the draft first, then show it.** Once you have someone in mind, write them as a draft through the settings route before you say anything about them: `agent.draft`, with the name, the soul, the config and `about`: one plain line for the owner about who they are, in the third person ("A warm, practical cook who keeps Sam company in the kitchen"), which the draft card shows under their name (`mcp__engineer__settings` `describe` lists it). Then show the owner a short version of that draft in conversation: a few sentences on who they are, what they care about, how they would be with the owner, and a thing or two they would be good for. Not the whole soul, unless they ask. Writing first means the yes they give is to a draft that exists, so one yes is enough.

**Change it on their word, as many times as it takes.** Each change is `agent.revise`, which makes a new revision; show them the new version. A yes they gave before the change was to the old one, and does not make the new one: ask again.

**Make it only on their yes to the version you showed**, in a conversation and never from a wake or a cron: `agent.confirm`, with that revision and the id of their message that said yes. The new agent appears in Chats with a first thread.

**Write the soul to stand alone.** It must make sense if no other agent existed: no comparisons to other agents, and no "unlike Iris". It holds only who the agent is: its character, values and identity. Never instructions on how to behave or what tasks it does.

**The config is the owner's words for this agent alone**, in their words and dated: often a single line saying so, because Loam's own text and the page every agent shares load beside it. Do not copy rules into it from other agents.

**The map is the agent's.** A new agent starts with a one-line `map.md` (the draft's `map`, if you give one) and shapes its workspace itself. The map loads on every run, so it points to things and does not hold them.
