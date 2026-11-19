# What a new person meets on day one

A fresh install's first day was walked end to end on 2026-11-16, and its
polish list became ten changes to the page (2026-11-17). Each is a decision
about what a person reads; this file says what was decided and where it lives,
so the next change keeps it. `test/first-day-polish.test.js` holds one test
per item, and `test/first-day-polish-browser.mjs` takes the pictures (every
item at 390 and 1440, light and dark; run it from the commit before for the
"before" set).

## A new agent is a card in the thread (`agent-drafts.js`)

When an agent drafts a new agent, the review is a card in the conversation,
after the line that says the draft was made, which is where the agent says it
is ("below"). It shows the new agent's face (the theme's face for its id, as
it will have once made), its name, and the first two sentences of its soul.
"Read the whole thing" opens the soul ("Who they are") and the config ("How
they work") rendered as markdown, never raw. **Create** is the primary action
and **Ask for changes** (which puts the cursor in the composer) the secondary.
While the person is writing, or on an older draft when there are two, Create
steps down to the secondary tier (`button-tiers.css`). A revision number is
never shown on a first draft; from the second it is a quiet "2nd draft". Once
made, the card becomes "Juniper is ready." with **Say hello to Juniper**,
which opens their first thread.

## Loam's lines read as words (`formatSystemLine`, `thread-experience.js`)

The broker writes its lines for the agents who read them, with ids in them:
"Iris drafted a new agent: Juniper (draft d-…, revision 1)", "Made Juniper
(juniper): their first thread is t-…", "(m12 in t-…)", "this thread is
`t-…`", and the starter line's instruction to Iris. The page draws them
without the ids: "Iris drafted a new agent: Juniper", "Iris made a 2nd draft
of Juniper", "Made Juniper." and a link to their first thread by its name
("Say hello in their first thread" while it has none of its own). The record
keeps the full words; only the drawing changes. A broker that stops writing
ids makes this a no-op.

## A failed turn is a system line (`failedTurnInfo`, `threads.js`)

A turn that ends in the provider's error is not the agent speaking. Claude
Code records it as its own stand-in reply (`model: "<synthetic>"`, the raw
error as its text); the engine records "<name>'s turn failed: …". Either is
drawn as a system line that says what happened and what to do, for example
"Iris couldn't reach Claude. Sign in again in Settings › Connections." with **Go
to Providers**; a usage limit says the provider has paused replies; anything
else says the turn didn't finish and to send again. The raw error is behind
**Details**. A real reply that only mentions an error is left alone: it has
to be the stand-in, or an error result with nothing done.

## Dictation without a key (`plugins/dictation/page/engine.js`)

When the app's server answers a take with 503 "Dictation needs a Gemini API
key…" (`src/transcribe.js`), the quiet line says "Dictation needs a Gemini
API key." with **Add a key**, which opens Settings › Plugins › Dictation,
where the key goes. It is not offered as a take to retry. With no dictation
terms note (the broker's 404) the terms card says "No names and terms note
yet…" instead of "not connected on this app".

## The update log (`update.js`)

A finished update shows its outcome and the versions ("0.1.0 → 0.1.1"); its
steps, the backup's path and the memory-pin counts are behind **Details**.
While it runs, the steps show and only their paths and counts are folded.

## Sign out and passkeys (`passkey.js`, Settings › Account)

**Sign out** goes to the login portal's `/logout` (`auth.` in front of this
host, `deploy/login/README.md`), which signs this browser out and says so.
The passkeys themselves are the portal's: Authelia's
`/api/secondfactor/webauthn/credentials`, on the portal's origin, which the
page cannot read. The page reads them from **`GET /_loam/passkeys`** on its
own host when the host relays it, beside `/_loam/login-code`:
`{"passkeys": [{"id", "description", "created_at", "last_used_at"}]}`, the
fields Authelia answers. With it, the card is "Your passkeys", each with its
name, when it was added and last used, and **Remove**; without it (404), the
card offers **See or remove your passkeys**. Removing always happens on the
portal's settings page, which asks the person to confirm who they are first;
the page never removes a passkey itself.

## Settings › Agents

The person's own agents come first. The engineers (one row per project and
"Every other project") and the reviewer sit together below in a quieter
**System** group, still each a row that opens its settings.

## The claim page (`claim.html`)

"At least 10 characters" is under the password before anything is typed
(`aria-describedby`), the rule the claim service enforces (`bin/loam-claim`,
`MIN_PASSWORD`).

## Files shortcuts (`files.js`, `loadRoots`)

The host offers "Vault system", "Loam project" and "Loam infrastructure"
wherever those folders exist, and every install has them: a fresh vault's
System holds only Loam's library and the empty `skills` shelf (`bin/library
seed-vault`), and Projects/Loam only the schedule page. Each of the three is
shown only once its folder holds something beyond those; an install that
works in them keeps every one, and a shortcut the person pinned themselves
always shows. The folders are looked into before the shortcuts are drawn, so
nothing appears and then moves.

## Build an app (`apps.js`, `buildAppThread`)

"Build an app" (the Apps page with no apps) opens a thread with Iris in it,
or the first agent awake when there is no Iris, titled "Build an app with
Iris" with a line on what to say and the composer asking "What would you like
to build?". Nothing is sent until the person writes; the thread is named by
its first message, as any other.
