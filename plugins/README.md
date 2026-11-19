# First-party plugins

Plugins that ship with this runtime and whose halves are the machine's: the
third folder of the Shipped layer, after loam-ui's `public/themes` and
`public/plugins` (`lib/loam_plugins.py`, `shipped_roots`; docs/plugins.md,
"Two layers"). Each folder is one plugin, its `PLUGIN.md` the manifest.

| Plugin | What | Default |
|---|---|---|
| `reviewer/` | the reviewer: its timer, its instructions, what agents are told of it | on |
| `daily-notes/` | the daily note's folder and the page's button | off |
| `doc-comments/` | comments and suggestions on Markdown docs: the calm editor, its server, the agents' `doc_*` tools (`docs/doc-comments.md`) | off |
| `images/` | your agents make and change pictures with OpenAI or Google, saved in the vault: the agents' `generate_image` tool, its keys and settings (built in since 0.2.10; in the Market before) | off |
| `git-backup/` | the vault and AI Spaces pushed to private git repositories of the owner's, with a deploy key per repository; health, warnings and restore (`docs/backup.md`) | off |

These are Loam's **built-in** plugins: they come with each release and are
turned on and off in Settings › Plugins. Everything in the Market lives in
its maker's own repository instead (docs/plugins.md, "Built in, or from the
Market"). `_listed/` is not a plugin folder: it holds the core's copies of
the five themes that left the shipped set, for its tests
(`_listed/README.md`).

Dictation and Read aloud left the shipped set for the Market in 0.2.9; their
copies are in `_listed/` too, for the tests and as the release's fallback
(`lib/loam_to_market.py`).
