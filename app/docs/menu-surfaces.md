# Menus stay with their buttons

A single shell (`menuSurface` in `public/menu-surface.js`) serves thread and
Classic menus, shared chat sheets and the Files picker. At a viewport narrower
than 700 CSS pixels it is a full-width, titled bottom sheet. At wider widths,
including tablets whose main page still uses the phone layout, it is anchored
to its actual button, with an 8 px gap. Start/end alignment, vertical flipping
and viewport shifting share one calculation. Long content scrolls inside the
available space. The trigger corner supplies a short scale/fade origin; reduced
motion removes it.

The shell uses the browser's manual Popover top layer: it stays in its own
reconciled DOM tree while escaping the chat card's clipping and stacking
contexts. State and dismissal still belong to each feature. Scroll, resize,
visual-viewport movement, content/trigger resize and completed layout animations
reposition an open surface. CSS zoom is converted once between screen and
layout pixels. If its trigger leaves the viewport the menu closes. A drill-in
whose original button disappears uses the still-connected root button, never
cached click coordinates.

Opening focuses the first enabled action or field. Arrow keys and Home/End
move among controls, Tab stays inside, Escape and outside taps dismiss, and
closing returns focus to the trigger. A nested picker returns to its button
inside the parent. Text fields retain their own arrow-key editing. Existing
phone sheet drag handlers are retained.

## Outside taps

The first tap outside a menu dismisses its topmost layer. A second tap can
activate the exposed control. This applies to a menu’s own opener, a desktop
rail button, an embedded file preview and controls inside a parent sheet.

Each manual popover opens a transparent, full-viewport element in the browser’s
top layer immediately before it opens its surface. Chromium gives the
popover’s painted `::backdrop` a non-interactive hit target. The separate
element owns the press through the click, including over an iframe. The
controller dismisses on click when the press and release belong to the same
layer. Feature-specific pointer-down listeners would break this ordering.

Native Activity dialogs use their own backdrop. The image viewer opens as a
modal dialog above its caller, including a plugin’s Settings sheet. Inline
mention and Market suggestions keep keyboard focus in their editor. Their
`data-menu-inputs` selector identifies the nearby controls that still belong
to the open picker.

The Playwright regression runs in the app suite when `LOAM_PLAYWRIGHT` points
to the installed module. Run it on its own with
`node --test test/outside-dismiss.test.js` and set `LOAM_CHROME` to the
Chromium executable. It uses touch at 390 CSS pixels and mouse input at 1440.
The phone uses the supported 75% interface size so a Chats row is visible
above the Settings sheet. Each comparison shows the same gesture over General.

| Viewport | Before and after the outside tap | Other tested surfaces |
| --- | --- | --- |
| Phone | [Light](evidence/outside-dismiss/gesture-390-light.webp) · [Dark](evidence/outside-dismiss/gesture-390-dark.webp) | [Light](evidence/outside-dismiss/surfaces-390-light.webp) · [Dark](evidence/outside-dismiss/surfaces-390-dark.webp) |
| Desktop | [Light](evidence/outside-dismiss/gesture-1440-light.webp) · [Dark](evidence/outside-dismiss/gesture-1440-dark.webp) | [Light](evidence/outside-dismiss/surfaces-1440-light.webp) · [Dark](evidence/outside-dismiss/surfaces-1440-dark.webp) |

## Inventory before the change

Initial discovery was against `95c62a7`, with the thread selection markers.
Refreshed main `6e5cd71` added reply menus while this job was
running. Both are integrated below; the before captures use that refreshed
main so every inventoried menu has a real baseline.

| Surface | Before | Now |
| --- | --- | --- |
| Chats agent face (2026-11-12) | Immediately created a thread | Shared shell at the face, quiet agent-name header, New thread / View profile; phone bottom sheet |
| Thread list ⋯ | Absolute `top:62px; right:12px` in list column | Shared shell at list-actions button |
| Thread header ⋯ | Absolute `top:64px; right:18px` in chat card | Shared shell at thread-actions button |
| Header delete confirmation | Absolute `top:72px; right:18px` | Same anchor as thread actions; Keep first |
| Providers & models | Fixed, computed once from click rectangle; phone sheet | Shared shell at composer or member control |
| Provider and model choices | **Already inline** listboxes within configuration, replacing the former `.tt-choice-menu` | Stay inline in the anchored/sheet configuration surface |
| Thread background tasks and task details | Fixed, one-time right/bottom rectangle; phone sheet | Shared shell at live task count; details stay in it |
| Mention choices | Absolute `bottom:100%` inside composer; keyboard belongs to typing | Retained, including Everyone, arrow selection and Escape |
| Thread details | Full scrolling details page | Retained workspace |
| Read aloud (thread and Classic messages) | Absolute relative to 30px glyph, flipped by a fixed 260px threshold; same floating menu on phone | Shared shell at the clicked speaker; phone sheet |
| Composer + / Add | Absolute above composer row on desktop, existing sheet on phone | Shared shell at +; upload choices retained |
| Classic This chat / Model / Effort | Same absolute composer-row menu with inline subpages; phone sheet | Shared shell at model chip; subpages retain root anchor |
| Classic background tasks | Absolute above notice row, separate width/offset CSS; phone sheet | Shared shell at background disclosure |
| Context | Centered desktop dialog; phone sheet | Shared shell at context indicator |
| Classic Chat actions (list), This chat (header), selected-chat actions and rename | Centered action/form dialog; phone action sheet | Shared shell at each initiating control; titles added where missing |
| New chat/provider choice | Centered desktop dialog; phone sheet; model/effort subpages | Shared shell at agent; existing provider chips and subpages retained |
| Running now | Centered desktop dialog; phone sheet | Shared shell at power icon; from Settings it retains the root Settings anchor |
| Settings, provider details and sign-in forms | Centered two-pane desktop dialog; phone sheet/subpages | Shared shell at Settings; existing forms and inline chips/toggles retained |
| Settings → Read aloud → Files voice | Native select popup, positioned by the operating system | Shared nested titled picker at Voice |
| Other Settings pickers | Inline buttons, ranges and chips (Appearance, microphone, thresholds, providers) | Retained inline controls; they open no popup |
| Attach from Files / file actions opened from chat | Centered desktop dialog; phone sheet | Shared shell, anchored to the initiating +/resource control |
| Thread message Reply / Copy actions | Custom click/hold menu | Removed on 2026-11-11; footer icons and swipe provide actions, long press and right-click stay native |
| Message timestamp details, long prose, tool/step actions | Inline disclosures and Copy, not floating menus | Retained inline; Read aloud covered above |
| Recipient face names | Small hover/focus tooltip on the face itself | Retained tooltip; not an action menu |

Activity dialogs and the full-screen expanded composer are separate workspaces
outside this menu inventory. No runtime, provider contract or persisted record
changes are needed.

## Verification

`npm test` includes the positioning arithmetic, shell and existing feature
checks. The real-page browser driver is `test/menu-surfaces-browser.mjs`; run
with an independently installed Playwright/Chromium and `MENU_EVIDENCE` pointing
to the output directory. `LOAM_PLAYWRIGHT` and `LOAM_CHROME` select those external
executables/modules. `MENU_BASELINE=<commit>` serves the original four page
files from this repository's git objects for before captures. Records are
synthetic behind the actual HTTP relay; no real session starts.

See [captures and measured checks](evidence/menu-surfaces/README.md).
