# First-party plugins that are listed, not shipped

Each folder here is a plugin made with Loam and published from its own
folder at a tag, then listed in the marketplace. None is installed by being
here: a folder whose name begins with `_` is never read as a plugin
(`lib/loam_plugins.py`), so `_listed/` holds them without putting them in
force. A person installs one from Settings › Plugins like any other.

| Plugin | What |
|---|---|
| `images/` | Make and change images with OpenAI or Google, saved in the vault (2026-11-18) |
| `syncthing/` | Your vault syncs with your own devices over Syncthing; agents pair a device and say how syncing is going (2026-11-18) |
| `autumn/`, `deep-forest/`, `foundry/`, `mist-trail/`, `yosemite/` | The first-party themes beyond Latte and Plain, built in until 0.2.5 (2026-11-18) |

**Where they are published.** Each folder is copied as it is into the public
repository `loamhq/plugins`, as `<id>/`, and tagged there `<id>-v<version>`
(one repository holds them all, so a tag names its plugin). Its listing in
`loamhq/marketplace` links `https://github.com/loamhq/plugins/tree/<id>-v<version>/<id>`.
A new version is a higher `version` in its `PLUGIN.md`, a commit there that
changes only that folder, its new tag, and a pull request to the listing.
The source of truth is this folder; `loamhq/plugins` is what was published.

**Themes moved from the shipped set** are kept for an install that chose one
(`lib/loam_listed_themes.py`, whose `MOVED` names each and the version its
link is at).

A plugin here takes nothing from Loam's own `lib/`; it reads what it needs
from the environment every plugin process is given (docs/plugins.md).

**Dictation and Read aloud** (`dictation/`, `read-aloud/`) were built into
Loam until 0.2.9, as page plugins in the app's `public/plugins/`. They are
Market plugins now, each in its maker's own repository (linked from its
Market page), where a new version is made and tagged. The copies here are at
1.0.0, the last version Loam shipped; the makers' repositories went on from
1.0.1, which added only the README, the licence, an icon and the maker's
name. They are here because the app's tests drive them
(`app/test/audio-plugins.mjs`), because the app server keeps two of Read
aloud's modules as its own (`app/src/speakable.js`, `document-speech.js`,
held equal by `tests/test_to_market.py`), and as the release's fallback when
an install that had one on updates without reaching the Market
(`lib/loam_to_market.py`). When the maker changes one, bring the change
here, so Loam is tested against what people run.
