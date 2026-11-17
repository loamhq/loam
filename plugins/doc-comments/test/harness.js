// Mount the editor alone against the dev server (test/dev_server.py), as a
// share page would: no Loam page around it. `?phone` draws the phone layout;
// `?dark` the dark tokens; `?zoom=0.9` zooms the page as Loam's Size setting
// does (`body { zoom }`); `?path=` opens another seeded doc. `?mic` gives the
// editor stand-ins for what Loam's page gives it: read aloud's button and
// dictation's mic (a field the "dictation" writes through, as Loam's does,
// and a take that stops when the doc stops taking typed text); `?can=view`
// opens the doc as a view link does, with no right to write.
import { createDocEditor } from '../page/editor/editor.js'
import { docApi } from '../page/api.js'
import { createSharing } from '../page/sharing.js'

const params = new URLSearchParams(location.search)
if (params.has('dark')) document.documentElement.classList.add('dark')
if (params.get('zoom')) document.body.style.zoom = params.get('zoom')
const api = docApi({ base: '/x/doc-comments/', root: 'vault', path: params.get('path') || 'Essays/drafts/On staying.md' })
const store = new Map()
// A picture of a known size that arrives late, as a real embed does.
function picture(name) {
  const img = new Image()
  const [w, hgt] = /wide/.test(name) ? [640, 280] : [480, 320]
  img.src = `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${hgt}"><rect width="100%" height="100%" fill="#B4733A" opacity=".35"/></svg>`)}`
  img.alt = name
  return img
}
let doc = await api.get()
if (params.get('can') === 'view') doc = { ...doc, can: { comment: false, suggest: false, edit: false, accept: false, resolve_any: false } }
const withMic = params.has('mic')
// Dictation as Loam's page runs it, in small: a take writes through the
// field (main.js's fieldFor) until the editor says the doc stopped taking
// typed text (onCanType), which ends it as `field.closed` does.
const take = { on: false, stopped: 0 }
const field = {
  get value() { return ed.text() },
  set value(v) { if (ed.canType() && v !== ed.text()) ed.replaceText(v) },
}
const mic = () => {
  const b = document.createElement('button')
  b.className = 'iconbtn mic'
  b.setAttribute('aria-label', 'Dictate')
  b.textContent = '🎤'
  b.onclick = () => { take.on = true }
  return b
}
const readAloud = () => {
  const b = document.createElement('button')
  b.className = 'iconbtn read-aloud'
  b.setAttribute('aria-label', 'Read aloud')
  b.textContent = '🔊'
  return b
}
window.__take = take
// What a running take does with its words: put them in at the end.
window.__dictate = (words) => { if (take.on) field.value = field.value + words }
let ed = null
const sharing = createSharing({ base: '/x/doc-comments/', root: 'vault', path: params.get('path') || 'Essays/drafts/On staying.md', title: () => 'On staying in one place', toast: (t, u) => ed.toast(t, u), onChange: () => ed.refreshHead() })
sharing.links = doc.links
ed = createDocEditor({
  api, doc,
  headEnd: () => [sharing.button()],
  phone: params.has('phone'),
  settings: { get: (k, d) => (store.has(k) ? store.get(k) : d), set: (k, v) => store.set(k, v) },
  // as Loam's Files gives them: the folders, then the doc's name
  crumbs: (params.get('path') || 'Essays/drafts/On staying.md').replace(/\.md$/, '').split('/').map((label, i, all) => (i < all.length - 1 ? { label, go: () => {} } : { label })),
  back: params.has('phone') ? { label: 'Back', go: () => {} } : null,
  fileMenu: () => {},
  openLink: (ref) => console.log('open', ref),
  embed: picture,
  ...(withMic ? {
    headExtras: () => [readAloud()],
    mic,
    micInHead: () => !params.has('phone'),
    onCanType: (yes) => { if (!yes && take.on) { take.on = false; take.stopped += 1 } },
  } : {}),
})
document.getElementById('app').append(ed.dom)
window.__ed = ed
window.__sharing = sharing
