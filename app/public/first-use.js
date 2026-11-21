// Each action has its own key, even when two buttons have the caption “Details”.
let active = false
const used = new Set()
const pending = new Set()
let save

export function syncFirstUse(controls, persist) {
  if (!Array.isArray(controls)) return
  active = true
  save = persist
  for (const key of controls) { used.add(key); pending.delete(key) }
  for (const key of pending) persistUse(key)
}

function persistUse(key) {
  save(key).then(() => pending.delete(key)).catch(() => pending.add(key))
}

export function firstThreadLesson() {
  const key = 'Agentless thread'
  if (used.has(key)) return false
  try {
    if (localStorage.getItem('loam.thread-lesson')) return false
    localStorage.setItem('loam.thread-lesson', '1')
  } catch { /* Memory still covers this visit. */ }
  used.add(key)
  if (active) persistUse(key)
  return true
}

export function firstUseActive() { return active }

const captions = {
  Board: 'Board',
  'Thread details': 'Details',
  'apps-details-button': 'Details',
  'Branch into a new thread': 'Branch',
  Activity: 'Activity',
  Settings: 'Settings',
  'Hide sidebar for a focused view': 'Hide list',
}

// Draw the caption as part of the control. Its accessible name and its
// existing keyboard and pointer handlers stay with the original button.
export function labelFirstUse(node, props) {
  if (!active) return
  const key = [props['aria-label'], props.title, ...(props.class ?? '').split(' ')]
    .find(name => Object.hasOwn(captions, name))
  if (!key || used.has(key)) return
  if (key !== 'Thread details' && /[a-z]/i.test(node.textContent)) return
  node.setAttribute('data-first-use', key)
  node.setAttribute('data-caption', captions[key])
}

export function usedControl(event) {
  const control = event.target.closest?.('[data-first-use]')
  if (!control || control.disabled) return
  const key = control.getAttribute('data-first-use')
  used.add(key)
  persistUse(key)
  for (const button of control.ownerDocument.querySelectorAll('[data-first-use]')) {
    if (button.getAttribute('data-first-use') === key) {
      button.removeAttribute('data-first-use')
      button.removeAttribute('data-caption')
    }
  }
}
