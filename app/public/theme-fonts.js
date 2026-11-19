// Only the active look registers its display face. Inactive cover captions
// use their serif fallback until that look has been selected once.
//
// Since 2026-11-18 the first-party looks beyond Latte and Plain are plugins
// from the Market, so a look's faces are its own folder's `fonts.css`, which
// the app serves checked at /theme-fonts/<id>.css (an empty sheet when the
// theme has none). Latte and Plain use the core faces.
;(function () {
  const core = new Set(['latte', 'plain'])
  const loaded = new Set()
  globalThis.LoamThemeFonts = function (id) {
    if (typeof id !== 'string' || !/^[a-z][a-z0-9-]*$/.test(id) || core.has(id) || loaded.has(id)) return
    loaded.add(id)
    const href = `/theme-fonts/${id}.css`
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
