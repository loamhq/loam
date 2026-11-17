// How the owner's agents appear to people outside (2026-11-17): each by its
// own name, or all as one, "<Owner>'s agent". One control, in three places:
// the default at the foot of Shared Docs, a doc's own choice in its share
// sheet, and a doc's own choice in Shared with me (its row menu). The server
// does the hiding (server/doccomments/appear.py); this only asks and says.

import { h, icon } from './editor/dom.js'

export const CHOICES = ['names', 'one']

// The words for a choice, as they finish "Your agents appear …".
export function how(choice, alias) {
  return choice === 'one' ? `as ${alias}` : 'by their own names'
}

// What a reader sees, for the line under a choice: a real agent's name when
// the page knows one ("Iris · Sam's agent" on another Loam, "Iris" on the
// owner's own doc), else plain words.
export function sample(choice, { alias, agent, there }) {
  if (choice === 'one') return alias
  if (!agent) return 'Each by its own name'
  return there ? `${agent} · ${alias}` : agent
}

// A select that reads as part of its sentence (the share sheet's "Their
// browser remembers it for …" look). `withDefault` adds "as usual (…)".
export function appearSelect({ value, def, alias, onChange, withDefault = false, label }) {
  const opts = []
  if (withDefault) opts.push(['default', `as usual (${how(def, alias)})`])
  opts.push(['names', how('names', alias)], ['one', how('one', alias)])
  const s = h('select', { class: 'dc-ap-sel', 'aria-label': label || 'How your agents appear' },
    opts.map(([v, t]) => h('option', { value: v, selected: v === value ? true : null }, t)))
  const shown = h('span', { class: 'dc-ap-shown', text: (opts.find(o => o[0] === value) || opts[0])[1] })
  s.addEventListener('change', () => { shown.textContent = (opts.find(o => o[0] === s.value) || opts[0])[1]; onChange(s.value) })
  // The words chosen, drawn as words, with the select laid over them: a
  // select is as wide as its longest choice, which left a gap before the
  // chevron. The select still takes the keyboard and the phone's picker.
  return h('span', { class: 'dc-ap-pick' }, shown, icon('chevronDown', 12), s)
}

// The sheet for one doc in Shared with me: three plain choices, and the one
// quiet line that matters there (what was sent is theirs now).
export function openAppearSheet({ title, there, value, def, alias, agent, onChoose }) {
  const scrim = h('div', { class: 'dc-dlg-scrim dc-wm-scrim dc-ap-scrim' })
  let current = value
  const close = () => {
    scrim.classList.remove('on')
    document.removeEventListener('keydown', onKey)
    setTimeout(() => scrim.remove(), 240)
  }
  const onKey = (e) => { if (e.key === 'Escape') close() }
  scrim.addEventListener('click', (e) => { if (e.target === scrim) close() })
  const rows = [
    ['default', 'As usual', `${how(def, alias).replace(/^./, c => c.toUpperCase())}, as set in Shared Docs`],
    ['names', 'By their own names', sample('names', { alias, agent, there: true })],
    ['one', 'As one', alias],
  ]
  const list = h('div', { class: 'dc-ap-list', role: 'radiogroup', 'aria-label': 'How your agents appear' })
  const draw = () => list.replaceChildren(...rows.map(([v, b, small]) => h('button', {
    class: `dc-mi dc-ap-opt${v === current ? ' on' : ''}`, type: 'button', role: 'radio', 'aria-checked': v === current ? 'true' : 'false',
    onclick: async () => {
      if (v === current) { close(); return }
      const was = current
      current = v
      draw()
      const ok = await onChoose(v)
      if (ok === false) { current = was; draw(); return }
      setTimeout(close, 160)
    },
  }, h('span', { class: 'dc-l' }, h('b', { text: b }), h('small', { text: small })), h('span', { class: 'dc-ck' }, icon('check', 16)))))
  draw()
  scrim.append(h('div', { class: 'dc-dlg dc-wm-dlg dc-ap-dlg', role: 'dialog', 'aria-label': 'How your agents appear' },
    h('div', { class: 'dc-wm-grab', 'aria-hidden': 'true' }),
    h('div', { class: 'dc-sh-head' },
      h('h2', { text: 'How your agents appear' }),
      h('button', { class: 'dc-ib', type: 'button', 'aria-label': 'Close', onclick: close }, icon('x', 18))),
    h('p', { class: 'dc-wm-lead', text: `What your agents write on “${title}” shows on ${there} as:` }),
    list,
    h('p', { class: 'dc-sh-cap dc-ap-note', text: 'What they have already written there keeps the name it was sent with.' })))
  document.body.append(scrim)
  document.addEventListener('keydown', onKey)
  requestAnimationFrame(() => scrim.classList.add('on'))
  setTimeout(() => list.querySelector('.on')?.focus(), 60)
  return close
}
