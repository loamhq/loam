# The starter: Iris and the hello thread

A blank Loam install ships one agent, Iris, and one thread: the hello
thread, where the person lands after signing in. Everything here is copied
into the install once, by `bin/library seed-starter` (lib/loam_starter.py),
and from then on belongs to the person: Iris is an ordinary agent they can
change, rename or archive, and nothing here is copied again.

**Every word in this folder is a placeholder.** The wording of Iris's soul
and of the hello conversation is designed later with the owner (decision 4,
2026-11-14). To change it, edit these files and nothing else:

| File | What it becomes |
|---|---|
| `iris/agent.json` | Iris's id, name and face (an emoji) |
| `iris/soul.md` | her `soul.md`: who she is |
| `iris/config.md` | her `config.md`: her rules |
| `iris/_active.md` | what she carries into every conversation, the hello conversation's plan included |
| `iris/_reflection.md` | what she does on her nightly wake |
| `hello.md` | the hello thread: its title (frontmatter) and the first message, written as Iris (the body) |

In the text, `{owner}` is the owner's name and `@VAULT@`, `@WORKSPACES@` and
`@SYSTEM@` are this install's vault, workspaces folder and the vault's
system folder, filled in when the starter is seeded.

The first message is posted as Iris but is not a model's turn: its record
says `"preset": true` (docs/starter.md). The person's reply is the first
thing Iris's model sees, with her preset opening marked as Loam's.
