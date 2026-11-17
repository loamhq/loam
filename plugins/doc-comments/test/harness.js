// Mount the editor alone against the dev server (test/dev_server.py), as a
// share page would: no Loam page around it. `?phone` draws the phone layout;
// `?dark` the dark tokens.
import { createDocEditor } from '../page/editor/editor.js'
import { docApi } from '../page/api.js'
import { createSharing } from '../page/sharing.js'

const params = new URLSearchParams(location.search)
if (params.has('dark')) document.documentElement.classList.add('dark')
const api = docApi({ base: '/x/doc-comments/', root: 'vault', path: params.get('path') || 'Essays/drafts/On staying.md' })
const store = new Map()
const doc = await api.get()
let ed = null
const sharing = createSharing({ base: '/x/doc-comments/', root: 'vault', path: params.get('path') || 'Essays/drafts/On staying.md', title: () => 'On staying in one place', toast: (t, u) => ed.toast(t, u), onChange: () => ed.refreshHead() })
sharing.links = doc.links
ed = createDocEditor({
  api, doc,
  headEnd: () => [sharing.button()],
  phone: params.has('phone'),
  settings: { get: (k, d) => (store.has(k) ? store.get(k) : d), set: (k, v) => store.set(k, v) },
  crumbs: [{ label: 'Essays', go: () => {} }, { label: 'drafts', go: () => {} }, { label: 'On staying' }],
  back: params.has('phone') ? { label: 'Back', go: () => {} } : null,
  fileMenu: () => {},
  openLink: (ref) => console.log('open', ref),
})
document.getElementById('app').append(ed.dom)
window.__ed = ed
window.__sharing = sharing
