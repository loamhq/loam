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

`_listed/` holds first-party plugins that are published and listed in the
marketplace rather than shipped (`_listed/README.md`): Images, so far.
Dictation and Read aloud left the shipped set for the Market in 0.2.9; their
copies are there too, for the tests and as the release's fallback
(`lib/loam_to_market.py`).
