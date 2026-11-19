# Voice choices and samples

Agent profile → Settings → Read aloud → Voice shows the thirty Gemini voices
with Male/Female labels from [Google's Chirp 3 HD voice table](https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd).
These are the same voice names. Only the bare id (for example, `Charon`) is
stored. The host-backed setting remains available to personas:

```http
PATCH /api/plugins/read-aloud/agents/<agent>
Content-Type: application/json

{"values":{"voice":"Charon"}}
```

The picker separates choosing a voice from previewing it. A sample starts on
one tap, stops on a second tap or another sample, and stops on selection,
dismissal, leaving the profile, recording, or disabling the plugin. Listening
does not change the saved voice. A failed save keeps the picker open and shows
the host's refusal. The shared menu supplies phone sheets, desktop anchoring,
Escape, focus restoration and viewport constraints.

**Recording status: the sample set is not yet recorded.** This checkout has an
empty `page/voice-samples.js` index. Its sample controls are disabled and the
picker explains why. Browser evidence uses a fixture tone to verify native
playback; that tone is not shipped and is not a Gemini voice recording.

To record the real samples, provide the existing read-aloud plugin key through
the broker's server-only secret store. With `BROKER_URL` and `BROKER_TOKEN`
in the process environment and `ffmpeg` on PATH:

```sh
SPEECH_DIR="$TMPDIR/voice-sample-cache" node tools/record-voice-samples.mjs --record
```

The script makes paid calls using `createSpeech`, so model selection (including
`SPEECH_TTS_MODEL`), the Full reply style (`informative, natural conversational
pace, even and clear`), validation, loudness normalization and mono 64 kbit/s
MP3 encoding stay on the existing path. It reads the existing key in memory;
the browser, output files and logs receive no key. The neutral sentence is
the same for every voice: “Hello. This is how I sound reading your notes aloud.”

All thirty takes must succeed and last 3–5 seconds before the index is
published. Inspect and listen to every resulting MP3, then commit `samples/`
and the generated `page/voice-samples.js` together. Scratch contains the
ordinary synthesis cache, which lets an interrupted run resume without paying
again for completed takes. An out-of-range take blocks publication; review it
before retrying with a fresh scratch cache. The shipped picker only reads static
files; preview playback has no synthesis request and requires no provider key.

Verification:

```sh
node --test test/voice-picker.test.js test/agent-profile.test.js test/audio-plugin-lifecycle.test.js
npm test
VOICE_EVIDENCE="$TMPDIR/voice-evidence" node test/voice-picker-browser.mjs
```

The browser check needs separately installed Playwright/Chromium, optionally
named by `LOAM_PLAYWRIGHT` and `LOAM_CHROME`. It checks 390/1440, light/dark,
native fixture playback, absent recordings, stop/switch/selection/dismissal,
plugin disable, scrolling, bare-id PATCH and persona-route persistence.
