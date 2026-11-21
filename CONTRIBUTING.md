# Contributing to Loam

Loam is developed in this repository. There are two ways a change reaches
`main`.

## The maintainers' install pushes to `main`

Loam is built with Loam: the maintainers run it, and its platform engineer
develops it. Each job tests the proposed merge in its own clone. For a
fast-forward, the job branch tip itself is the candidate. The landing
checks both tips under the project lock before advancing `main` and pushing.
If either tip moves during testing, it builds and tests a fresh candidate.
Repeated races move testing under the lock so a busy project can still land.
Commits made in the job during a test stay on its branch for the next landing.
Maintainer job commits and landing merges use the resolved job identity.
See [Git attribution](docs/attribution.md).

Before any push to this repository, the landing's push gate checks every
commit the repository does not have yet. Its author and committer must
match the resolved identity. The gate scans added lines, paths and commit
messages against the install's deny list of private names. The landing
holds a commit that fails (`push_gate` in `lib/loam_worktree.py`). A hold that
names only the engineer's memory note or a commit's message is not the
work's: the change lands as one commit without them, and the note is kept
beside the job (`land` in the same file).

## Reports from installs

An agent on any Loam install can write up a bug, a feature request or an
idea for its owner with `loam report`. After the owner approves the exact
preview, they can file from their GitHub account, or send via the makers’
bot when a relay is available. Before it is shown to the owner, the report has their name, their agents' names,
paths, their install's address, keys, and e-mail and IP addresses taken out.
A fix that install's engineer made can come with it as a patch
(`loam report bug --patch`). The templates in `.github/ISSUE_TEMPLATE/`
ask the same questions for anyone filing by hand.

## Everyone else opens a pull request

1. Fork the repository, or push a branch if you have write access, and open
   a pull request against `main`.
2. Run `python3 tests/run-all` before you ask for review, and say what you
   ran. A change to the app also runs `npm test` in `app/`. The suite needs
   a Linux box with systemd: `tests/test_schedule.py` starts a user manager
   of its own for its timers (`tests/private_systemd.py`) and never touches
   yours. It runs files in parallel, and each sees only a scratch install
   of its own (`tests/README.md`).
3. Keep the change to one purpose. Say what must be true afterwards and how
   you checked it, the way the engineer's receipts do.
4. Nothing personal goes in: no names, addresses, hosts, keys or captured
   sessions from a real install. Fixtures are synthetic.

A maintainer merges it on GitHub. The maintainers' install fetches `main`
before every landing and fast-forwards to it, so a merged pull request is
the base for the next job there.

## Releases

A release is a tag on `main` equal to the version in `manifest.json`, made
by a landing that asks for one (`release-tag` in the job's directory). An
install updates by moving its checkout to the newest release tag on its
`origin` (`docs/update.md`).

Before requesting the tag, write `docs/releases/<version>.md` and link it
from `CHANGELOG.md`. The Loam landing and `bin/sign-release` check the
commit being tagged for a nonempty `## For your agent` section.

Write this section for the agent who knows the person and decides whether
to tell them. For each noticeable change, say in plain words who it matters
to, what they will see or can do, and how to try it. Give the words they can
say to their agent or the place to click. Mark each change **Major** if it
is worth a message for the people it affects, or **Small** if it is worth
a line only when it affects something they use. Group fixes people will
never notice into one short **Behind the scenes** line. A release with
one real change takes a few lines. Put any contributor detail under
`## Developer details`, which the agent can skip. Use generic descriptions
of the people and agents involved.

For example:

```markdown
# Loam 1.2.3

## For your agent

- **Major · People who use Files to share folders.** Download a whole
  folder as one ZIP. In Files, open the folder's menu and choose Download.
```

Once `docs/releases/signers` holds a key, a release must be an SSH-signed
tag, and installs refuse one that does not verify against the keys they
already hold. The landing signs it when the checkout has a signing key, and
otherwise leaves it for `bin/sign-release` on the machine that holds the
key (`docs/signing.md`).
