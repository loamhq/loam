# The starter: Iris and the hello thread

*Since 2026-11-14 (first release, L11; the owner's decision 4). The code is
`lib/loam_starter.py`; the command is `bin/library seed-starter`; the tests
are `tests/test_starter.py`; the words are in `docs/starter/`
(`docs/starter/README.md` says which file holds which). The words are the
owner's approved starter of 2026-11-16, which replaced L11's placeholders.*

## What a blank install gets

One agent, Iris, and one thread with her, the hello thread. The installer
runs, in order, `bin/library seed-vault` (docs/skills.md) and
`bin/library seed-starter`.

**About me.** `seed-vault` also writes `About me.md` at the top of the
vault: a heading and one line saying it is what the person's agents know
about them, theirs to read and edit (the owner, 2026-11-16). From 2026-11-18
to 2026-11-19 every agent in the map's shape was told of it by Loam's text
(`docs/persona/loam.md`); the owner's shorter text of 2026-11-19 does not
name it, and the library's `loam-how-it-works` does. Iris's `nights.md` keeps it.
Until 2026-11-18 a new agent was given an `_always.md` with that line
instead; an agent made then keeps it. After that the note is
the person's and the agents': Iris fills it in during the hello conversation
and keeps it accurate on her nightly wake.

`seed-starter` makes Iris from `docs/starter/iris/` through
`loam_agents.make`, the same step L10's create route ends in: her workspace,
her soul and config, the two loading files, `agent.json`, and provisioning on
every provider. On a box with other agents her
config takes the fleet's clauses as any new agent's does; on a blank one
there are none, and the starter's config is what she has. From then on she
is an ordinary agent: the person can change her, rename her or archive her.

Then it writes the hello thread: a thread with Iris as its default and only
member, pinned, with `hello: true` in its frontmatter, and one message, the
body of `docs/starter/hello.md`, posted as Iris and marked preset.

**Once per install.** A marker, `<state>/agents/starter.json` (the agent,
the thread, the message, when), makes every later run a no-op, so a person
who archived Iris or deleted the thread is not handed another. And it does
nothing at all where a workspace of the starter's id exists: an agent of
that name is somebody's, and its soul is never written here. That is why
running it on a box that already has an Iris, as the first install does, changes
nothing (`tests/test_starter.py`).

## A preset message

The first message is the runtime's words in Iris's name, not a model's
turn: no session, no engine, no model, no cost. Its record says so:

```
### Iris · 2026-11-15 04:27
<!-- loam {"id": "m1", "author": "iris", "at": "…", "kind": "message", "preset": true} -->
```

When the person replies, Iris's first turn starts a fresh session as every
first turn does, and the recent exchange it is handed shows her opening
with `(preset: Loam posted this opening in your name before your first
turn; carry on from it as yours)` after her name (`loam_threads.transcript`).
From that reply on, the conversation is the model's. What she does in it is
in her `how-i-am-here.md`, "The hello conversation", which her map tells her
to read before her first reply: aims and leans, not a script.
By the end she knows who the person is (their name, where they live, their
age, and whatever they choose to tell her, kept in `About me.md`); they know
they can come to her or any agent about Loam; they have a rough sense of
what Loam is; and they have their first agent, drafted as a character
through the library skill `loam-making-an-agent` and made on their yes.
If they mention an AI they used before, she tells them once that they can
bring its context over (a prompt to paste, or the service's export), an
offer and never a step: the library skill `loam-bringing-context-from-another-ai`
(2026-11-17).

## The page's contract (for U4)

- **The hello thread** is the row of `GET /v1/threads` with `"hello": true`
  (every other row says `false`). It is pinned. Land the person there the
  first time, after sign-in and provider sign-in. It is an ordinary thread
  in every other way; once the person has written in it, nothing marks it.
- **A preset message** is a message of `GET /v1/threads/<id>` with
  `"preset": true`; every other message has no `preset` key. It has no
  `engine`, no `model` and no "What I did" steps. Draw it as the agent's
  message; if the page shows anything to tell it apart, it is that it was
  written for the agent, never a model's name or a cost.
- No route makes a preset message. Only `seed-starter` writes one.

## Proved

On a scratch install root (2026-11-14): a blank config, `seed-vault`, then
`seed-starter` with the real `bin/provision`. The scratch processes had no
connection to the user's systemd manager, so nothing on the box could be
started or stopped. Then a broker of the scratch install on a loopback port:
`GET /v1/threads` listed `Hello` with `hello: true`, its `m1` by Iris with
`preset: true`; the person's `Hi Iris, I am Sam.` was answered by Iris on
Claude Code (`claude-fable-5-2`): "Good to meet you, Sam. A couple of small
things so I know who I'm talking with. First: how old are you?"

## Not covered here

- Landing the person in the thread, and anything the page draws: U4.
- The installer that runs these two commands is L12's (docs/install.md).
  It writes the blank install's `<workspaces>/_shared/reach.md`, which every
  persona's `CLAUDE.md` imports (bin/provision `REACH`), and re-runs
  `seed-starter` until it has finished: since L12 a failed make is set aside
  and made again by the next run, with no hand clearing (docs/install.md,
  "A failed starter").
