// A theme's preview (2026-11-19): Loam in miniature, wearing one look, for
// the Market's listing of a theme and a theme's page in Settings › Plugins.
// The owner, 11-19: "a preview type thing within the description of the
// theme and the different color schemas that it has so users can see it
// without having to try it or install it."
//
// It is a page of its own, framed (theme-scene.html), because a look is
// written against the root (`:root[data-look][data-theme][data-option-…]`):
// in a frame the real rules apply exactly as they do when the theme is on,
// faces, materials and fonts included, and nothing reaches the page around
// it. The frame's address says what to wear, after the `#`:
//
//   look=<id>  mode=light|dark  from=market|installed  o.<option>=<choice>
//
// `from=market` loads the listing's tokens as the app has checked them
// (`/api/marketplace/<id>/theme.css`, tokens only, nothing fetched) and its
// faces (`…/fonts.css`, @font-face over its own files); `installed` loads
// the installed looks (`/themes.css`) and `/theme-fonts/<id>.css`. A new
// address re-dresses the scene without reloading it. The page that frames
// it reads its colours through `globalThis.loamScene.colours(tokens)`.
//
// The world in it is invented: agents, a thread and a note that belong to
// nobody. At a frame narrower than WIDE the scene is a phone's thread.
import { h, blob, icon } from './ui.js'
import { agentLook } from './sessions.js'

const WIDE = 600
const SCENE_W = 880
const AGENTS = [
  { id: 'sprout', name: 'Sprout', initial: 'S' },
  { id: 'quill', name: 'Quill', initial: 'Q' },
  { id: 'wren', name: 'Wren', initial: 'W' },
  { id: 'juniper', name: 'Juniper', initial: 'J' },
]
const roster = { list: AGENTS, byId: new Map(AGENTS.map(a => [a.id, a])) }
const face = (id, size, font) => blob(agentLook(id, roster), { size, font, inline: true })

const root = document.documentElement
const mount = document.getElementById('scene')
const sheets = new Map()
function sheet(key, href) {
  for (const [k, link] of sheets) if (k.startsWith(key.split(':')[0] + ':') && k !== key) { link.remove(); sheets.delete(k) }
  if (sheets.has(key)) return sheets.get(key).ready
  const link = document.createElement('link')
  link.rel = 'stylesheet'; link.href = href
  link.ready = new Promise(resolve => { link.onload = resolve; link.onerror = resolve })
  document.head.append(link)
  sheets.set(key, link)
  return link.ready
}

function read() {
  const q = new URLSearchParams(location.hash.slice(1))
  const look = /^[a-z][a-z0-9-]*$/.test(q.get('look') ?? '') ? q.get('look') : 'latte'
  const options = {}
  for (const [k, v] of q) if (k.startsWith('o.') && /^[a-z][a-z0-9-]*$/.test(k.slice(2)) && /^[a-z0-9-]+$/.test(v)) options[k.slice(2)] = v
  return { look, mode: q.get('mode') === 'dark' ? 'dark' : 'light', from: q.get('from') === 'market' ? 'market' : 'installed', options }
}

async function dress() {
  const { look, mode, from, options } = read()
  const core = look === 'latte' || look === 'plain'
  const waits = [sheet(`theme:${from}:${look}`, from === 'market' ? `/api/marketplace/${look}/theme.css` : '/themes.css')]
  if (!core) waits.push(sheet(`fonts:${from}:${look}`, from === 'market' ? `/api/marketplace/${look}/fonts.css` : `/theme-fonts/${look}.css`))
  await Promise.all(waits)
  for (const a of [...root.attributes]) if (a.name.startsWith('data-option-')) root.removeAttribute(a.name)
  for (const [k, v] of Object.entries(options)) root.setAttribute(`data-option-${k}`, v)
  root.dataset.look = look
  root.dataset.theme = mode
  draw()
  await document.fonts?.ready
  root.dataset.ready = `${look}:${mode}:${Object.entries(options).map(([k, v]) => `${k}=${v}`).join(',')}`
  parent?.postMessage?.({ loamScene: 'ready', ready: root.dataset.ready }, location.origin)
}

// --------------------------------------------------------------- the scene
const step = text => h('div', { class: 'sc-steps' }, icon.check(12), h('span', { text }), icon.chevronDown(11))
function thread({ phone }) {
  return h('div', { class: 'sc-thread' },
    h('div', { class: 'sc-msg-you' }, h('p', { class: 'say user', text: 'Could we plan the spring garden? Somewhere for tomatoes, and a corner for sunflowers.' })),
    h('div', { class: 'sc-msg' },
      h('div', { class: 'sc-who' }, face('sprout', 22, 10), h('span', { class: 'sc-name', text: 'Sprout' })),
      phone ? null : step('2 steps'),
      h('p', { class: 'sc-text', text: 'Gladly. Three beds along the fence get the longest light: tomatoes and beans in the first, herbs by the path, and the sunflowers at the back where they won’t shade anything.' }),
      h('div', { class: 'sc-card' },
        h('span', { class: 'sc-card-icon' }, icon.file(16)),
        h('span', { class: 'sc-card-words' }, h('span', { class: 'sc-card-title', text: 'Spring garden plan' }), h('span', { class: 'sc-card-sub', text: 'Garden / plan.md · 3 beds, 14 plants' })),
        h('span', { class: 'sc-chip', text: 'Draft' }))),
    h('div', { class: 'sc-msg' },
      h('div', { class: 'sc-who' }, face('quill', 22, 10), h('span', { class: 'sc-name', text: 'Quill' })),
      h('p', { class: 'sc-text', text: 'I added a watering note to the board.' })))
}
function composer() {
  return h('div', { class: 'sc-composer' },
    h('span', { class: 'sc-typed', text: 'And a bench by the herbs?' }),
    h('span', { class: 'sc-composer-row' },
      h('span', { class: 'sc-tool' }, icon.plus(14)),
      h('span', { class: 'sc-tool sc-model', text: 'Agent’s default' }),
      h('span', { class: 'sc-send' }, icon.send(14))))
}
function wide() {
  const row = (title, sub, look, on = false) => h('div', { class: `sc-row${on ? ' on' : ''}` },
    look ? face(look, 32, 14) : h('span', { class: 'sc-row-icon' }, icon.users(16)),
    h('span', { class: 'sc-row-words' }, h('span', { class: 'sc-row-title', text: title }), sub ? h('span', { class: 'sc-row-sub', text: sub }) : null),
    on ? null : h('span', { class: 'sc-row-time', text: 'Now' }))
  return h('div', { class: 'sc-wide' },
    h('nav', { class: 'sc-rail' },
      h('img', { class: 'sc-brand', src: '/icon.svg', alt: '' }),
      h('span', { class: 'sc-tab on' }, icon.chat(15), h('span', { text: 'Chats' })),
      h('span', { class: 'sc-tab' }, icon.folder(15), h('span', { text: 'Files' })),
      h('span', { class: 'sc-tab' }, icon.apps(15), h('span', { text: 'Apps' }))),
    h('aside', { class: 'sc-list' },
      h('div', { class: 'sc-list-head' }, h('span', { class: 'display sc-title', text: 'Chats' }), h('span', { class: 'sc-list-tools' }, icon.pencil(13), icon.search(13))),
      h('div', { class: 'sc-faces' }, ...AGENTS.map(a => h('span', { class: 'sc-face' }, face(a.id, 34, 15), h('span', { text: a.name })))),
      h('p', { class: 'kicker sc-kicker', text: 'Pinned' }),
      row('Spring garden', 'Sprout replied', null, true),
      h('p', { class: 'kicker sc-kicker', text: 'Recent' }),
      row('Books for winter', null, 'quill'),
      row('A slower Sunday', null, 'wren')),
    h('main', { class: 'pane sc-pane' },
      h('header', { class: 'sc-head' }, h('span', { class: 'sc-row-icon' }, icon.users(15)), h('span', { class: 'display sc-thread-title', text: 'Spring garden' })),
      thread({ phone: false }),
      composer()))
}
function phone() {
  return h('div', { class: 'sc-phone' },
    h('main', { class: 'pane sc-pane' },
      h('header', { class: 'sc-head' }, h('span', { class: 'sc-back' }, icon.chevronLeft(16)), h('span', { class: 'display sc-thread-title', text: 'Spring garden' }), face('sprout', 26, 12)),
      thread({ phone: true }),
      composer()))
}

let shape = null
function draw() {
  const narrow = innerWidth < WIDE
  shape = narrow ? 'phone' : 'wide'
  mount.replaceChildren(narrow ? phone() : wide())
  fit()
}
function fit() {
  const narrow = innerWidth < WIDE
  if ((narrow ? 'phone' : 'wide') !== shape) return draw()
  root.style.setProperty('--scene-scale', narrow ? '1' : String(innerWidth / SCENE_W))
}

// A colour of the scene, as the browser has it in the mode shown: each token
// painted on a probe and read back, so `var()` chains and mixes resolve.
function colours(tokens) {
  const probe = document.createElement('span')
  probe.style.cssText = 'position:absolute;width:1px;height:1px;opacity:0;pointer-events:none'
  document.body.append(probe)
  const out = {}
  for (const token of tokens) {
    if (!/^--[a-z][a-z0-9-]*$/.test(token)) continue
    probe.style.backgroundColor = ''
    probe.style.backgroundColor = `var(${token})`
    out[token] = getComputedStyle(probe).backgroundColor
  }
  probe.remove()
  return out
}
globalThis.loamScene = { colours, ready: () => root.dataset.ready ?? null }

addEventListener('hashchange', dress)
addEventListener('resize', fit)
dress()
