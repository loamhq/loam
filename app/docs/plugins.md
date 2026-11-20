# Plugins and personal changes

You can change anything. The [stable parts](stable-parts.md) are the short
compatibility promise; the [types](../public/loam.d.ts) describe API 0 for an
editor or coding agent. Start with [places and contexts](plugins/places.md),
[loading and installation](plugins/loader.md), and the [module map](plugins/modules.md).

Keep personal changes in the host's **Yours** layer. The host owns the plugin
folder and `/v1/plugins` catalog; the page reads it through `/api/plugins` and
loads only effective rows. A Yours package with a shipped id replaces the
shipped package, including a fork carrying `forked_from` provenance. The page
preserves that metadata; it neither scans host paths nor edits shipped files.
Removing the personal copy reveals the shipped one. No user folder is mounted
into the web container and no update process here writes Yours.

Settings › Plugins groups installed extensions by origin and labels each theme
card. The page derives origin from the broker's `layer` and `source`. A Market
install records `source.via` as `market`. A link install also appears under
From the Market when its id, repository and release match the listing, including
a listing's reviewed commit. An explicit listing folder must match the installed
folder. The About page links to the listing for Market plugins and shows the
host folder for locally made plugins.

Enabled server packages show a status on the list when their service needs
attention and the current state in About. Failure logs stay collapsed until
opened. Restart posts to `/api/plugins/:id/restart` and refreshes the catalog.
Agent and skill packages marked `switch: "listed"` have no enable switch.
Plugin icons load through the app's files relay and fall back to an initial.

App plugins join Apps under their catalog source (Yours, Installed or Loam).
Their `/x/:id/` address stays on the page's origin, served by the installation's
existing authenticated front end. It is never sent through the JSON relay.
Desktop main and side panels re-read the catalog before opening a plugin;
off, starting, failing, stopping and stopped apps show their state without
creating a frame. Phones open running apps in a browser tab.

Settings › Connections › GitHub reads `/api/integrations/github/accounts` in
host order. Each account has its own refresh and disconnect, with the host's
`disconnect_also` named before a personal account is disconnected. Returned
uninstall/revoke/delete links remain explicit browser actions. Add account
uses the host's install URL or form-posts its registration manifest through
the existing callback pages; organization options support `org` and `own_app`.
The relay preserves JSON bodies and the optional `by` query on these new
routes. Older hosts answering 404/405 for accounts retain their legacy shared
GitHub controls. See the [fixture checks and captures](evidence/plugin-runtime/README.md).

Shipped page modules settle first, independently and with bounded startup;
Yours modules then activate. Registry and stylesheet precedence also use the
layer, so later asynchronous registrations from defaults cannot displace a
personal replacement. An update/reload retains the host's effective choice.

An effective Yours row with `has.theme` loads its `theme.css` after defaults,
including CSS supplied by an extension rather than a theme. Themes remain
selectable through Appearance. For another plain CSS file, a page module can
write `loam.ui.styles.add('assets/custom.css')`; it receives the same ordering
and automatic cleanup. The host must serve the file through its existing
plugin file route. A loose CSS file absent from the host catalog cannot be
discovered by the page. There is no new host filesystem/scanning contract.

To replace a component, register its component id in the owning place. A
manifest's `overrides` documents it, and Settings shows replacements even if
the manifest omitted that declaration. Going past stable component ids or
changing DOM directly remains allowed.

Read aloud and Dictation own their page code, helpers, styles, controller
state and existing device preferences in their own packages. Since 0.2.9
they are Market plugins, each in its maker's own repository, and no longer
ship: a fresh install has neither, and draws no mic, reader or agent voice
field until one is installed and on. Their copies for this repository's
tests are `../plugins/_listed/dictation/` and `../plugins/_listed/read-aloud/`
(`test/audio-plugins.mjs` installs them into the test page). They use the
same public interface available to personal packages, and keep their device
preferences under the same storage keys, so an install that had them keeps
its choices. The server keeps its own copies of the two modules it shares
with Read aloud (`src/speakable.js`, `src/document-speech.js`), held equal
to the listed copy by `tests/test_to_market.py`. Generic fields, sheets, lifecycle, navigation,
normalization, browser audio focus and UI primitives stay core. Server speech,
transcription and authenticated relays remain server infrastructure; this
page-only change does not retire them or change vendor credentials.

Each audio plugin declares its settings through `settings.section` with
`group: 'plugin'`. Settings → Plugins renders them only in their owner’s
detail while it is on; there is no separate Voice destination. See the
[preference inventory and host route gap](plugin-preferences.md).

Plugin-owned services can contribute rows, details and notices inside
Integrations through the [settings contract](plugins/places.md#plugin-owned-integrations).
A plugin's host-owned files stay outside `public/` and the web image.
