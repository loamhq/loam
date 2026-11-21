// The framed theme scene, its options and its palette. Market listings keep
// preview choices here. Installed themes in Settings supply account settings.
//
// What a theme says (`options`, `palette` from its PLUGIN.md) is all it
// takes. Its palette is `{token, name, name_dark?}`; a theme that names none
// shows six of its roles by their plain names. The colours are read from the
// scene itself, in the mode shown, so they are the theme's own.
import { h, kept } from './ui.js'
import { segmentedControl } from './chat-ui.js'
import { shownOptions } from './theme-options.js'

export const PLAIN_PALETTE = [
  { token: '--paper-page', name: 'Page' }, { token: '--paper-pane', name: 'Pane' }, { token: '--paper-card', name: 'Card' },
  { token: '--paper-ink', name: 'Ink' }, { token: '--latte-espresso', name: 'Accent' }, { token: '--signal-crema', name: 'Signal' },
]
const MODES = [{ id: 'light', label: 'Light' }, { id: 'dark', label: 'Dark' }]

// The hex of a colour as the browser computes it (its red, green and blue
// function), with its alpha when not opaque. The pattern is built from parts
// so the colour guard (test/themes.test.js) reads no literal colour here.
const COMPUTED = new RegExp(['^', 'rgb', 'a?\\(\\s*(\\d+)[,\\s]+(\\d+)[,\\s]+(\\d+)(?:[,\\s/]+([\\d.]+%?))?\\s*\\)$'].join(''))
export function hexOf(css) {
  const m = COMPUTED.exec(String(css ?? '').trim())
  if (!m) return null
  const hex = '#' + m.slice(1, 4).map(n => Math.max(0, Math.min(255, +n)).toString(16).padStart(2, '0')).join('').toUpperCase()
  const a = m[4] == null ? 1 : m[4].endsWith('%') ? parseFloat(m[4]) / 100 : +m[4]
  return a >= 1 ? hex : `${hex} · ${Math.round(a * 100)}%`
}

export function createThemeShowcase({ schedule = () => {}, mode = () => 'light' } = {}) {
  const shows = new Map()
  globalThis.addEventListener?.('message', event => {
    if (event.origin !== location.origin || !event.data?.loamScene) return
    for (const show of shows.values()) if (show.frame.contentWindow === event.source) read(show)
  })
  function read(show) {
    try {
      const scene = show.frame.contentWindow?.loamScene
      const ready = scene?.ready()
      if (!ready) return
      show.colours = scene.colours(show.tokens)
      show.ready = ready
      show.loaded = true
      schedule()
    } catch { /* a frame between pages answers nothing; the next ready will */ }
  }
  function showOf(key) {
    let show = shows.get(key)
    if (!show) {
      const frame = document.createElement('iframe')
      frame.className = 'theme-show-frame'
      frame.setAttribute('tabindex', '-1')
      frame.setAttribute('scrolling', 'no')
      frame.setAttribute('inert', '')
      frame.addEventListener('load', () => read(show))
      show = { frame, mode: mode(), values: {}, colours: {}, tokens: [], loaded: false, ready: null }
      shows.set(key, show)
    }
    return show
  }
  function address(look, displayMode, from, values) {
    const q = new URLSearchParams({ look, mode: displayMode, from })
    for (const [id, value] of Object.entries(values)) q.set(`o.${id}`, value)
    return `/theme-scene.html#${q}`
  }
  // `key` names one preview (a listing, a plugin page); `from` is where its
  // tokens come from: `market` (the listing's link) or `installed`.
  function view({ key, look, name, options = [], palette = [], from = 'market', settings }) {
    const show = showOf(key)
    const named = palette.length ? palette : PLAIN_PALETTE
    show.tokens = named.map(p => p.token)
    const displayMode = settings ? mode() : show.mode
    const choices = settings ? settings.options : show.values
    const values = Object.fromEntries(options.map(o => [o.id, choices?.[o.id] ?? o.default]))
    const src = address(look, displayMode, from, values)
    if (show.src !== src) {
      show.src = src
      if (!show.frame.getAttribute('src')) show.frame.setAttribute('src', src)
      else show.frame.contentWindow?.location.replace(src)
    }
    show.frame.title = `${name} in Loam, ${displayMode}`
    const set = patch => { Object.assign(show, patch); schedule() }
    const want = `${look}:${displayMode}:`
    const current = show.loaded && show.ready?.startsWith(want)
    return h('section', { class: 'theme-show', 'aria-label': `${name}, previewed`, key: `show:${key}` },
      h('div', { class: `theme-show-stage${show.loaded ? '' : ' is-loading'}`, role: 'img', 'aria-label': `Loam wearing ${name}, in ${displayMode} mode` }, kept(show.frame)),
      h('div', { class: 'theme-show-controls' },
        h('div', { class: 'theme-show-control' }, h('span', { class: 'theme-show-label', id: `${key}-mode`, text: 'Mode' }),
          segmentedControl({ label: 'Mode', options: MODES, value: settings?.mode ?? show.mode,
            choose: id => settings ? settings.set({ mode: id }) : set({ mode: id }), props: { class: 'theme-show-choices', 'aria-labelledby': `${key}-mode` } })),
        ...shownOptions(options, values).map(option => h('div', { class: 'theme-show-control', key: option.id },
          h('span', { class: 'theme-show-label', id: `${key}-o-${option.id}`, text: option.label }),
          segmentedControl({ label: option.label, options: option.choices, value: values[option.id],
            choose: id => settings ? settings.set({ options: { [look]: { [option.id]: id } } }) : set({ values: { ...show.values, [option.id]: id } }),
            props: { class: 'theme-show-choices', 'aria-labelledby': `${key}-o-${option.id}` } })))),
      h('ul', { class: `theme-palette${current ? '' : ' is-loading'}`, 'aria-label': `${name}’s colours in ${displayMode} mode` },
        ...named.map(p => {
          const colour = show.colours[p.token], hex = current ? hexOf(colour) : null
          const label = (displayMode === 'dark' && p.name_dark) || p.name
          return h('li', { class: 'theme-swatch', key: p.token, tabindex: '0', title: hex ?? '', 'aria-label': hex ? `${label}, ${hex}` : label },
            h('span', { class: 'theme-swatch-chip', 'aria-hidden': 'true', style: { background: current && colour ? colour : '' } }),
            h('span', { class: 'theme-swatch-name', text: label }),
            h('span', { class: 'theme-swatch-hex', 'aria-hidden': 'true', text: hex ?? '' }))
        })))
  }
  function drop(key) { shows.delete(key) }
  return { view, drop }
}
