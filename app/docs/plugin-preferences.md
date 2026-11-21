# Read aloud and Dictation settings

Settings has no Voice section. Open **Settings → Plugins → Read aloud** or
**Dictation**. Each detail owns its controls, shows them only while on, and
keeps values while off. Old Voice/Dictation/Read aloud settings links resolve
to Plugins. Appearance, Agents, Providers, Integrations, Files, Sign-in and
Update keep their destinations.

The plugins declare their UI through the public `settings.section` place with
`group: 'plugin'`, rather than theme manifest `options`. These controls include
microphone enumeration, note links, a sample player and a voice menu: they need
the existing render mechanism. Ownership comes from the registering scope, so a
third-party plugin uses the same API without a core id check. The host sends
`plugin.settings.opened` with `{id}` on entry, re-entry or enabling in detail,
and renders manifest-declared secret fields once. See the
[public contract](plugins/places.md).

## Every former Voice control

| Control | New home | Existing storage / route |
| --- | --- | --- |
| Dictation mode: Record first / As you speak | Plugins → Dictation | `loam-ui.dictation.mode`, browser |
| Polish: Off / Light / Full | Plugins → Dictation | `loam-ui.dictation.polish`, browser |
| Longest recording | Plugins → Dictation | `loam-ui.dictation.maxMinutes`, browser |
| Microphone choice and Allow microphone | Plugins → Dictation | `loam-ui.dictation.deviceId`, browser; permission is the browser’s |
| Names and terms note/link/status | Plugins → Dictation | `GET /api/dictation-terms` → host `/v1/dictation-terms`; note stays on host |
| Copy / Forget terms saved before the note | Plugins → Dictation | `loam-ui.dictation.terms`, browser; retained until explicitly forgotten |
| Files voice and voice picker | Plugins → Read aloud | `loam-ui.read-aloud.voice`, browser |
| Describe tables and diagrams | Plugins → Read aloud | `loam-ui.read-aloud.describeTablesAndDiagrams`, browser |
| Prepare / Play / Pause sample | Plugins → Read aloud | Existing speech routes/cache; no new preference |
| Shared Google key guide | Both plugin settings and browser speech controls | `PUT /api/keys/google` checks and saves the shared key |

No data migration is needed: plugin preference adapters still read the exact
same keys on first activation. No defaults overwrite stored values. The
`loam-ui.speech` playback rate and legacy listen/brief thresholds also retain
their keys; those are not controls in the former Voice section. Per-agent
reading voices remain in agent profiles and use the existing host-backed
`/api/plugins/read-aloud/agents/:agent` route.

## Credentials and shared values

Both manifests declare optional Google and OpenAI keys. Loam resolves each declared key on the server. The plugins use Google first, OpenAI second and browser speech when neither key is set. The key guide uses the same checked write as Connections and refreshes both plugins after saving. An existing plugin-specific override still takes precedence for that provider.

## Outstanding host contract: persona parity

The brief requires every setting to be changeable by a persona through the
same route as the page. That is **not currently true** for these device-local
preferences. The supplied host API has plugin settings and agent settings
routes, but no device-settings route. The previous
[settings registry](settings-sections.registry.json) already records this gap;
moving the UI cannot resolve it.

The host needs a contract for device identity, importing existing browser
values without overwriting them, and delivering persona changes back to the
page. Microphone ids also belong to a particular browser/device. This repo
cannot implement or verify the host half, and the brief says to return a
broader change before building it. This change leaves persistence in place
and records that dependency instead of inventing an unsupported route or
second store. Host-managed key/agent-voice routes stay available unchanged;
full persona parity remains incomplete until the device-settings contract is
supplied and the page is bound to it.

## Evidence

[Browser captures and test output](evidence/plugin-preferences/README.md)
cover 390/1440 light/dark, old stored values on first activation, edits, off/on,
reload, hidden disabled controls, no disabled imports, and third-party settings
ownership. The environment supplies no staging endpoint, broker credentials
or deployment interface. These are loopback fixture captures, **not staging**;
the requested staging verification remains outstanding.
