import { firstUseActive } from './first-use.js'
// "Attach from Files": the picker the composer's "Pick from Files" opens, over
// the chat rather than instead of it, so a draft that has no session yet keeps
// its words while files are picked for it.
//
// It browses the same host routes Files does — `roots`, then `list` — and hands
// back the rows picked, several at once, as references. Nothing here reads a
// file; what the agent may read of one is the host's to decide when the message
// arrives.

import { h, icon } from './ui.js'
import { emptyState } from './empty-state.js'
import { rootsOf, rowsOf, nextOf, isFolder, tileOf, metaOf, splitPath, parentOf, ROOT_LOOK, FILES_LOCATION_HIDDEN } from './files.js'

export function createPicker({ api, schedule }) {
  const st = { open: false, agent: null, onPick: null, root: null, path: '', roots: [], rows: [], cursor: null, loading: false, error: '', selected: new Map(), turn: 0 }

  const q = (o) =>
    Object.entries(o)
      .filter(([, v]) => v !== null && v !== undefined && v !== '')
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
      .join('&')

  function open({ agent = null, onPick }) {
    Object.assign(st, { open: true, agent, onPick, selected: new Map() })
    go(null, '')
  }

  function close() {
    st.open = false
    st.onPick = null
    st.selected = new Map()
    schedule()
  }

  async function go(root, path = '', more = false) {
    const hidden = root === 'system'
    if (hidden) { root = null; path = ''; more = false }
    st.notice = hidden ? FILES_LOCATION_HIDDEN : ''
    const turn = ++st.turn
    if (!more) Object.assign(st, { root, path, rows: [], cursor: null, error: '' })
    st.loading = true
    schedule()
    try {
      if (!root) {
        const body = await api('GET', '/files/roots')
        if (turn !== st.turn) return
        st.roots = rootsOf(body)
      } else {
        const body = await api('GET', `/files/list?${q({ root, path, cursor: more ? st.cursor : null, limit: 200 })}`)
        if (turn !== st.turn) return
        st.rows = more ? [...st.rows, ...rowsOf(body)] : rowsOf(body)
        st.cursor = nextOf(body)
      }
    } catch (err) {
      if (turn !== st.turn) return
      st.error = err.message
    }
    st.loading = false
    schedule()
  }

  function toggle(row) {
    const key = `${row.root}:${row.path}`
    if (st.selected.has(key)) st.selected.delete(key)
    else st.selected.set(key, row)
    schedule()
  }

  function attach() {
    const rows = [...st.selected.values()]
    const onPick = st.onPick
    close()
    if (rows.length > 0) onPick?.(rows)
  }

  function back() {
    if (!st.root) return false
    go(st.path === '' ? null : st.root, parentOf(st.path))
    return true
  }
  return { st, open, close, go, back, toggle, attach }
}

export function pickerSheet(picker, { agentName = null } = {}) {
  const st = picker.st
  if (!st.open) return null
  const rootName = (r) => st.roots.find((x) => x.root === r)?.name ?? ROOT_LOOK[r]?.name ?? r
  const where = st.root ? [rootName(st.root), ...splitPath(st.path)].join(' / ') : null
  const rows = []
  if (st.root) {
    rows.push(
      h(
        'button',
        { class: 'setrow fs-pickup', key: 'pick:up', onclick: picker.back },
        h('div', { class: 'icon-tile fs-foldertile' }, icon.chevronLeft(16)),
        h('div', { class: 'fs-words' }, h('div', { class: 'fs-name', text: st.path === '' ? 'Files' : splitPath(parentOf(st.path)).at(-1) ?? rootName(st.root) }), h('div', { class: 'fs-meta', text: 'Back' })),
      ),
    )
  }
  if (st.notice) rows.push(h('p', { class: 'fs-quiet', role: 'status', text: st.notice }))
  if (st.error) rows.push(h('div', { class: 'banner banner-retry', role: 'alert', text: st.error }))
  else if (st.loading && (st.root ? st.rows.length === 0 : st.roots.length === 0)) rows.push(h('div', { class: 'fs-quiet', text: 'Loading…' }))
  else if (!st.root) {
    for (const r of st.roots) {
      rows.push(
        h(
          'button',
          { class: 'setrow fs-pickroot', key: `pick:root:${r.root}`, onclick: () => picker.go(r.root, '') },
          h('div', { class: 'icon-tile', style: { background: 'var(--paper-card)', color: 'var(--paper-card-ink-2)' } }, icon.folder(18)),
          h('div', { class: 'fs-words' }, h('div', { class: 'fs-name', text: r.name }), h('div', { class: 'fs-meta', text: r.homeDesc || r.desc })),
          icon.chevronRight(10),
        ),
      )
    }
  } else if (st.rows.length === 0) rows.push(emptyState({ object: 'folder', size: 'small', level: 'h3', title: 'Nothing here yet', body: firstUseActive() ? 'Keep files here with your agents.' : null, props: { class: 'fs-quiet' } }))
  else {
    for (const row of st.rows) {
      const key = `${row.root}:${row.path}`
      if (isFolder(row)) {
        rows.push(
          h(
            'button',
            { class: 'setrow', key: `pick:${key}`, onclick: () => picker.go(row.root, row.path) },
            h('div', { class: 'icon-tile fs-foldertile' }, icon.folder(18)),
            h('div', { class: 'fs-words' }, h('div', { class: 'fs-name', text: row.name }), h('div', { class: 'fs-meta', text: metaOf(row) })),
            icon.chevronRight(10),
          ),
        )
        continue
      }
      const on = st.selected.has(key)
      const tile = tileOf(row.name)
      rows.push(
        h(
          'button',
          { class: `setrow fs-pickfile${on ? ' on' : ''}`, key: `pick:${key}`, role: 'checkbox', 'aria-checked': on ? 'true' : 'false', onclick: () => picker.toggle(row) },
          h('div', { class: 'icon-tile', style: { background: tile.bg, color: tile.fg } }, h('span', { text: tile.label })),
          h('div', { class: 'fs-words' }, h('div', { class: 'fs-name', text: row.name }), h('div', { class: 'fs-meta', text: metaOf(row) })),
          h('span', { class: `fs-check${on ? ' on' : ''}` }, on ? icon.check(12) : null),
        ),
      )
    }
    if (st.cursor) rows.push(h('button', { class: 'pillbtn content-mark fs-more', disabled: st.loading, text: 'Show more', onclick: () => picker.go(st.root, st.path, true) }))
  }
  const n = st.selected.size
  return [
    h('div', { class: 'display sheettitle', text: 'Attach from Files' }),
    h('div', { class: 'fs-sub', text: agentName ? `${agentName} gets read access to what you pick.` : 'The agent gets read access to what you pick.' }),
    where ? h('div', { class: 'fs-meta fs-pickwhere', text: where }) : null,
    h('div', { class: 'card card-group fs-picklist scroll' }, ...rows),
    h(
      'div',
      { class: 'fs-pickfoot' },
      h('span', { class: 'fs-meta', text: `${n} selected` }),
      h('div', { class: 'headspace' }),
      h('button', { class: 'pillbtn', text: 'Cancel', onclick: () => picker.close() }),
      h('button', { class: 'primary fs-attach', disabled: n === 0, text: 'Attach', onclick: () => picker.attach() }),
    ),
  ]
}
