# Empty states

*2026-11-18. One component for every place in the app that has nothing to
show: `public/empty-state.js`, styled by `public/empty-state.css`.*

## What it is

Loam's own blob (the same organic family as the agents' faces and the L
mark, drawn wider) filled with a quiet tint of the theme, holding **one
fine-line object**. Under it: a title in the display face, one line of
words, and at most one action. No faces, sparkles or confetti, and nothing
that reads as clip-art.

```js
emptyState({ object, title, body, action, size, level, titleId, bodyClass, props })
```

- `object`: one of `EMPTY_OBJECTS`, listed below.
- `size`: `large` (a whole window: the empty Market), `pane` (a pane or a
  list column; the default), or `small` (a narrow panel: a board, a
  profile's tab, the file picker, a section of a page).
- `level`: the title's heading level (`h2` by default; `h3` inside a panel
  that has its own heading).

**Every paint is a class on a token.** The blob is `--latte-foam` at 55%, a
sheet is `--paper-raised` outlined in `--paper-ink-3`, a line is
`--paper-ink-3`, the one accent is `--latte-espresso`, and the sprout's soil
is `--latte-coffee-2`. No colour is written in the module, so the drawing
fits every theme, light and dark, and follows Sun as it shifts.
`test/empty-state.test.js` fails on a literal colour in either file.

**One touch of motion.** When the empty state first appears, the sprout's
stem draws in and its two leaves unfold. The reconciler keeps the node
across redraws (`morph()` in `public/ui.js`), so the motion plays once each
time the state appears, not on every redraw. No other object moves. Under
`prefers-reduced-motion: reduce` the sprout is drawn whole and nothing
animates (`test/empty-state-browser.mjs`).

## Where it is used, and which object each place has

| Place | Object | Title / line | Action |
| --- | --- | --- | --- |
| The empty Market | sprout (large) | The Market is just opening / Nothing is listed yet… | Ask an agent to build it |
| "Nothing quite right?" at the end of a results or kind page | sprout | Nothing quite right? / Ask one of your agents to build it… | Ask an agent to build it |
| A Market search with no match | search | Nothing quite right for "…"? | Ask an agent to build it |
| No threads (the list on a phone; the pane on a wide screen) | speech | Room for a new thought / Pick an agent above or start a thread. | — |
| No thread open (wide pane) | speech | Good things start here. / Choose a conversation or start a new thread. | — |
| A thread search with no match | search | No threads match / Try another word. | — |
| No archived threads | archive | No archived threads / Threads you archive will appear here. | — |
| An empty board | page (small) | Nothing on this board yet / A short list for this thread. You and the agents can edit it. | the list's own Add, under it |
| Files with nothing open (wide pane) | folder | Pick a file / One on the left. It opens here. | — |
| An empty folder in Files | folder (small) | Nothing here yet / What you or your agents put in this folder shows up here. | — |
| An empty folder in the file picker | folder (small) | Nothing here yet | — |
| No apps (the list on a phone; the pane on a wide screen) | window | No apps yet / An agent can build an app for you. | Build an app |
| An agent's Threads tab, empty | speech (small) | No threads yet / Threads with *name* show up here. | — |
| Schedules, none yet | clock (small) | No schedules yet / Schedule a task to get started. | + Schedule a task, under it |
| Activity, nothing yet | moon (small) | Nothing has happened here yet / What your agents do shows up here. | — |
| Activity, filters match nothing | search (small) | No activity matches these filters / Try another agent or kind. | — |
| Settings › Agents › Archived, none | archive | No archived agents / Agents you archive wait here, ready to restore. | — |

**On a wide screen, one place says it once.** With no threads at all, or no
apps, the list column and the pane would show the same empty state side by
side. The pane says it (with the action, for apps), and the list column
stays quiet.

## Where a line of text stays instead

The component is for a place whose whole content is missing: a page, a
pane, a panel, a tab or a list. A status line inside a page that has other
content stays a line of text, because a drawing there would be clutter:

- **Nothing running**: Activity's *Now* line, Settings' Activity row, and a
  thread's tasks popover, which closes itself.
- Values inside a populated page: *No run yet*, *No key yet*, *No next
  run*, *Declares no network, record or memory access*.
- An agent profile's overview groups (*No schedules yet.* and *No recent
  activity.* beside the other groups).
- Settings › Agents with no agents, and the agent strip's *No agents yet.*:
  a group under the New agent button, beside the System group, and a case a
  working install does not reach.
- Loading and error states (*Opening chats…*, *Loading…*, *could not be
  loaded*). These are not empty states.
- A new thread's opening (*A new thread with Sprout*) keeps the thread's
  own face, which is who you are talking to, not a decoration.

## What checks it

- `test/empty-state.test.js`: every object renders in the same blob, painted
  only by classes; the words in order, with one action; no literal colour in
  either file; only the sprout animates, and reduced motion stops it; the
  Market's old drawing is gone.
- `test/empty-state-browser.mjs`: in a real browser, the sprout's stem and
  leaves animate and nothing else does; under reduced motion, in every
  shipped theme, light and dark, nothing animates, the stem is drawn whole,
  the leaves are open, and every paint resolves to its token. The empty
  Market draws it with its words and action.
- `tools/capture-empty-states.mjs`: captures at 390 and 1440, light and
  dark, in Latte and Autumn, of every place above that the fixture can
  reach, and a specimen sheet of every object (`EMPTY_THEMES=all`: in every
  shipped theme).
