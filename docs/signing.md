# Signed releases

*Added 2026-11-19. The code: `lib/loam_signing.py` (the check),
`lib/loam_update.py` (the updater uses it), `install.sh` (its own copy of
the check), `cut_release` in `lib/loam_worktree.py` (the release step),
`bin/sign-release` (signing from the machine that holds the key). Tests:
`tests/test_signing.py`.*

An install of Loam runs as root at the start and updates itself, so whoever
can publish a release runs code on every install that updates. Before this,
anyone who could push a tag to Loam's repository could do that. Now a
release must be signed with a maintainer's key, and an install checks the
signature against keys it already holds before it runs anything from the
release.

## What a release is

A release is a git tag named for its version (`0.3.0`), on `main`, equal to
the `version` in `manifest.json` (CONTRIBUTING.md). Signed, it is an
annotated tag carrying an SSH signature (`git tag -s` with
`gpg.format=ssh`). The signature covers the tag object, which names the
commit, so it covers the whole tree of that commit.

**The keys** are in `docs/releases/signers`, in OpenSSH's `allowed_signers`
format:

```
loam-release namespaces="git" ssh-ed25519 AAAA... a comment
```

Checking needs `git` and `ssh-keygen` (OpenSSH 8.1 or newer), which every
Linux server and Mac has. It needs no network, no keyserver and no GnuPG.

## What an install checks

**An update** (`loam update`, Settings › About Loam) fetches the newer tag,
then checks it before anything else is read from it:

- the tag must be a tag object named for that release, signed with an SSH
  key;
- the key must be in `docs/releases/signers` **as the running release
  committed it**. The incoming release's own copy is never used, so a
  release cannot bring the key it was signed with;
- the signature must match the tag object.

If any of these fails, the update is refused with the code `signature`,
before the backup, before the move, before anything else. Nothing is half
applied. The refusal shows in three places:

- Settings › About Loam: "Loam 0.3.0 could not be verified", with the
  reason, and no update button;
- a line in General, and a turn for the owner's default agent, telling it not
  to work around it and to tell the owner plainly;
- `loam update` and `loam update status` at a terminal.

If the release verifies, the update moves to the commit the verified tag
object names, not to the tag's name again (a check from the page while an
update runs fetches the tag again, and could re-point it).

`loam update status` and the Settings page say which state the install is
in: "Releases are checked against N maintainer key(s) before they run", or
"Releases are not checked: the running release carries no maintainer key".

**A fresh install** (`install.sh`) clones the release it chose and checks
it the same way, in shell, against the keys written into `install.sh`
itself (between its `RELEASE-SIGNERS` markers). It checks before anything
from the clone runs, and checks again on a re-run that reuses the clone. If
the check fails, the clone is removed, nothing on the server was changed,
and the installer stops with exit code 3 and the reason. A test keeps the
installer's copy of the keys equal to `docs/releases/signers`.

## What a first install still trusts

The first script has to come from somewhere. `install.sh` is trusted as it
reached the server: whatever `https://raw.githubusercontent.com/loamhq/loam/main/install.sh`
served, over TLS. Everything after the script is checked against the keys
that script carries. So the remaining trust for a fresh install is:

- GitHub, and TLS to it, to serve the script as it is in the repository;
- whoever can push to the repository's `main`, because they can change the
  script and the keys in it.

The signature still matters for a fresh install: a release tag that is moved
or replaced, a different `--source`, or a tampered clone is refused. What it
cannot catch is a changed `install.sh`.

**For a careful first install**, check the script before running it. Get
the maintainer's key fingerprint from somewhere other than the repository
(where it is published is decided with the key, below), then:

```sh
git clone --branch 0.3.0 https://github.com/loamhq/loam.git && cd loam
grep -v '^#' docs/releases/signers | cut -d' ' -f3- | ssh-keygen -lf -   # compare the fingerprint
git -c gpg.ssh.allowedSignersFile=docs/releases/signers verify-tag 0.3.0
sudo bash install.sh
```

**Third-party installers.** The install also runs installers that are not
Loam's: `uv`'s, Antigravity's, the provider CLIs at the versions the release
names, and the Debian, Docker, Caddy and Node packages. Those are trusted as
their own publishers serve them, and a Loam signature does not cover them.

## The first signed release, and boxes already installed

Every release before the first one that carries a key in
`docs/releases/signers` is unsigned, and so is checked by nothing. An install
on such a release (every install today, this one included) updates to the
first keyed release the way every update worked before: it trusts TLS to
GitHub and whoever can push a tag there. That one hop is the only unchecked
one. From the keyed release on, the install holds the key, and every later
update is checked against it. The owner does nothing for this: it is an
ordinary update.

`ssh-keygen` comes with `openssh-client`, which every server reached over
SSH already has. The installer installs it if it is missing. An install
without it refuses a signed update (reason `cannot-check`), and never skips
the check.

## Cutting a release

The release step stays what it was: a landing that asks for a tag
(`release-tag`, CONTRIBUTING.md). When the landed tree or the previous
release carries a key, the landing signs the tag as part of that step:

- **This checkout has a signing key** (`git config user.signingKey`, an SSH
  key, private on disk, or public with the private half in an ssh-agent or
  on a hardware key): the landing makes the signed tag, checks it against
  the previous release's keys and the new tree's, and pushes it. If either
  check fails, it deletes the tag and pushes nothing.
- **It has none**: `main` lands and is pushed, no tag is made, and the
  receipt says "Release 0.3.0 is waiting to be signed" with the command
  that signs it. On the machine that holds the key, in any clone of the
  repository:

  ```sh
  bin/sign-release 0.3.0 <commit> --key ~/.ssh/loam_release.pub
  ```

  It checks the commit is on `main` and its manifest says `0.3.0`, shows
  what it is about to sign, and asks. On a yes it signs, checks the
  signature as the installs will, and pushes the tag.

A project whose tree has no `docs/releases/signers` tags as it always did.

## Rotating a key, and losing one

**Rotation.** A release signed with the old key adds the new key to
`docs/releases/signers` (and to `install.sh`'s copy). Every install accepts
it, because it holds the old key. From then on the installs hold both, and
the next release can be signed with the new key. A later release, signed
with the new key, drops the old one. A key can also be given an end date in
the file (`valid-before="20270101"`).

**A lost key, with a second key.** Keep two keys in the file from the start,
for example a hardware key and its backup, kept apart. Losing one is then a
rotation: the other signs a release that removes the lost one.

**A lost key, with no second key.** No release can reach the installs by
the update. Each owner has to give their install the new key once by hand,
after checking it somewhere they trust (`docs/releases/signers` in a commit
of their own on the install, or a re-install). Loam's makers would say so on
the front page and in an issue. That is costly, and the
reason to keep two keys.

**A stolen key.** Rotate with the other key immediately, in a release that
drops the stolen key. Installs that update are then safe. Until they update,
a release signed with the stolen key would still be accepted by them.

## Where the private key lives

*Not decided: the maintainers' choice.* Everything above works the same
whatever they choose, except the GitHub option, whose check is different.
Every agent on the maintainers' server is root there, so a key on that
server can be used by any agent that has been fooled.

| Where | What it protects against | Each release costs the person holding it |
|---|---|---|
| A key on the person's own computer, with a passphrase | a fooled agent or a leaked push credential on the server; GitHub | one command (`bin/sign-release`), and the passphrase, about a minute |
| A hardware key (an `ed25519-sk` SSH key on a security key), plus a second as backup | the same, and also a compromised computer (the key cannot be copied off it) | the same command, and a touch of the key |
| GitHub's artifact attestations / keyless signing from a release workflow | a release made outside the repository's workflow; **not** whoever can push to the repository, who can push a tag and change the workflow, unless a protected environment and tag rules keep the server's credential out | a click to approve the workflow run, with the protected environment; installs would need GitHub's CLI or cosign to check, and a different check |
| A key on a separate small machine (a home device, a tiny VPS) that signs on the person's approval | the same as the first row, if the approval cannot come from the server | a tap on an approval prompt, once the signing machine is built and kept up (new code; not built) |
| A key on the maintainers' server | GitHub, and a push credential leaked elsewhere; **not** a fooled agent on that server | nothing: the landing signs |

**Recommended:** a hardware key and its backup, both in
`docs/releases/signers`, with signing done by `bin/sign-release` on the
maintainer's own computer. It is the only option that stops an agent on the
server, fooled or not, from making a release the installs accept, without
new infrastructure. It costs one command and one touch per release, and it
answers a lost key with the backup. If no command per release is
acceptable, the separate machine with a tap is the next option, but it has
to be built first.
