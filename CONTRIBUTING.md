# Contributing to Loam

Loam is developed in this repository. There are two ways a change reaches
`main`.

## The maintainers' install pushes to `main`

Loam is built with Loam: the maintainers run it, and its platform engineer
develops it. Each of that engineer's jobs lands on the install's own
checkout (tests run on the branch and on the merge; see
`lib/loam_worktree.py`), and the landing pushes `main` here. Those commits
carry the engineer's own identity, never a person's.

Before any push to this repository, the landing's push gate checks every
commit the repository does not have yet: it must carry that identity, and
nothing it adds, no path it touches and nothing in its message may match the
install's deny list of private names. A commit that fails is held on the
install, not pushed (`push_gate` in `lib/loam_worktree.py`).

## Reports from installs

An agent on any Loam install can write up a bug or a feature request for
its owner and, on their yes, hand them a link that files it here as an issue
from their own GitHub account (`loam report`, lib/loam_feedback.py). Before
it is shown to the owner, the report has their name, their agents' names,
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
   yours.
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
