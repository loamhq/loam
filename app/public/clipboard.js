// Putting text on the clipboard, and the one answer to how.
//
// `navigator.clipboard` is there in every browser this page is opened in behind
// HTTPS and the login, and it is asked first. Where it is absent — a page served
// over plain HTTP to a phone on the same network, an older WebView — the
// fallback is the one every browser still honours: a textarea off the screen,
// its text selected, and `copy`. It is `.style` properties and never a style
// attribute, because the page's policy refuses those.
//
// The answer is whether it worked. The caller says so in its own words; this
// file never draws anything.

export async function copyText(text) {
  const value = String(text ?? '')
  const clip = globalThis.navigator?.clipboard
  if (clip && typeof clip.writeText === 'function') {
    try {
      await clip.writeText(value)
      return true
    } catch {
      // Refused — no focus, no permission — and the old way may still work.
    }
  }
  return legacyCopy(value)
}

function legacyCopy(value) {
  const doc = globalThis.document
  if (!doc?.body || typeof doc.execCommand !== 'function') return false
  const area = doc.createElement('textarea')
  area.value = value
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.top = '-1000px'
  area.style.opacity = '0'
  doc.body.appendChild(area)
  try {
    area.select()
    return doc.execCommand('copy') === true
  } catch {
    return false
  } finally {
    doc.body.removeChild(area)
  }
}
