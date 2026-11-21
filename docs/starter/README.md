# The starter: Iris and the hello thread

A blank Loam install ships one agent, Iris, and one thread: the hello
thread. After sign-in a phone shows Hello unread in Chats, and a wide screen opens it. Everything here is copied
into the install once, by `bin/library seed-starter` (lib/loam_starter.py),
and from then on belongs to the person. They can change, rename or archive
Iris. Her soul, config and map load on every run.

The words are the owner's approved starter (2026-11-16), which replaced the
placeholders of 2026-11-14. The source is the design note "Starter Iris: soul
and hello". The greeting and first-session guidance were revised on 2026-11-20.

| File | What it becomes |
|---|---|
| `iris/agent.json` | Iris's id and name (her face is the theme's until the person uploads one) |
| `iris/soul.md` | her `soul.md`: who she is (character, values and identity, nothing about how she works) |
| `iris/config.md` | her `config.md`: the owner's words for her alone (one line at first; Loam's text and the owner's shared page load beside it) |
| `iris/map.md` | her `map.md`, the one file of her own that loads on every run: it points at the two below |
| `iris/how-i-am-here.md` | how she is here, and the hello conversation's aims; her map tells her to read it before her first reply |
| `iris/nights.md` | what she does with her nightly wake; her map tells her to read it when she wakes |
| `hello-board.md` | the six things to try on Hello’s board, also available when the person asks for the list back |
| `hello.md` | the hello thread's title (frontmatter) and Iris's first message (the body), which greets the owner by name |

In the text, `{owner}` is the owner's name and `@VAULT@`, `@WORKSPACES@` and
`@SYSTEM@` are this install's vault, workspaces folder and the vault's
system folder, filled in when the starter is seeded.

The first message is posted as Iris but is not a model's turn: its record
says `"preset": true` (docs/starter.md). The person's reply is the first
thing Iris's model sees, with her preset opening marked as Loam's.
