// What the Enter key does, and what to call the key that sends.
//
// **Enter makes a new line. It never sends.** The owner, 2026-09-22: "When I
// press enter on keyboard it should go newline and not send message. On desktop
// we can have enter with another key send the message". A composer whose Enter
// sends is a composer a person cannot write a paragraph in, and on a phone —
// where the on-screen return key is the only thing between two thoughts — it is
// a message sent half-written.
//
// So sending is a deliberate act with a modifier held, or the send button, and
// nothing else. This file is the decision on its own: no DOM, no `navigator`
// read at import time, nothing but the event's own fields in and a boolean out,
// so the rule can be asserted in `node --test` without a keyboard anywhere near
// it.

// Whether this machine calls the modifier ⌘. Takes the `navigator` rather than
// reading the global, so a test can hand it a Mac and a not-Mac in one process.
//
// `userAgentData.platform` first, because it is the one a browser still answers
// truthfully; `platform` is deprecated and frozen but present everywhere; the
// user agent string is the last resort and catches the iPads that claim to be
// Macs and the Macs that claim to be nothing.
export function isMac(nav) {
  const said = `${nav?.userAgentData?.platform ?? ''} ${nav?.platform ?? ''} ${nav?.userAgent ?? ''}`
  return /mac|iphone|ipad|ipod/i.test(said)
}

// Does this keydown send the message?
//
// Ctrl+Enter and Cmd+Enter both do, on either platform: one of the two is the
// idiom wherever a person has come from, and accepting the other costs nothing
// and surprises nobody. What is *not* accepted is Enter on its own, Shift+Enter
// or Alt+Enter — all three are a new line, which is what the key is for.
//
// An Enter that ends an input method's composition — picking a character from
// the candidate list a Japanese or Chinese keyboard puts up — is that keyboard's
// own Enter and never this app's, modifier or not.
export function sends(event) {
  if (!event || event.key !== 'Enter') return false
  if (event.isComposing || event.keyCode === 229) return false
  return Boolean(event.metaKey) || Boolean(event.ctrlKey)
}

// What the desktop's hint line calls it. The glyphs are the keys as they are
// printed on the machine in front of the person: ⌘↩ on a Mac, Ctrl+↩ anywhere
// else. Both chords work on both; the hint names the one that will not look
// foreign.
export function sendHint(mac) {
  return mac ? '⌘↩ to send' : 'Ctrl+↩ to send'
}
