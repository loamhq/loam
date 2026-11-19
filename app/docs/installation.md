# Installation configuration

The web app reads public installation settings from environment variables at
startup and embeds only those fields in its existing, uncached
`/appearance-state.js` response. That script runs before styles and page
modules. The defaults live in `public/installation.js` and are shared with
`src/config.js`. The README configuration table is the deploy renderer's list.
Invalid domains, zones and vault-relative paths fail startup with a named error.

`APPS_DOMAIN` governs the Apps framing decision and the domain entry in the
page's CSP. The existing general HTTP/HTTPS frame sources remain, since file
and plugin content already uses them; this change does not narrow the policy.

`APP_TIME_ZONE` governs the shared display clock, calendar-day grouping,
daily/inbox note filenames and Sun's initial fallback location. It does not
change a saved Sun home or the browser's locale/hour-cycle preference. General
board quick-note dates and expiry durations remain UTC, as the runtime's board
format requires. Previously the display clock and filenames used the browser
zone, not an installation literal. The default zone now comes from the served
configuration; staging should check with the intended owner's devices.

Saved Files inbox choices still win over the installation default. Daily notes
uses the enabled `daily-notes` plugin's folder; neither the web environment nor
browser storage overrides it. Actual
board and conversation paths come from runtime records and Files resources;
the web half has no conversations-folder literal or new conversations-folder
setting. `VAULT_BOARDS_FOLDER` affects only the synthetic design preview.
`VAULT_GENERAL_BOARD` identifies the general board's special quick-note format.
`VAULT_ATTACHMENTS_FOLDER` identifies the subtree in older absolute attachment
records. Relative paths returned by uploads are never rewritten.

## Owner identity

`GET /api/owner` relays the authenticated `GET /v1/owner` response unchanged,
with no cache. It accepts only GET. The runtime supplies `{id, name, accepts}`.
The page reads it before opening conversations and retries on focus. There is
no personal fallback identity: a failed read leaves ownership unknown.
`public/owner.js` owns matching against `accepts`, displayed owner names and
initials. That matching covers Apps attribution, schedules/Activity, thread
bubbles and reply labels, mention-join grouping and optimistic-send reconciliation.
Board quick-add comments use the supplied display name. Sending still omits
owner fields where the runtime already supplies them.

The wire values `reach: "owner"` and Activity actor **kind** `"owner"` are
unchanged vocabulary. Neither appeared as a literal in this checkout's web
sources; generic relay and settings code continues to preserve such values.
Historical design-quote attributions in comments remain; they are not identity
defaults. The before/after inventory is in the evidence directory.

## Fresh plugins and existing state

Each shipped `PLUGIN.md` now explicitly declares `default_on: true|false`.
All themes, including Latte, declare true. Read aloud and Dictation declared
false until 0.2.9, when they left the shipped set for the Market: a fresh
install has neither (`test/installation.test.js`). `src/shipped-plugins.js` reads the declaration for the local fallback
catalog. Theme availability remains controlled by Appearance, without a
second enable switch.

An effective runtime catalog row's `on` is authoritative. The web app never
patches it to a new manifest default. Refreshes, outages and reloads retain
saved runtime choices. During a first transient failure, enabled state stays
unknown and plugin code stays off; a 404/405 host gets the shipped fallback.
The audio workflow DOM harness explicitly opts into audio so its tests keep
exercising recording and playback; fresh defaults have separate checks.

## Runtime handoff

The brief described manifest defaults, but this checkout had no declarations
and the fallback enabled every extension. The app-side declarations and parser
are now concrete. The host catalog/persistence code belongs to another repo
and was neither inspected nor changed here.

Before accepting a fresh runtime installation, its plugin catalog must read
the boolean `default_on` from each shipped manifest **only when no saved
enabled-state entry exists**. An existing true or false must win, without a
migration that resets it. Required host check: a new state store answers audio
off and themes on; a store with either audio plugin enabled still answers it
enabled after an update/restart. If the runtime already uses another manifest
field, agree that field before rollout and align the shipped declarations.
The web tests prove catalog parsing and client preservation, not this external
host persistence behavior. This is a staging dependency, not a reason to
infer freshness in the browser or send corrective PATCHes.

## Branching without a vendor key

No provider or new broker route is needed. The existing branch read includes
the runtime's context-based `{title, draft}` fallback, which remains editable
and creates an unsent thread draft. Without a Read aloud plugin key, the app returns
that opening without a model call. A broker-supplied plugin key still enables the existing
optional polish; vendor errors fall back the same way. This preserves existing
behavior and requires no login/key on a new installation.

## Checks and staging

`npm test` runs the installation, plugin-state and no-key branch checks alongside
the existing suite. [Evidence](evidence/install-configuration/README.md) records
the commands, counts and produced branch. These are synthetic brokers and DOM
interaction checks, not a live runtime walk or browser layout capture.

Staging should verify owner name/initial and old-author aliases, existing audio
preferences, Apps framing, Files note dates/folders and no-key branching. The
runtime handoff above must be settled for fresh installs. Deployment was not
part of this change.
