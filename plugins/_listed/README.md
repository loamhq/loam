# The core's copies of five Market themes

Autumn, Deep Forest, Foundry, Mist Trail and Yosemite were built into Loam
until 0.2.5. They are Market plugins now, each in its maker's own repository
(linked from its Market page) since 0.2.9; the source of each is there, and a
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
