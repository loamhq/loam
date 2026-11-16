// The words on the line where the reply will be, and when they change.
//
// The owner, 2026-10-01: "the waiting for response text change way too quickly
// while we wait for the first response from the model." It changed every 3.2
// seconds, which read as fidgeting through a first reply's ten or twenty. So
// "Thinking" holds for the first stretch, which is most first replies; after it
// a phrase stands for nine seconds, and the change is a fade: the words go out
// for AWAIT_FADE_MS before the phrase turns over (`out`, which the stylesheet's
// `.awaiting-words.fade.out` draws) and come back in on the draw that turns it.
// The dot breathes throughout, so the line is alive between phrases.
//
// It touches no DOM and reads no clock: it is given how long the line has stood,
// so a test can ask what it says at any moment without waiting for one.

export const AWAIT_PHRASES = ['Thinking', 'Digging in', 'Turning it over', 'Working it through', 'Gathering the threads', 'Finding the words']
export const AWAIT_FIRST_MS = 12000
export const AWAIT_EVERY_MS = 9000
export const AWAIT_FADE_MS = 400

// What the line says `elapsed` ms after it began, whether its words are on their
// way out, and how long until the next draw is due — the fade or the turn.
export function awaitingPhrase(elapsed) {
  const index = elapsed < AWAIT_FIRST_MS ? 0 : 1 + Math.floor((elapsed - AWAIT_FIRST_MS) / AWAIT_EVERY_MS)
  const turnsAt = AWAIT_FIRST_MS + (index === 0 ? 0 : index * AWAIT_EVERY_MS)
  const left = turnsAt - elapsed
  const out = left <= AWAIT_FADE_MS
  return { phrase: AWAIT_PHRASES[index % AWAIT_PHRASES.length], out, nextMs: out ? left : left - AWAIT_FADE_MS }
}
