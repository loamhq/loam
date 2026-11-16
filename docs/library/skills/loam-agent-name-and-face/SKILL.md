---
name: loam-agent-name-and-face
description: How to rename a Loam agent or change its picture (an emoji or an uploaded image), through the same settings route the app uses. Use when the owner asks to rename an agent, give it a new face or picture, or put back the old one.
---

# An agent's name and face

The owner can do both on the agent's profile in the app. You can do it for
them, in a conversation, with `mcp__engineer__settings` (`by` your name),
which calls the same route, so the page shows the change at once. Run
`describe` first: it lists both settings with their exact values.

- **Name**: `set` `agent.display` on the agent: 1 to 40 characters, not
  another agent's id or name. `null` puts back the name its id gives it. The
  id, and the workspace's folder, never change; the agent's own files keep
  calling it what they call it.
- **Face, an emoji**: `set` `agent.emoji`: one emoji.
- **Face, an image**: `set` `agent.face` to the path of a PNG, JPEG, WebP
  or GIF of 2 MB at most, in a workspace or the vault; `null` goes back to
  the emoji. An uploaded image stays through any theme change.

Each change is the owner's: make it on their word in the conversation, not
from a wake or a cron.
