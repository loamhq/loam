import { themeImage } from './theme-image.js'
import { authenticatedFetch as fetch } from './auth.js'
import { validateThemeOptions } from './theme-css.js'
export function createPluginThemes({ theme, failed = () => {}, request = fetch }) {
  let shipped = null, previous = null, revision = 0, pending = null
  return async rows => {
    const candidates = rows.filter(row => row.effective && !row.broken && row.kind?.includes('theme'))
    if (!candidates.length) return // A missing catalog keeps the built-in fallback.
    if (!shipped) {
      try { const response = await request('/shipped-plugins.json'); const body = await response.json(); shipped = body.plugins.filter(row => row.kind?.includes('theme')).map(row => ({ id: row.id, name: row.name, author: row.author, version: row.version, ...themeImage(row), ...(row.options ? { options: validateThemeOptions(row.options, row.id) } : {}) })) }
      catch { shipped = [{ id: 'latte', name: 'Latte & Newspaper' }, { id: 'plain', name: 'Plain' }] }
    }
    const catalog = []
    for (const row of candidates) {
      try {
        if (!Number.isInteger(row.api) || row.api > 0) throw new Error(`Unsupported theme API: ${row.api}`)
        const options = validateThemeOptions((row.layer === 'shipped' ? shipped.find(t => t.id === row.id)?.options : undefined) ?? row.options, row.id)
        catalog.push({ id: row.id, name: row.name, author: row.author, version: row.version, layer: row.layer, ...themeImage({ ...(row.layer === 'shipped' ? shipped.find(t => t.id === row.id) : {}), ...row }), modes: ['light', 'dark'], ...(options.length ? { options } : {}) }); failed(row.id, null)
      } catch (error) { failed(row.id, error) }
    }
    for (const id of ['latte', 'plain']) if (!catalog.some(row => row.id === id)) catalog.push(shipped.find(row => row.id === id) ?? { id, name: id === 'latte' ? 'Latte & Newspaper' : 'Plain' })
    catalog.sort((a,b) => a.id === 'latte' ? -1 : b.id === 'latte' ? 1 : a.name.localeCompare(b.name))
    const signature = JSON.stringify(catalog)
    if (signature === previous) return
    previous = signature
    const link = document.querySelector('link[href^="/themes.css"]')
    if (link) {
      // Changing the loaded link's href removes its CSS while the replacement
      // is in flight, exposing the core defaults. Keep it until the next sheet
      // has loaded; a failed refresh leaves the current theme intact.
      pending?.remove()
      const next = document.createElement('link')
      next.rel = 'stylesheet'
      next.href = `/themes.css?plugins=${++revision}`
      next.onload = () => {
        if (pending !== next) return
        pending = null; link.remove(); theme.apply()
      }
      next.onerror = () => { if (pending === next) pending = null; next.remove() }
      pending = next
      link.after(next)
    }
    theme.install(catalog); theme.apply()
  }
}
