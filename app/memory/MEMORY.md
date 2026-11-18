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
