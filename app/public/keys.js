// Plain Enter inserts a newline. Sending requires a modifier or the send button.

// Browsers expose platform information through different fields, especially on iPad.
export function isMac(nav) {
  const said = `${nav?.userAgentData?.platform ?? ''} ${nav?.platform ?? ''} ${nav?.userAgent ?? ''}`
  return /mac|iphone|ipad|ipod/i.test(said)
}

// Both desktop modifiers send on either platform. An IME owns the Enter that commits a candidate.
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
