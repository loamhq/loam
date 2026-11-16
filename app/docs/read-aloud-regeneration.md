# Available audio and regeneration

The Read aloud menu gives cached Listen/Brief takes a play icon with the
theme's secondary-action fill and matching ink, and “Ready to play” with the
duration. Both rows sit on the ordinary menu background, without a ready
card, tint or border. An unmade take keeps its neutral Newspaper icon and
describes the time needed to make it. Each available
take has a separate Regenerate button beside its playback control. Files has
the same action for the selected Full or brief listening time. No new setting
or persona route is needed.

Regenerate sends the existing speech request, including its source, text,
mode, voice, style and (for Files) listening minutes, with `force: true` to
`POST /api/speech`. The relay still reads the current host-managed persona
voice. A force flag must be a boolean. It bypasses the ready cache and answers
202 with a new job id; ordinary requests keep their existing cache behavior.
Concurrent requests for the same resolved variant share one job. Lookup starts
nothing. While a replacement runs, a lookup's preparing answer includes
`previous`, the prior ready take, so another page can still offer playback.

The replacement uses an immutable MP3 and sidecar under a new id. Only after
writing and validating both does the service atomically publish
`<variant-id>.current`, containing the new id. Further cache lookups resolve
that pointer, including after restart. An expired replacement does not revive
its predecessor. Both the audio files and the pointer are in the existing
speech-cache directory and follow its expiry policy; no migration is needed.
Older immutable audio URLs survive until their own expiry, so a playing
browser's range requests cannot switch recordings mid-track. A failed
provider request, failed validation or failed encoding leaves the previous
cache reference untouched. No broker contract change is required.

The page keeps its existing player node, audio object, position and playback
state during generation, failure, and success. It never calls play from the
regeneration path. If audio has already been started, that player keeps its
recording until it is closed; the menu/player says to close it to use the new
audio. The next opening uses the replacement. Unplayed takes use the new audio
at their next tap. Busy/error feedback stays with that variant, repeated taps
are ignored, and a delayed pre-regeneration lookup cannot restore stale
metadata. Thread take identity includes explicit voice/style options so a
changed persona choice cannot reuse a take for earlier options.

The icon-only treatment supersedes the earlier ready card in every theme.
Current visual and regeneration checks: [read-aloud-icon](evidence/read-aloud-icon/README.md).
Original service/cache validation: [read-aloud-ready](evidence/read-aloud-ready/README.md).
