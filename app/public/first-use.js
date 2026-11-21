// Each action has its own key, even when two menus have the caption “More”.
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

export function firstUseActive() { return active }

const captions = {
  Back: 'Back', Board: 'Board', Activity: 'Activity', Settings: 'Settings',
  'New thread': 'New chat', 'Search threads': 'Search', 'Thread list actions': 'More',
  'Thread actions': 'More', 'Hide sidebar for a focused view': 'Hide list', 'Show sidebar': 'Show list',
  'Back to threads': 'Chats', 'Return to open panel': 'Open panel', 'Close board': 'Close',
  'Attach a file': 'Attach', 'Mention an agent': 'Mention', 'Write this in a bigger window': 'Expand',
  Send: 'Send', Reply: 'Reply', Copy: 'Copy', 'Branch into a new thread': 'Branch',
  'Open to the side': 'Side panel', 'This folder': 'More', 'Add to this folder': 'Add',
  'This file': 'More', Download: 'Download', 'Thread details': 'Details',
  'Close search': 'Close', 'Close Activity': 'Close', 'Close settings': 'Close',
  Edit: 'Edit', Source: 'Source', Preview: 'Preview', 'Open in new tab': 'Open',
  'fs-rowmenu': 'More', 'fs-shortcutmenu': 'More', 'fs-copylink': 'Copy link',
  'tt-card-menu': 'More', 'apps-details-button': 'Details',
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
