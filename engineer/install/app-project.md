# Project file: Loam's app — the Loam engineer, app-only

*What the Loam engineer owns when a brief gives it `scope: app`, and what it cannot touch then. It sits beside the role file, which says how every engineer works. Loam ships it (`engineer/install/app-project.md`) and `bin/provision --engineer` renders it into `app/project.md`; it is never committed there. Until 2026-11-19 the app had an engineer of its own; it is now this scope of the one Loam engineer, with the same safeguards.*

## Your project

The app is Loam's web app: the page the owner uses, on a phone or a laptop, to talk to the agents on their machine (Chats, Files, Apps, Activity and Settings, plus plugin slots), answering `/api/*` from the session broker. It is Node's standard library with no dependency and no build step: `src/` is the server, `public/` the page.

**In this scope the `app/` folder of Loam's repository is your whole project.** Your session starts in `app/` of your job's tree, which is a clone of Loam's checkout. Everything outside `app/` is the platform's: the broker, the deploy layer, the units, the tests in `tests/`, the docs at the root. You may read all of it — the broker's contract (`broker/README.md`) and the deploy layer (`deploy/README.md`, "Loam's own app") are the app's two neighbours — and you change none of it in this scope. **A landing refuses a branch that changes a path outside `app/`**, names the paths, and sends the job back to take them out. A change the app needs from the platform (a broker route, a deploy value) is a sentence in your receipt for whoever briefed you, who sends it as a Loam brief without a scope.

The job runs without sudo and without the account's credentials: its unit carries `NoNewPrivileges`. Anything that needs either is not app-only work.

## Landing and going live

- **Your branch lands on Loam's `main`**, tested with `npm test` in `app/` on the branch and on the merge (and is pushed only when this checkout tracks a remote). Commit as the identity your tree already has; never change it.
- **Loam's history is public-ready and its push is gated.** A commit may not name this install: no person's name, no agent's name, no domain, host, path or address of this machine, in the code, a test fixture, a doc or the commit message. Tests use `example.com` and the owner placeholder (`setOwner({ id: 'you', name: 'Sam', ... })`), as the existing ones do. A commit that names one is held at the push, and nothing after it reaches the public repository until it is rewritten.
- **Live is `loam-app@loam-ui.service`**, run from a release of Loam's checkout at a landed commit; staging, where configured, is `loam-app@loam-ui-next.service` at `next.<domain>`. A brief with `deploy: true` puts your landed commit live (`bin/deploy loam-ui up <sha> --forward-only`), which restarts the app's unit and never the session broker; staging, and a deploy later, are `bin/deploy loam-ui-next up <sha>` or a Loam brief titled `Deploy loam-ui`. You never run either in this scope.
- **What the app is handed is the platform's**: its environment is `bin/deploy`'s `service_env` (the deploy README's table says what each variable means). A new variable the app reads needs a default that keeps it working without it, and a sentence in your receipt asking the platform to hand it over.

## How it runs, so the app never assumes otherwise

A systemd unit with its own uid and nothing of the box: the file system read-only but its state directory (`APP_MEDIA_DIR`), no `/home`, its own `/tmp`, a loopback listener (`APP_HOST`). The page previews' Chromium is the host's `chromium-headless-shell` when the install has one. There is no container, no `/storage` volume and no Dockerfile.

## Build, run, test

- `npm test` in `app/` runs the suite; a receipt that says the tests passed quotes it and its output. The browser checks run when `APP_PREVIEW_CHROME` names a Chromium.
- `npm start` runs the server; the README says the rest. Run anything you start on a port of your own, never the live app's or staging's, and stop it before you finish.

## Quality bar for the page

Keep the page simple, uncluttered, intentional, intuitive and easy to understand. A change to what the page looks like comes with before-and-after captures at a phone and a desktop width, light and dark.

## What you must leave alone

- **Everything outside `app/`**, as above; the landing enforces it.
- **`app/CLAUDE.md`, `app/role.md`, `app/project.md` and `app/.claude/`**, which are rendered for you and never committed. `app/memory/MEMORY.md` is the app's memory and is committed with your work; write it for a contributor you have never met, about the app and never about this install.
- **Credentials of any kind**, in the code, a test, a doc or a commit.
- **Anything public.** The app is behind the login; making anything reachable without it is the owner's act alone.
- **The agents' own files and the owner's notes**, which are what the app shows, never what this engineer edits.
