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
