// Browsers expose platform information through different fields, especially on iPad.
export function isMac(nav) {
  const said = `${nav?.userAgentData?.platform ?? ''} ${nav?.platform ?? ''} ${nav?.userAgent ?? ''}`
  return /mac|iphone|ipad|ipod/i.test(said)
}

// Mobile browsers do not reliably distinguish hardware Enter from the onscreen
// Return key. Keep Return available for new lines on those browsers.
export function mobileKeyboard(nav) {
  return nav?.userAgentData?.mobile === true || /android|iphone|ipad|ipod/i.test(nav?.userAgent ?? '')
    || (isMac(nav) && nav?.maxTouchPoints > 1)
}

export function composing(event) {
  return event.isComposing || event.keyCode === 229
}

export function sends(event, mode = 'enter', mobile = false) {
  if (!event || event.key !== 'Enter' || composing(event) || event.shiftKey || event.altKey || event.repeat) return false
  if (event.metaKey || event.ctrlKey) return true
  return mode === 'enter' && !mobile
}

export function sendHint(mac, mode = 'enter', mobile = false) {
  if (mobile) return 'Return adds a new line'
  if (mode === null) return ''
  return mode === 'enter' ? 'Enter to send · Shift+Enter for a new line' : mac ? '⌘↩ to send' : 'Ctrl+↩ to send'
}

export function composerKeys({ api, changed, nav = globalThis.navigator, initial }) {
  let value = initial, writing = false, error = '', revision = 0
  const mac = isMac(nav), mobile = mobileKeyboard(nav)
  async function refresh() {
    if (writing) return
    const ticket = ++revision
    try {
      const result = await api('GET', '/settings/composer')
      if (ticket !== revision || !['enter', 'modifier'].includes(result.send_key)) return
      if (value !== result.send_key) { value = result.send_key; changed() }
    } catch { /* Retain the last known preference when disconnected. */ }
  }
  async function set(mode) {
    if (writing || value === mode) return
    revision++
    writing = true
    error = ''
    changed()
    try { value = (await api('PATCH', '/settings/composer', { send_key: mode })).send_key }
    catch (e) { error = e.message }
    finally { writing = false; changed() }
  }
  return { get value() { return value }, get writing() { return writing }, get error() { return error },
    get hint() { return sendHint(mac, value, mobile) },
    get modifierLabel() { return mac ? '⌘ + Enter' : 'Ctrl + Enter' },
    sends: event => sends(event, value, mobile), refresh, set }
}
