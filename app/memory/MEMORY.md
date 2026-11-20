# What this project knows (the app)

Started fresh for the first release, 2026-11-15; the app's earlier history
is not here. Add to this file as you learn by building, for a reader you
have never met.

- **No dependencies, no build.** The app is Node's standard library
  (`package.json` has no dependencies), served from `src/server.js`, with
  the page in `public/`. `npm test` runs `test/` and `tools/*.test.js`.
- **The app holds no authority.** It reaches agents only through the
  session broker's narrow, authenticated interface (`broker/README.md` at
  the repository's root); it never sees a path of the server or a
  credential.
- **Market reviews** arrive in the list. A plugin’s declaration arrives
  separately with its details. The review button can open before that second
  request finishes. A reviewer can use `note.changed` for a documentation
  edit, so its presence alone does not establish wider permissions.
  When checking desktop wrapping, measure the rendered text range. The
  enclosing review button can fit even while a child clips the warning.
- **The domain is the install's.** Nothing in the app names a domain; the
  deploy layer hands the app its origins (`bin/deploy`, `service_env`).
- **It runs on the host, not in a container** (since 2026-11-16): a systemd
  unit with its own uid, a read-only system and a state directory
  (`SPEECH_DIR`, `APP_MEDIA_DIR`). It listens on `APP_HOST`, loopback by
  default. Nothing in the app may assume `/storage`, a container or a
  writable `/tmp` beyond its own.
- **Page previews** start the host's Chromium headless shell through `choom`
  at `APP_PREVIEW_OOM_SCORE_ADJ`, with WebGL drawn by SwiftShader
  (`--use-angle=swiftshader --enable-unsafe-swiftshader`): without those two,
  a WebGL page stalls the screenshot until the capture times out.
- **Themes and page plugins** live in `public/themes/` and
  `public/plugins/` and form the Shipped layer with the runtime's own
  `plugins/` (`docs/plugins.md` at the root).
- **`public/claim.html`** is the claim page: one self-contained file under a
  strict CSP, served by the runtime's claim service before the server has
  an owner (`docs/install.md`).
- **Sun mode gradual shift** lives in `public/sun-shift.js` and is driven by
  `public/theme.js` only on live visible Sun boundaries when
  `prefers-reduced-motion` is not `reduce`: it snapshots both modes' resolved
  `:root` colour tokens via `getComputedStyle`, drifts surfaces in OKLCH across
  the chosen `transition` duration (`fast` 3 s, `normal` 10 s, `long` 20 s),
  flips inks (including paired inverted `-ink` tokens) near the midpoint,
  switches `data-theme` and `theme-color` at `p = 0.5`, and removes all inline
  overrides on completion or manual interruption. `public/themes/*` and non-`public/tokens.css` files in `public/` are tested against literal colour
  patterns by `test/themes.test.js`.
- **Agent faces have no emoji** (retired 2026-11-16): an agent's face is
  always the theme's drawn face (`blob()` / `agentFace()`) or its uploaded
  image (`face`). `sessions.js`, `agent-profile.js` and `agent-drafts.js`
  ignore any `emoji` field on agent/draft records, and the profile face
  control is a `menuSurface` popover (`st.faceMenu` / `.profile-face-menu`)
  anchored to `.profile-face` (exempt from `button-tiers.css` popover
  suppression so **New thread** keeps its primary pill) offering a large face
  preview, `Upload a picture`, and (when a picture is set) `Use the theme’s
  face`. Thread icons (`thread.icon` in `thread-identity.js`) remain a
  separate concept.
- **Files row selection and action hovers** live on `.fs-entry` (`.on` for main,
  `.in-side` for side, with `:has(> .setrow.on)` / `:has(> .row-side-mark)`
  fallbacks so `files.root` plugins reusing `.fs-entry` inherit them): the row
  wrapper paints the surface fill and `.fs-words::after` rule while
  `.fs-entry > .setrow` stays transparent, and `button-tiers.css` excludes
  `.fs-entry *` from its general opaque `--paper-hover` rule (which has
  specificity `(0,6,0)` from `.approve .allow`) so `.fs-rowmenu` and
  `.row-side-action > .iconbtn` share a rounded translucent tint.
  `files.revealOpen(root)` scrolls an off-screen `.fs-entry.on` into view once
  per `(folder, file)` visit.
- **A test that opens a thread must close it.** `createThreads().open()` (and
  `create()`) attaches the live-updates stream through the global `fetch`; when
  that fails it reconnects every 2.5 s (`threads.js`, `connect`'s retry) for as
  long as the thread is open. A test whose assertion throws before
  `threads.close()` leaves that timer behind and its file never exits, even
  with `--test-timeout` (the test is over; the process is not). Close in
  `t.after`, and stub `fetch` with a stream that ends on abort
  (`test/first-day-polish.test.js`, `test/thread-opening.test.js`). Find a
  hang's timer with an `async_hooks` hook on `Timeout` that records the stack.
- **Day-one decisions** (`docs/first-day.md`): the new agent's review is a card
  in the thread's flow; Loam's lines are drawn without ids
  (`formatSystemLine`); a failed turn is a system line (`failedTurnInfo`);
  Files hides the three developer shortcuts until their folders hold more
  than the install put there; Sign-in reads passkeys only from a host relay
  at `/_loam/passkeys` and removes them only on the portal. Any change to the
  broker's words for drafts and first threads must keep `formatSystemLine`'s
  patterns (copied from `bin/session-broker`) in step.
- **Before/after pictures** come from running the same capture script in a
  `git archive` of the commit before (`test/first-day-polish-browser.mjs`):
  the fixture and the scene steps must not depend on the change, so the script
  finds controls by role and text that exist on both sides, or skips a step
  that is not there.
- **Settings › Market** (2026-11-18, `docs/market.md`): a sheet of kind
  `market`, drawn by `page/market.js` and placed by `page/sheets.js`; its
  install sheet is an `app.overlay` so a thread can open it by id. Three
  things a full-window surface has to undo: the rail's foot rises above any
  centred popover (`menu-surface.css`) and showed through the Market until
  `market.css` lowered it there; the shared 44px hit area inflates a picture
  of a button (a swatch's Send pill) unless that pill opts out; and a test
  reading a class straight after a keypress reads the last frame (the page
  redraws on the next one): wait for the class.
- **Browser tests already failing on `main` at 0.2.4** (same first error with
  and without the Market): app-modal, menu-surfaces, plugin-preferences,
  settings-review (cover captions' contrast), sheet-layers, five-themes,
  plugins-polish, plugin-host, theme-options. Compare against a `git archive`
  of `main` before blaming a change.
- **A listing card in an agent's reply** (2026-11-18, `docs/market.md`, "A
  listing card in a thread"): `threads.js` cuts an agent's text at card
  lines (`splitCards`, `public/market-card.js`) and draws them with
  `host.marketCards` between cached prose pieces. The cards must stay out
  of the prose cache (`progressiveProse` keeps a node per text), or a card
  would never turn to Installed. The prose slot for a message with no card
  is unchanged (`index`), so nothing else re-renders.
- **One empty state** (2026-11-18, `docs/empty-states.md`): `emptyState()` in
  `public/empty-state.js` draws every place with nothing to show: the blob,
  one object, a title, one line, at most one action. A size class and an
  object class live on different nodes but share the `empty-state-` prefix,
  so no size may share an object's name (a size called `page` once painted
  the board's page object as a padded box). The sprout plays its motion once
  per appearance only because `morph()` keeps the SVG node; a key or a
  wrapper that changes between redraws would replay it. On a wide screen,
  where the list column and the pane would both be empty (no threads, no
  apps), the pane says it and the list stays quiet.
- **Whose engineer the app has.** Since 2026-11-19 work on the app is the
  Loam engineer's `scope: app` (a brief to `Loam` with `scope: app`): it
  runs in `app/`, a landing refuses any path outside it, `npm test` in
  `app/` is the test, and `deploy` restarts the app's unit and never the
  session broker. Work that needs the broker or the deploy layer too is a
  Loam brief without a scope.

## 2026-11-18 — the Market as a store

- **`button-tiers.css` ties a plain `.market .pillbtn.x:not(.primary):not(.secondary)`
  and wins by coming later.** Its `:is(...)` list takes the specificity of
  its strongest member (`.approve .allow`, two classes), so the tier rule is
  (0,6,0). A Market button with its own paint (glass over a photograph, the
  hairline "Try it first") needs one more class than that, e.g.
  `:root .market .pillbtn.market-glass.market-try:not(.primary):not(.secondary)`.
  Read the computed style in a browser; a screenshot of dark text on a
  photo was the only sign.
- **Literal colours live only in `tokens.css`** (`test/themes.test.js`
  greps every other file for `#hex` and `rgba(`). Paint over a photograph
  from the core caption pair `--theme-cover-ink` / `--theme-cover-shade`,
  with `color-mix(in srgb, var(--theme-cover-ink) 16%, transparent)` for
  glass and gradients.
- **`control-style-guard.mjs` wants every audited control at least 40px
  tall and painted in tokens**; small Market pills are 40px, and the glass
  buttons (the photo exception) are left out of its selector by name.
- **The colour-family check walks up to the first opaque background.** A
  photo frame painted `--theme-cover-shade` is outside the families it
  judges, so white words over it are not read as low contrast against the
  page, and they stay legible while the picture loads.
- **A sticky element cannot outlive its parent.** The phone listing's
  buttons dock at the foot as a second copy (`dock`) in the Market's frame,
  shown once the copy under the title has scrolled above the scroller
  (`watchDock`, on the body's scroll), not as `position: sticky` inside the
  header.
- **In a dock that is always drawn but hidden, selectors find two.**
  `.market-listing-acts` matches the dock's copy on a phone too; tests and
  captures use `.market-listing-head .market-listing-acts`.
- **Files home empty Shortcuts** (2026-11-18): `homeShortcuts()` in
  `public/files.js` renders `.fs-noshort` as a plain muted paragraph aligned
  with `.fs-kicker` (no `.card` surface or icon tile), still gated behind
  `st.roots.loaded && !st.roots.error`.
- **Streaming Zip64 folder archives** (2026-11-18, `src/archive.js`,
  `GET /api/files/archive`): relays `/v1/files/archive` when the broker offers
  it and otherwise streams Zip64 over `/v1/files/list` + `/v1/files/download`,
  excluding `.git`, `.stversions`, secrets, symlinks and `spaces` churn.
  Requires `Remote-User` (`signedIn(req.headers).username`), returning `403`
  (not `401`, which would trigger `auth.js`'s `/up` session-renewal retry
  loop on a browser preflight `&check=1`). In `streamZipArchive()`, abort only
  on `res.on('close')` when `!res.writableFinished` — never `req.on('close')`,
  which fires immediately on a bodyless `GET` in Node HTTP. Playwright's
  `extraHTTPHeaders` is not attached to Chrome DownloadManager requests, so
  `test/files-archive-browser.mjs` puts a local `authProxy` in front of `app`
  to model Caddy's `forward_auth`.
- **First-setup backup step and Settings off state** (2026-11-19):
  `public/onboarding.js` (`createOnboardingBackup`, `welcomeScreen`) adds an
  optional second step ("Back up your Loam") after provider sign-in whenever
  `git-backup` is in the host plugin catalog and not broken, using
  `PATCH /api/plugins/git-backup` and `/x/git-backup/{status,connect,test}`
  with portable repository guidance and a one-click `Skip for now` control
  (no confirmation; turns `git-backup` back off if onboarding turned it on
  with no remote saved). In Settings › Plugins (`public/plugin-settings.js`),
  when `git-backup` is off, its list row and detail switch row plainly say
  "Off-box backup is off"; when `git-backup` is on, Settings is unchanged.
