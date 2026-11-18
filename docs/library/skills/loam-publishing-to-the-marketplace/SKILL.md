---
name: loam-publishing-to-the-marketplace
description: How to share a Loam plugin, theme or skill with other people - by link (pushed to a GitHub repository of the owner's at a tag) and by asking for a listing in the marketplace (a pull request from the owner's own GitHub account, reviewed by hand). Use when the owner wants to share, publish or list something they or an agent built, or to find plugins others made.
---

# Sharing a plugin, and the marketplace

A plugin is shared by **link**: its folder in a GitHub repository, at a
tag. Anyone installs it from that link (Settings › Plugins › Install from a
link). The **marketplace** is a list of such links that every Loam shows in
Settings › Plugins; being on it is asking, not publishing.

What the owner needs: a GitHub account, connected in Settings › Integrations
(that is how you push for them), and a repository they made for it.

## Share it by link

1. **The plugin is ready.** It is in the owner's plugins folder, its
   `PLUGIN.md` is right (`loam-building-a-plugin`), it is listed as not
   broken, and its `version` is the one to share.
2. **The owner makes the repository** on github.com (New repository, e.g.
   `loam-dusk`), on the account GitHub is connected with, with nothing in
   it. Public or private is their choice and, for public, their direct yes:
   a public repository is on the open web. Ask; do not assume.
3. **Push it:**
   ```
   loam market share <plugin id> <owner>/<repository>
   ```
   It puts the folder in the repository as `<id>/`, tags it `v<version>`,
   and answers the link: `https://github.com/<owner>/<repository>/tree/v1.0.0/<id>`.
   A new version is a higher `version` in `PLUGIN.md` and the same command.
   A repository that holds several plugins tags each `<id>-v<version>`
   instead, so a tag names its plugin; Loam's own first-party plugins are
   published that way, in `loamhq/plugins`.
4. **Give the owner the link.** It works for others once the repository is
   public. Whoever installs it is shown what it declares under `touches`
   before anything is installed, and every later version is confirmed again.

A **skill** is shared the same way, as a plugin of `kind: skill` (a folder
with its `SKILL.md` and a `PLUGIN.md`). Loam lists such a package but does
not install one from a link yet; whoever gets it copies its folder onto
their shared shelf (`skills/` in the vault's system folder).

## Ask for a listing in the marketplace

The list lives in the public repository `loamhq/marketplace`; a person reads
every listing before it is merged (its README says what they check). To ask:

```
loam market request https://github.com/<owner>/<repository>/tree/v1.0.0/<id> --description "One sentence on what it does."
```

It reads the plugin from the link (nothing is installed) and answers the
listing as it would be filed, and a link. Show the owner the listing. On
their yes, give them the link: it opens GitHub's new-file page in the
marketplace with the listing filled in. Signed in, they press **Propose new
file**, then **Create pull request**; GitHub makes the fork on their account.
The repository must be public, and the link at a tag, never a branch.

## Find what others made

`loam market list` (or Settings › Market) shows the list,
marked where something is installed already. Install one by its link, as any
link: the owner sees what it declares and says yes. Nothing listed yet is
the normal state for a new marketplace, not an error.
