---
name: loam-agent-name-and-face
description: How to rename a Loam agent or change its picture (an uploaded image over the theme's drawn face), through the same settings route the app uses. Use when the owner asks to rename an agent, give it a new face or picture, or put back the old one.
---

# An agent's name and face

The owner can do both on the agent's profile in the app. You can do it for
them, in a conversation, with `mcp__engineer__settings` (`by` your name),
which calls the same route, so the page shows the change at once. Run
`describe` first: it lists both settings with their exact values.

- **Name**: `set` `agent.display` on the agent: 1 to 40 characters, not
  another agent's id or name. `null` puts back the name its id gives it.
  The id and workspace folder stay as they are.
- **Face**: every agent has the face the theme draws for it. To give it a
  picture instead, `set` `agent.face` to the path of a PNG, JPEG, WebP or
  GIF of 2 MB at most, in a workspace or the vault; `null` goes back to the
  theme's face. An uploaded image stays through any theme change.

Each change is the owner's: make it on their word in the conversation, not
from a wake or a cron.

When you rename an agent, set the name, then look for the old name in its
soul and other files, in the vault, and in other agents' workspaces. Tell
the owner in plain words where it still names the agent.

Wait for the owner's direct yes to those edits. That yes covers the name
in the owner's soul file and in another agent's workspace. If they ask
you to rename the agent “everywhere,” they have already given it. Change
only references to the agent in current text. Leave other uses of the word
and history, including thread records and receipts, as written. Say what
you changed and what you left as history.
