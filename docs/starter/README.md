# The starter: Iris and the hello thread

A blank Loam install ships one agent, Iris, and one thread: the hello
thread, where the person lands after signing in. Everything here is copied
into the install once, by `bin/library seed-starter` (lib/loam_starter.py),
and from then on belongs to the person: Iris is an ordinary agent they can
change, rename or archive, and nothing here is copied again. She is born in
the map's shape (2026-11-18): soul, config and map load on every run, and no
`_always.md`, `_active.md` or `_reflection.md` is made.

The words are the owner's approved starter (2026-11-16), which replaced the
placeholders of 2026-11-14. The source is the design note "Starter Iris: soul
and hello". The hello greeting and guidance were revised on 2026-11-19.

| File | What it becomes |
|---|---|
| `iris/agent.json` | Iris's id and name (her face is the theme's until the person uploads one) |
| `iris/soul.md` | her `soul.md`: who she is (character, values and identity, nothing about how she works) |
| `iris/config.md` | her `config.md`: the owner's words for her alone (one line at first; Loam's text and the owner's shared page load beside it) |
| `iris/map.md` | her `map.md`, the one file of her own that loads on every run: it points at the two below |
| `iris/how-i-am-here.md` | how she is here, and the hello conversation's aims; her map tells her to read it before her first reply |
| `iris/nights.md` | what she does with her nightly wake; her map tells her to read it when she wakes |
| `hello.md` | the hello thread's title (frontmatter) and Iris's first message (the body), which greets the owner by name |

In the text, `{owner}` is the owner's name and `@VAULT@`, `@WORKSPACES@` and
`@SYSTEM@` are this install's vault, workspaces folder and the vault's
system folder, filled in when the starter is seeded.

The first message is posted as Iris but is not a model's turn: its record
says `"preset": true` (docs/starter.md). The person's reply is the first
thing Iris's model sees, with her preset opening marked as Loam's.
