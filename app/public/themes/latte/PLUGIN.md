---
id: latte
name: Latte & Newspaper
version: 1.0.0
author: Loam contributors
api: 0
default_on: true
kind: [theme]
description: "Loam's own look: café browns and newsprint, warm and easy on the eyes, with latte-art faces if you like."
palette:
  - {token: --paper-page, name: Newsprint, name_dark: Night edition}
  - {token: --paper-card, name: Milk foam, name_dark: Dark roast}
  - {token: --paper-ink, name: Printer’s ink, name_dark: Steamed milk}
  - {token: --latte-espresso, name: Latte, name_dark: Cream}
  - {token: --signal-crema, name: Crema, name_dark: Caramel}
image: cover.webp
image_position: 62% 45%
options: [{"id":"agent-colours","label":"Agent colours","choices":[{"id":"palette","label":"Palette"},{"id":"latte-tones","label":"Latte tones"}],"default":"palette"},{"id":"agent-shapes","label":"Agent shapes","choices":[{"id":"distinct","label":"Each its own"},{"id":"circle","label":"Circle"},{"id":"rounded-square","label":"Rounded square"},{"id":"latte-art","label":"Latte art"}],"default":"circle"},{"id":"latte-mug","label":"In a mug","choices":[{"id":"on","label":"On"},{"id":"off","label":"Off"}],"default":"on"},{"id":"latte-inside","label":"Inside","choices":[{"id":"pour","label":"Pour"},{"id":"initials","label":"Initials"}],"default":"pour"}]
places: []
overrides: []
touches:
  network: []
  record: none
  memory: none
  server: false
---

Latte & Newspaper is the look Loam comes with: café browns on newsprint, in
light and in dark.

## What you can choose

- **Agent colours**: a palette of colours, or latte tones.
- **Agent shapes**: each its own, circle, rounded square, or latte art,
  where each agent's face is poured into a cup, in a mug or not, with a pour
  or its initials.

Choose it, and its options, in Settings › Appearance, separately for your
phone and your desktop.

---

## Technical details

A token-only theme with a café photograph for its preview. It runs no page
code and contacts no network host.
