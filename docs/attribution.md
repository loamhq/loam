# Whose name is on agents' work

Since 2026-11-18. The code is `lib/loam_git_identity.py`; the test is
`tests/test_git_identity.py`.

Work your agents commit carries **your** name, not the name of the provider
the agent ran on. Claude Code, Codex and Antigravity do not add themselves as
co-authors.

## Setting it

Settings › GitHub › **Your name on agents' work**, or ask a persona. Both use
the same route, `PATCH /v1/settings/git`:

    settings set git.identity github
    settings set git.identity '{"name": "Ada Lovelace", "email": "ada@example.org"}'
    settings set git.identity null

- `github` reads the connected GitHub account once, using the token the
  connection already has; no scope is added. It takes the account's profile
  name and GitHub's private no-reply address for it,
  `<id>+<login>@users.noreply.github.com`. GitHub credits commits with that
  address to the account, and your real address never appears in them.
- A typed name and address are used exactly as given. A provider's address
  is refused.
- Unset, which is how a fresh install starts: commits carry the engineer's
  identity, `<project> engineer <engineer+<project>@noreply.local>`, never a
  provider's. Each job's receipt says how to set yours.

Loam never guesses an address, and it has no built-in person.

## Which name a job commits as

The first rule that applies wins:

1. **The project's person.** A project record's `commit_identity`
   (`lib/loam_commit_identity.py`) is for a repository shared with other
   people. It has stricter rules of its own.
2. **The repository's own identity.** This is the canonical checkout's
   `user.name` and `user.email` when someone set them on purpose: anything
   other than the default that `bin/provision` writes. A checkout with the
   push gate on (`loam.pushGate`) always keeps its own identity, because the
   gate checks commits against it.
3. **The owner's setting**, described above.
4. **The engineer's identity**, with the prompt.

Both author and committer get the chosen identity. So does the landing's
merge commit, which is made in the job's tree.

## Provider attribution

- **Claude Code:** `attribution` is set to empty, and `includeCoAuthoredBy`
  to false, both for every engineer job (`--settings`) and in the user
  settings the personas share (`lib/loam_provider_memory.py`).
- **Codex 0.170:** has no local setting for this. It adds
  `Co-authored-by: Codex` when a workspace policy fetched from OpenAI says
  so (`commit_attribution` is refused by `--strict-config`). On the account
  checked on 2026-11-18, a real `codex exec` commit carried no trailer.
- **Antigravity 1.5:** the binary contains no co-author text.
- **Every job's clone** gets a `commit-msg` hook that takes a provider's
  lines out of each message as the commit is made.
- **The landing** re-makes the job's own commits that an agent made, giving
  them the chosen identity and removing provider lines from their messages.
  It does this only for commits in `main..branch` that are on no remote. It
  catches commits made with `--no-verify`, and trees from before the setting
  existed.

"A provider's lines" means only:

- a `Co-authored-by` trailer whose address belongs to a provider
  (`noreply@anthropic.com`, `noreply@openai.com`, a provider bot's GitHub
  no-reply address);
- a `Claude-Session` trailer;
- a "Generated with Claude Code / Codex" line.

A person's co-author trailer, a person named Claude, `Signed-off-by` and
every other line are kept.

## What is never changed

- Nothing already on a remote is rewritten, and nothing is force-pushed.
  History published before this change keeps its names and trailers.
- A commit that someone other than the agent made or committed in a job's
  branch is kept byte for byte, its own trailers included.
- A signed commit is kept as made, and the receipt names it. Re-making it
  would drop the signature.
- A pull-request project's branch is not re-made at landing, because its
  head may already be on the remote. Its defence is the tree's identity,
  the harness settings and the hook.

## Who pushes and opens a pull request

- **A person-authored project:** that person's token, after the commits are
  checked (unchanged).
- **Otherwise, when the owner chose `github`:** the owner's own connection,
  but only if GitHub's `/user` names the same login for the token that
  reaches the repository. If it does not, the app's installation opens the
  pull request, as before. The result's `actor` says which one did.

Loam never signs in again, never switches accounts and never adds a scope.
Author metadata never stands in for a GitHub actor.

A landing to `main` pushes with whatever credential the checkout's remote
uses (a deploy key here). GitHub shows a deploy-key push as the key, not as
a person.

## Outside Loam's control

- What GitHub shows for an address depends on that address being verified
  on the account, or being the account's no-reply address.
- Commits you or anyone else make by hand, outside a job's clone.
- Remotes Loam does not push to.
- A provider that changes how it attributes itself. The hook and the
  landing are the backstop for that.
- The box's own 15-minute autocommits of the vault, workspaces and plugins
  (`git.autocommit` in `docs/config.md`). These are the box's, not an
  agent's, and are unchanged.
