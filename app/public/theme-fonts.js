// Only the active first-party look registers its display face. Inactive cover
// captions use their serif fallback until that look has been selected once.
;(function () {
  const looks = new Set(['deep-forest', 'autumn', 'yosemite', 'mist-trail', 'foundry'])
  const loaded = new Set()
  globalThis.LoamThemeFonts = function (id) {
    if (!looks.has(id) || loaded.has(id)) return
    loaded.add(id)
    const href = `/themes/${id}/fonts.css`
    if (document.readyState === 'loading') {
      // Insert in the parser's path so saved themes have their font rules
      // before first paint, under the same self-only stylesheet policy.
      document.write(`<link rel="stylesheet" href="${href}">`)
    } else {
      const link = document.createElement('link')
      link.rel = 'stylesheet'; link.href = href
      link.onerror = () => { loaded.delete(id); link.remove() }
      document.head.append(link)
    }
  }
})()
