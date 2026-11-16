---
id: proof
name: Proof plugin
version: 1.0.0
author: Fixture contributors
api: 0
kind: [extension, override]
places: [rail.section, app.overlay, command, message.renderer]
overrides: [core.message]
touches:
  network: []
  record: read
  memory: none
  server: false
---

Test fixture only; never shipped. Demonstrates a destination, overlay, command,
relative module import and asset, and a complete core message replacement.
