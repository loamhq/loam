---
name: loam-publishing-to-the-marketplace
description: How to share a Loam plugin or skill with other people - what exists today (a GitHub repository at a tag, installed from a link) and what does not yet (a marketplace listing). Use when the owner wants to share, publish or sell something they or an agent built.
---

# Publishing to the marketplace

**There is no marketplace listing yet.** What exists today is installing
from a link, and that is how something is shared.

**A plugin.** Put its folder (with its `PLUGIN.md`) in a GitHub repository,
at the root or in a subfolder, and tag the commit to share, with the same
version as the manifest. Whoever installs it gives Loam the link to that
tag or commit (`https://github.com/<owner>/<repo>/tree/<tag>/<folder>`).
Loam shows them what it declares under `touches` and what it holds before
anything is installed, and installs it only on a second, confirming call.
A branch is refused, because a branch moves. Every later version is
confirmed again, with what it adds named.

**A skill.** A skill is shared as a plugin of `kind: skill`: a folder with
its `SKILL.md` and a `PLUGIN.md`. Loam lists such a package but does not
install one from a link yet; to use one, copy its folder onto the shared
shelf (`skills/` in the vault's system folder) and every agent sees it.

**Publishing is exposure.** Making a repository public, or anything else
reachable on the open web without the login, waits for the owner's direct
yes, each time.
