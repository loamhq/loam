# First-party plugins that are listed, not shipped

Each folder here is a plugin made with Loam and published from its own
folder at a tag, then listed in the marketplace. None is installed by being
here: a folder whose name begins with `_` is never read as a plugin
(`lib/loam_plugins.py`), so `_listed/` holds them without putting them in
force. A person installs one from Settings › Plugins like any other.

| Plugin | What |
|---|---|
| `images/` | Make and change images with OpenAI or Google, saved in the vault (2026-11-18) |

A plugin here takes nothing from Loam's own `lib/`; it reads what it needs
from the environment every plugin process is given (docs/plugins.md).
