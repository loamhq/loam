import { h } from './ui.js'

// The built-in Files text editor.
// A host owns its draft and revision. The field owns its caret and save keys.
export function fileEditor({ edit, save, blur, label }) {
  const field = h('textarea', {
    class: 'fs-field', spellcheck: 'true', 'aria-label': label,
    oninput: () => edit(field.value),
    onblur: () => blur(),
    onkeydown: e => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 's') {
        e.preventDefault?.()
        save()
      }
    },
  })
  return field
}
