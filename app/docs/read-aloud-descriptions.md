# Full describes tables and diagrams

“Describe tables and diagrams” is on unless explicitly saved as false. Existing
`loam-ui.read-aloud` objects with only a voice therefore acquire the new default
without rewriting or resetting that voice. The toggle lives in Settings →
Plugins → Read aloud and Settings → Voice, using the shared settings row and
switch styles. Choosing a Files voice preserves the toggle.

Read aloud’s settings adapter is deliberately device-local (`settings.device`),
as documented in `public/plugin-loader.js`. The setting shares that store as
`describeTablesAndDiagrams`. There is no persona settings route for these device
preferences today. The settings registry records the new field and retains the
existing host-route handoff; this change does not invent a second host store.

## Assembly and estimates

`page/speakable.js` is synchronous and shared with the server through
`src/speakable.js`. Its plain text remains the original skip notices. Its block
view now carries description metadata: the complete table, or a recognized
diagram fence’s body and language, plus the preceding heading and paragraph.
Ordinary code is never a description candidate. Supported fences are Mermaid,
PlantUML, `puml`, `dot`, and Graphviz, case insensitive; both backtick and tilde
fences work. The converter’s existing blockquote traversal also retains these
blocks.

Full’s server job obtains these blocks and fills their descriptions before
splitting speech chunks. It uses `SPEECH_BRIEF_MODEL` and the same server-only
Read aloud plugin Gemini key used for TTS and Brief. The browser sends the
choice as `describe_tables_and_diagrams` in both speech requests and lookups;
omitting it defaults on. Non-booleans are refused. The browser’s take identity
also includes the choice for Full, so an already prepared take cannot shadow
the newly selected behavior.

The writer receives a heading capped at 300 characters, a preceding paragraph
capped at 1,500, and the block itself. The prompt requests a lead-in and the
main contextual takeaway in one or two sentences, about 30 words, without
enumerating cells/nodes. Output loses markup and is limited to two sentences.
Empty or excessively long output, an HTTP failure, or the 12-second timeout
uses that block’s original skip text. Up to three blocks are described
concurrently; these calls are not retried. The description stage has a 30-second
total budget, after which unfinished and unstarted blocks keep their skip
notices, leaving time for speech generation even on a note with many blocks. Ordinary code
keeps the existing skip notice and `READ_CODE_BLOCKS` remains false.

Files estimates count about 30 words per description candidate when enabled,
and the original skip text when disabled. The synchronous converter and
`documentSpeakable()` themselves still return the original deterministic text.
Brief’s writer, prompt, and cache identity do not use this preference.

## Caches

Each description, including a failure’s fallback, is atomically cached under
`SPEECH_DIR/descriptions/<sha256>.json`. Its key contains a prompt version, the
enabled setting, text-model name, block content, language, and supplied context.
Changing context deliberately invalidates it: the same numbers can mean
different things under different headings. Concurrent requests for the same
description share one call; a server restart or voice change reuses disk text.
Entries are swept after `SPEECH_TTL_HOURS` unused, alongside the audio cache.
No source content or generated text is added to production logs.

Full audio identities include the enabled description version. Disabled Full
retains its previous identity and exact speech text, so old skipped audio is
still reusable. Brief’s identity is unchanged. A ready MP3 is returned without
another description or speech call. Description text is cached independently
of the voice, making later voices use the same wording while that entry lasts.

## Verification

`npm test` covers detection, context, defaults on existing device settings,
explicit off persistence, estimates, original Full text, per-block failure and
timeout fallback, two-sentence output, cache reuse after restart/across voices,
context/model/content invalidation and expiry, and Brief’s unchanged identity.
Server integration checks use the real HTTP route and TTS pipeline against a
recorded Gemini and encoder. They cover replies and Files, both settings, and
assert the exact words handed to TTS.

The optional Chromium driver is `test/read-aloud-descriptions-browser.mjs`.
It uses the production page and styles with isolated broker/speech fixtures,
checking 390/1440 light/dark, the plugin-details toggle, existing voice-only
settings defaulting on, voice choice preserving explicit off, and new Full
requests for the same reply and file after toggling.

[Captures, transcripts and commands](evidence/read-aloud-descriptions/README.md)
distinguish this local verification from the outstanding live staging check.
No staging address, authenticated broker, provider key or deployment interface
was supplied to this job. Real Gemini wording/audio and deployment are unverified.

The [merged-tree follow-up](evidence/read-aloud-descriptions/merge-check/README.md)
reproduces and removes a timing assumption in the shared-budget regression.
Its test now explicitly triggers the deadline after the first three provider
calls have arrived; production still uses the 30-second budget.
