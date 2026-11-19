# Whose name is on agents' work

Since 2026-11-18. The code is `lib/loam_git_identity.py`; the test is
`tests/test_git_identity.py`.

Work your agents commit carries **your** name, not the name of the provider
the agent ran on. Claude Code, Codex and Antigravity do not add themselves as
co-authors.

## Setting it

Settings › GitHub › **Your name on agents' work**, or ask a persona. Both use
the same route, `PATCH /v1/settings/git`. The card has one choice of three,
**Default**, **Type a name** and **Neutral**, and under Default, with GitHub
connected, one box: **Also open pull requests as me**.

    settings set git.identity null        # Default (how every install starts)
    settings set git.identity '{"name": "Ada Lovelace", "email": "ada@example.org"}'
    settings set git.identity neutral     # always "<project> engineer", no note
    settings set git.identity github      # the box: Default, and pull requests as you

### The default

Unset is the default, on a fresh install and on one that existed before
(the owner, 2026-11-18: "the same is true for other Loam users as well
unless they change it"). At each job it is, in order:

1. **Your connected GitHub account**, when the connection says clearly which
   account that is: every authorized connection on this install (the Loam
   app's, and each organization app's) is signed in as the same login. Its
   profile name and its no-reply address,
   `<id>+<login>@users.noreply.github.com`. The id and name are read with
   GitHub's `GET /user`, using the token the connection already has, and
   kept on that authorization for a day (`profile` in
   `state/github/integration.json`, never in its public view); a failed read
   keeps the last one. A connection added later is used at the next job; a
   removed one is not.
2. **With nothing connected, your computer's own git identity**
   (`git config --global`), only when its address is already a GitHub
   no-reply address. A private address there is never used: Loam does not
   publish an address you have not already published.
3. **Otherwise the engineer's name**, `<project> engineer`, and the job's
   receipt and the Settings card say why and what to do: connect GitHub when
   nothing is connected; choose (type a name) when two different GitHub
   accounts are connected, because Loam never picks one; check the
   connection when GitHub could not be asked.

Loam never signs in for this, never adds a scope and never switches
accounts.

### The explicit choices

- `github` reads the connected GitHub account once, using the token the
  connection already has; no scope is added. It takes the account's profile
  name and GitHub's private no-reply address for it,
  `<id>+<login>@users.noreply.github.com`. GitHub credits commits with that
  address to the account, and your real address never appears in them.
- A typed name and address are used exactly as given. A provider's address
  is refused.
- `neutral` is the opt-out: commits carry the engineer's identity,
  `<project> engineer <engineer+<project>@noreply.local>`, and no note asks
  for more.
- `github` is a snapshot of the account as it was when chosen, and it is the
  only setting that lets the owner's own token open a pull request (below).

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
3. **The owner's setting**, or with nothing set the default, described
   above.
4. **The engineer's identity**: with the prompt, unless the owner chose
   `neutral`.

So a project or repository override, and the gated public Loam checkout's
neutral `Loam engineer`, are never replaced by the default.

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
Author metadata never stands in for a GitHub actor. **The default is
metadata only**: it puts your name and no-reply address on the commits, and
does not authorize anything or change who pushes or opens a pull request.
Only the explicit `github` choice (the box on the card) lets your own token
open one.

A landing to `main` pushes with whatever credential the checkout's remote
uses (a deploy key here). GitHub shows a deploy-key push as the key, not as
a person.

## Outside Loam's control

- What GitHub shows for an address depends on that address being verified
  on the account, or being the account's no-reply address.
- Commits you or anyone else make by hand, outside a job's clone, and any
  git you run yourself (a terminal, an IDE, GitHub's web editor): those use
  your own git configuration and credentials, not this setting.
- Who GitHub shows as the pusher or the pull request's author: that is the
  credential that made the call (a deploy key, the Loam app, or your token
  when you chose `github`), never the commits' metadata.
- Remotes Loam does not push to.
- A provider that changes how it attributes itself. The hook and the
  landing are the backstop for that.
- The box's own 15-minute autocommits of the vault, workspaces and plugins
  (`git.autocommit` in `docs/config.md`). These are the box's, not an
  agent's, and are unchanged.
