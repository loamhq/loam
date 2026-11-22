// Choose how the owner's agents appear to people outside Loam.
// The server enforces the choice on outgoing collaboration responses.

import { bottomSheet, grab } from './sheet.js'
import { h, icon } from './editor/dom.js'

export const CHOICES = ['names', 'one']

export function how(choice, alias) {
  return choice === 'one' ? `as ${alias}` : 'by their own names'
}

export function sample(choice, { alias, agent, there }) {
  if (choice === 'one') return alias
  if (!agent) return 'Each by its own name'
  return there ? `${agent} · ${alias}` : agent
}

export function wordSelect({ options, value, onChange, label, className = '' }) {
  const words = (v) => (options.find(o => o[0] === v) || options[0])[1]
  const s = h('select', { class: 'dc-ap-sel', 'aria-label': label },
    options.map(([v, t]) => h('option', { value: v, selected: v === value ? true : null }, t)))
  const shown = h('span', { class: 'dc-ap-shown', text: words(value) })
  s.addEventListener('change', () => { shown.textContent = words(s.value); onChange(s.value) })
  return h('span', { class: `dc-ap-pick${className ? ` ${className}` : ''}` }, shown, icon('chevronDown', 12), s)
}

export function appearSelect({ value, def, alias, onChange, withDefault = false, label }) {
  const options = []
  if (withDefault) options.push(['default', `as usual (${how(def, alias)})`])
  options.push(['names', how('names', alias)], ['one', how('one', alias)])
  return wordSelect({ options, value, onChange, label: label || 'How your agents appear' })
}

export function openAppearSheet({ title, there, value, def, alias, agent, onChoose }) {
  const scrim = h('div', { class: 'dc-dlg-scrim dc-wm-scrim dc-ap-scrim' })
  let current = value
  let closed = false
  const close = () => {
    if (closed) return
    closed = true
    bs.dispose()
    scrim.classList.remove('on')
    document.removeEventListener('keydown', onKey)
    setTimeout(() => scrim.remove(), 320)
  }
  const bs = bottomSheet(scrim, { close })
  const onKey = (e) => { if (e.key === 'Escape') close() }
  scrim.addEventListener('click', (e) => { if (e.target === scrim) close() })
  const rows = [
    ['default', 'As usual', `${how(def, alias).replace(/^./, c => c.toUpperCase())}, as set in Shared`],
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
    grab(h),
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
