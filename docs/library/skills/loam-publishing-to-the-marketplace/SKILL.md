---
name: loam-publishing-to-the-marketplace
description: How to share a Loam plugin, theme or skill with other people - by link (pushed to a GitHub repository of the owner's at a tag) and by asking for a listing in the marketplace (a pull request from the owner's own GitHub account). Use when the owner wants to share, publish or list something they or an agent built, or to find plugins others made.
---

# Sharing a plugin, and the marketplace

A plugin is shared by **link**: its folder in a GitHub repository, at a
tag. Anyone installs it by pasting a link to it into **Install from a link**
(in Settings › Plugins, and at the foot of the Market); the repository's
address is enough, because Loam finds the newest release and the plugin's
folder itself. The **marketplace** is a list of such links that every Loam
shows in Settings › Market; being on it is asking, not publishing.

**Built in, or in the Market.** Loam has two kinds of plugin, as Obsidian
does. *Built-in* ones ship inside Loam (Images, Quick actions, Comments, the
reviewer, Latte and Plain): they come with every release, most start off,
and the owner turns them on in Settings › Plugins. *Market* ones each live
in their maker's own repository and are listed in their maker's name.
Something new, whoever makes it, goes to the Market in its maker's name;
one that proves itself can later become part of Loam, and then the Market
copy steps aside for the built-in one on the update that brings it, and its
listing is withdrawn.

What the owner needs: a GitHub account, connected in Settings › Connections
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
   One repository per plugin, on its maker's account, is how everything in
   the Market is kept, Loam's own makers' plugins included. (A repository
   that holds several tags each `<id>-v<version>`, so a tag names its
   plugin; for the Market, keep one each.)
4. **Give the owner the link.** It works for others once the repository is
   public. Nobody has to build a link by hand: `https://github.com/<owner>/<repository>`
   installs the newest version, and a repository of several plugins asks
   which one. Whoever installs it is shown what it declares under `touches`
   before anything is installed, and every later version is confirmed again.

A **skill** is shared the same way, as a plugin of `kind: skill` (a folder
with its `SKILL.md` and a `PLUGIN.md`). Loam lists such a package but does
not install one from a link yet; whoever gets it copies its folder onto
their shared shelf (`skills/` in the vault's system folder).

## Ask for a listing in the marketplace

The list lives in the public repository `loamhq/marketplace`; a listing is a
pull request there. Loam's reviewer reads it by itself within a few minutes,
against the marketplace's standards page (`review/STANDARDS.md` there): the
plugin's code at the exact commit, its listing and README. It never runs the
code. A pass merges by itself and writes **Loam's note** (what it touches,
what it may change, what is worth knowing), shown on its Market page; every
Loam installs exactly the commit that was reviewed. A refusal says on the
pull request what to change; the maker fixes it and pushes again, as often
as they like. A new version is reviewed the same way. To ask:

```
loam market request <a link to it> --description "One sentence on what it does."
```

Any link to it works (the repository's address, or the one `share` answered).
It reads the plugin from the link (nothing is installed) and answers the
listing as it would be filed, pinned at the release it found, and a link. Show the owner the listing. On
their yes, give them the link: it opens GitHub's new-file page in the
marketplace with the listing filled in. Signed in, they press **Propose new
file**, then **Create pull request**; GitHub makes the fork on their account.
The repository must be public, and the plugin released at a tag (`share`
does that): a listing is pinned, so a branch's link is filed at the commit
it is on now and never follows it.

Read the standards page with the owner before asking: what it refuses most
is a host the plugin contacts that `touches` does not name, reading a key or
login it was not given, and code it downloads and runs.

A listing can carry more, and is worth it (the marketplace's README has the
fields): a `cover`, a wide picture (at least 1500 pixels across, 3:2) in the
plugin's folder or at an https link, shown on its page and its cards; a
README.md in the folder, which its page shows (pictures in the folder show
too; HTML does not); and an `author` with the maker's own links
(`{"name": …, "links": [{"label": …, "url": …}]}`: their site, GitHub,
Patreon, email…), each shown with its own small mark. Ask the owner which
links they want shown; never add one they did not give.

## Find what others made

`loam market list` (or Settings › Market) shows the list,
marked where something is installed already. To find something for the
owner, search it as they would (`mcp__engineer__market`, or `loam market
search <words>`), and show what fits as a card in your reply: each result's
`card` line, on a line of its own. They install it from the card, through
the same sheet as the Market's, and say yes there; you never install it for
them. When someone hands you a link to a plugin that is not listed, read it
with `mcp__engineer__market` and `link` (or `loam market link <link>`): any
shape works, nothing is installed, and the answer's `card` line offers it the
same way. Nothing listed yet is the normal state for a new marketplace, not an
error: offer to build it instead.

### Download counts

Sharing attaches the plugin folder to the GitHub release. After review, a
confirmed Market install or update downloads that file and verifies it
against the reviewed commit. GitHub counts the download. The Market collects
the totals nightly. They include direct downloads and updates. Git fallbacks,
older installs and update checks add nothing. A failed share after tagging can
be retried at the same version with the same files.
