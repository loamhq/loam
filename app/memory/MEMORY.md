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
  deploy layer hands the app its origins (`bin/deploy`, `origins_env`).
- **Themes and page plugins** live in `public/themes/` and
  `public/plugins/` and form the Shipped layer with the runtime's own
  `plugins/` (`docs/plugins.md` at the root).
- **`public/claim.html`** is the claim page: one self-contained file under a
  strict CSP, served by the runtime's claim service before the server has
  an owner (`docs/install.md`).
