// A plugin's screenshots (2026-11-19): what it looks like in use, in a row
// under its name, each with its one line, opening larger on a tap. The
// owner, 11-19: "If it's something that's UI-related, it should definitely
// have screenshots of what the UI looks like and how it works." Drawn on a
// Market listing (page/market.js) and a plugin's page in Settings ›
// Plugins (plugin-settings.js), from `screenshots` in its PLUGIN.md.
//
// `shots` is [{ src, src_dark?, caption }], already addresses the page can
// load; the dark one shows when Loam is dark.
import { h } from './ui.js'
import { openImageView } from './image-view.js'

export function pluginGallery({ shots = [], dark = false, name = '', key = 'gallery', failed = new Set(), redraw = () => {} } = {}) {
  const list = shots.map(s => ({ ...s, src: (dark && s.src_dark) || s.src })).filter(s => s.src && !failed.has(s.src))
  if (!list.length) return null
  const open = i => openImageView(list.map(s => ({ src: s.src, name: s.caption || name })), i, { download: false, label: `${name} screenshots` })
  return h('section', { class: `plugin-gallery${list.length === 1 ? ' plugin-gallery-one' : ''}`, 'aria-label': `${name} in use`, key },
    h('ul', { class: 'plugin-gallery-row' }, ...list.map((s, i) => h('li', { class: 'plugin-shot', key: s.src },
      h('button', { type: 'button', class: 'plugin-shot-open', 'aria-label': s.caption ? `${s.caption}: see it larger` : `Screenshot ${i + 1}: see it larger`, onclick: () => open(i) },
        h('img', { class: 'plugin-shot-img', src: s.src, alt: s.caption || '', loading: 'lazy', decoding: 'async', onerror: () => { failed.add(s.src); redraw() } })),
      s.caption ? h('p', { class: 'plugin-shot-caption', text: s.caption }) : null))))
}
