// Public `share.collaboration` v1 share-page module.
// Dynamically imported by Sharing's standalone `/s/<token>` page when Comments
// is enabled. Never assumes anything about Sharing's internals: receives only
// its API base URL, headers, callbacks and DOM container.

import { createDocEditor } from './editor/editor.js'
import { createCommentsPanel } from './panel.js'
import { docApi } from './api.js'
import { h, icon } from './editor/dom.js'

export async function mountMarkdown({
  container,
  apiBase,
  headers = {},
  title = '',
  owner = '',
  store = null,
  headEnd = () => [],
  onGone = () => {},
}) {
  const api = docApi({ base: apiBase, headers })
  const doc = await api.get()
  const phone = matchMedia('(max-width: 760px)')
  const ed = createDocEditor({
    api,
    doc,
    phone: phone.matches,
    settings: store,
    titleBar: { title, sub: owner ? `${owner}’s file` : '' },
    history: false,
    onGone,
    headEnd,
    openLink: (ref) => { if (/^https?:/i.test(ref)) window.open(ref, '_blank', 'noopener,noreferrer') },
  })
  const onMedia = () => ed.setPhone(phone.matches)
  phone.addEventListener('change', onMedia)
  container.replaceChildren(ed.dom)

  // "N new since your last visit" pill
  if (store) {
    const last = store.get('last', null)
    const fresh = []
    if (last) {
      for (const n of ed.notes()) {
        if (n.state !== 'open') continue
        const mine = (a) => a?.id === doc.me?.id
        if (n.created > last && !mine(n.author)) fresh.push(n.id)
        else if ((n.replies || []).some(r => r.created > last && !mine(r.author))) fresh.push(n.id)
      }
    }
    store.set('last', new Date().toISOString())
    if (fresh.length) {
      let i = -1
      const label = h('span', { text: `${fresh.length} new since your last visit` })
      const pill = h('div', { class: 'dc-newpill' },
        h('button', {
          class: 'dc-newstep', type: 'button',
          onclick: () => { i = (i + 1) % fresh.length; ed.focusNote(fresh[i]); label.textContent = `${i + 1} of ${fresh.length} new` },
        }, h('i'), label, icon('chevronDown', 12)),
        h('button', { class: 'dc-newx', type: 'button', 'aria-label': 'Dismiss', onclick: () => ed.setNotice(null) }, icon('x', 12)))
      ed.setNotice(pill)
    }
  }

  return {
    doc,
    destroy() {
      phone.removeEventListener('change', onMedia)
      ed.destroy()
    },
  }
}

export async function mountPanel({
  container,
  apiBase,
  headers = {},
  onGone = () => {},
  onCount = null,
}) {
  const api = docApi({ base: apiBase, headers })
  const doc = await api.get()
  const panel = createCommentsPanel({ api, initial: doc, onGone, onCount })
  if (container) container.replaceChildren(panel.dom)
  return {
    doc,
    dom: panel.dom,
    openSheet: () => panel.openSheet(),
    destroy: () => panel.destroy(),
    openCount: () => panel.openCount(),
  }
}
