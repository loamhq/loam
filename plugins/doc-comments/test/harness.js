// Mount the editor alone against the dev server (test/dev_server.py), as a
// share page would: no Loam page around it. `?phone` draws the phone layout;
// `?dark` the dark tokens; `?zoom=0.9` zooms the page as Loam's Size setting
// does (`body { zoom }`); `?path=` opens another seeded doc.
import { createDocEditor } from '../page/editor/editor.js'
import { docApi } from '../page/api.js'

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
const doc = await api.get()
const ed = createDocEditor({
  api, doc,
  phone: params.has('phone'),
  settings: { get: (k, d) => (store.has(k) ? store.get(k) : d), set: (k, v) => store.set(k, v) },
  crumbs: [{ label: 'Essays', go: () => {} }, { label: 'drafts', go: () => {} }, { label: 'On staying' }],
  back: params.has('phone') ? { label: 'Back', go: () => {} } : null,
  fileMenu: () => {},
  openLink: (ref) => console.log('open', ref),
  embed: picture,
})
document.getElementById('app').append(ed.dom)
window.__ed = ed
