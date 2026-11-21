# The core's copies of Market plugins

Autumn, Deep Forest, Foundry, Mist Trail and Yosemite were built into Loam
until 0.2.5. They are Market plugins now, each in its maker's own repository
(linked from its Market page) since 0.2.10; the source of each is there, and a
new version is made and tagged there.

The core still styles them by id (`materials.css`, `face-paints.css`,
`signature-avatar.js` in `app/public/`), so it keeps a copy of each here to
hold that styling to its rules: the app's tests read these folders
(`app/test/listed-themes.mjs`), and so do the theme tools in `app/tools/`.
They are also the release's own copy that `lib/loam_listed_themes.py` falls
back on when an install that was wearing one updates past 0.2.5 without
reaching GitHub.

None is installed by being here: a folder whose name begins with `_` is never
read as a plugin (`lib/loam_plugins.py`). These copies are at 1.0.1, the last
version published from this repository (to `loamhq/plugins`, now archived,
at `<id>-v1.0.1`); the makers' repositories went on from 1.0.2, which added
only the README, the licence and the maker's name. When a maker changes a
theme's look, bring its `theme.css`, `fonts.css` and fonts here, so the core
is tested against what people wear.

**Dictation and Read aloud** (`dictation/`, `read-aloud/`) were built into
Loam until 0.2.9, as page plugins in the app's `public/plugins/`. They are
Market plugins now, each in its maker's own repository (linked from its
Market page), where a new version is made and tagged. The 1.2.0 copies here support Google, OpenAI and native browser speech. They are here because the app's tests drive them
(`app/test/audio-plugins.mjs`), because the app server keeps two of Read
aloud's modules as its own (`app/src/speakable.js`, `document-speech.js`,
held equal by `tests/test_to_market.py`), and as the release's fallback when
an install that had one on updates without reaching the Market
(`lib/loam_to_market.py`). When the maker changes one, bring the change
here, so Loam is tested against what people run.
