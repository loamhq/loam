---
name: loam-agent-name-and-face
description: How to rename a Loam agent or change its picture (an uploaded image over the theme's drawn face), through the same settings route the app uses. Use when the owner asks to rename an agent, give it a new face or picture, or put back the old one.
---

# An agent's name and face

The owner can do both on the agent's profile in the app. You can do it for
them, in a conversation, with `mcp__engineer__settings` (`by` your name),
which calls the same route, so the page shows the change at once. Run
`describe` first: it lists both settings with their exact values.

- **Name**: `set` `agent.display` on the agent: 1 to 40 characters, producing
  a valid folder slug (`a-z`, `0-9`, `-`, 2–24 characters), not reserved and
  not another agent's id, name, or past alias. `null` resets the display casing
  for the agent's current id. Changing the name renames the agent everywhere:
  its id, workspace folder (`ai-labs/<new>`), wake and cron units, commons
  inbox, rendered provider/runtime files, and registries all move to the new
  name, while keeping git history, settings, plugin preferences, and resumable
  conversations intact. Vault thread records (`15_CONVERSATIONS/*.md`) stay
  untouched as history and resolve the old id to the renamed agent via
  `former_ids`. The agent must be idle to move its folder (a mid-turn rename,
  including an agent renaming itself during its own turn, is refused — ask the
  owner to rename it from its profile page, or have another agent rename it
  once it is idle).
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
in that agent's soul and in other agents' workspaces. If they ask
you to rename the agent “everywhere,” they have already given it. Change
only references to the agent in current text. Leave other uses of the word
and history, including thread records and receipts, as written. Say what
you changed and what you left as history.
