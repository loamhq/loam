# Theme options: host handoff

The browser and persona settings API use the same broker route for the
owner's manifest-defined theme options.

## Wire value and merge

Use the authenticated, no-store `GET/PATCH /v1/settings/appearance`.
GET and successful PATCH return 200 with the whole value, filling defaults:

```json
{
  "phone": {
    "mode": "system", "size": 100, "theme": "latte",
    "options": { "latte": { "agent-colours": "palette", "agent-shapes": "circle", "latte-mug": "on", "latte-inside": "pour" } }
  },
  "desktop": {
    "mode": "system", "size": 100, "theme": "latte",
    "options": { "latte": { "agent-colours": "palette", "agent-shapes": "circle", "latte-mug": "on", "latte-inside": "pour" } }
  }
}
```

A PATCH names only edited leaves. Either device row edits the same account
options. The broker merges atomically by theme and option.

```json
{ "desktop": { "options": { "latte": { "agent-shapes": "circle" } } } }
```

This changes the shape on every device and preserves agent colours, mode, size
and theme. Switching to Plain preserves the saved Latte options. Mode and active
theme also sync across devices. Size and Sun transition still follow device kind.

Validate all leaves against the installed manifests. The shipped catalog is:

| Theme | Option | Choices | Default |
| --- | --- | --- | --- |
| Latte | `agent-colours` | `palette`, `latte-tones` | `palette` |
| Latte | `agent-shapes` | `distinct`, `circle`, `rounded-square`, `latte-art` | `circle` |
| Latte | `latte-mug` | `on`, `off` | `on` |
| Latte | `latte-inside` | `pour`, `initials` | `pour` |
| Plain | none | | |

Unknown device kinds, fields, themes, options or choices; non-object/array maps;
non-string choices; and empty patches are 400 with an error sentence. Preserve
existing mode/size validation. Do not silently drop `options` and return success.
On older persisted data, absent choices use manifest defaults. Removed choices
use the new default on read; stale catalog entries are omitted from public
readings. Do not persist browser-derived settings separately.

## Persona settings registry

[appearance-settings.registry.json](appearance-settings.registry.json) contains
all fourteen shipped leaves. It is generated from `loadThemes()` by
[appearance-registry.js](../tools/appearance-registry.js); installation must feed
this declaration into the host's existing registry and use the same catalog for
route validation. There is no new registration route assumed here.

```
appearance.phone.options.latte.agent-colours
appearance.phone.options.latte.agent-shapes
appearance.desktop.options.latte.agent-colours
appearance.desktop.options.latte.agent-shapes
appearance.phone.options.latte.latte-mug
appearance.phone.options.latte.latte-inside
appearance.desktop.options.latte.latte-mug
appearance.desktop.options.latte.latte-inside
```

Each is a string enum with the default above. Reads/writes through the persona
settings tool and these HTTP routes must reach the same stored value. When a
theme is installed/removed, regenerate and synchronize the declaration as part
of installation; CSS-only option additions require no core app code change.

```sh
node tools/appearance-registry.js --write
node tools/appearance-registry.js --check
```

## Acceptance to run on the host

1. GET old records: all defaults appear; size/mode/theme survive migration.
2. PATCH one desktop option, then a different phone option. GET returns both
   leaves for both devices, including under concurrent writes.
3. Choose Plain, then Latte: the two Latte choices remain.
4. Restart the host and open a fresh browser: the correct shape/colour choice is
   present at first paint, with no preference in browser storage.
5. Read and change each new registry leaf through the persona settings tool.
   A focused browser picks up the change through the existing appearance GET.
6. Invalid maps/ids/choices return 400 and preserve the old value.

## Compatibility already implemented

The relay forwards both operations unchanged. The public boot response includes
only validated choices/defaults from the installed catalog. An old host omitting
options still boots with defaults. Refused writes keep the live preview, report
the host's sentence and offer retry. If a host returns 200 while dropping an
option, the controller detects it and says that the host has not saved the theme
options. No browser preference cache masks the missing integration.

The browser fixture tests boot, retries and fresh navigation. The broker's
settings tests cover concurrent merges, legacy records and persona-tool parity.
[Browser evidence](evidence/theme-options/README.md).


## Latte art extension

The Latte manifest now declares `latte-art` plus the two sub-options above.
Install its current catalog alongside the generated registry so HTTP validation
and persona settings accept the same enums. The page hides the colour control
while art is selected and hides sub-options for other shapes; hidden choices
remain stored and can still be edited through the same route. No dependency
validation should discard hidden values. PATCH, for example:

```json
{ "phone": { "options": { "latte": {
  "agent-shapes": "latte-art", "latte-mug": "off", "latte-inside": "initials"
} } } }
```

A persona writes those same shared leaves through either device alias.
