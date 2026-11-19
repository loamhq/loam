// A theme, shown by wearing it (2026-11-18): the miniature of Loam that
// Settings › Appearance's theme picker draws on each card, a message, a face
// and Send, and a line of body text, in whatever look the
// `.theme-preview[data-look=<id>]` around it scopes. One component, so the
// picker and the Market draw the same preview: a Market theme with no cover
// shows itself this way, from its own theme.css before it is installed.
import { h, blob } from './ui.js'

export function themeMiniature({ initial = 'A', color = 'var(--persona-gold)', ink = 'var(--persona-gold-ink)' } = {}) {
  return h('span', { class: 'theme-miniature' },
    h('span', { class: 'say user', text: 'Room to think' }),
    h('span', { class: 'theme-mini-controls' },
      blob({ color, ink, shape: '34%', initial }, { size: 26, font: 13, inline: true }),
      h('span', { class: 'pillbtn primary', text: 'Send' })),
    h('span', { class: 'theme-mini-body', text: 'A little space for ideas.' }))
}

// The preview on its own: the miniature inside the look's scope. `look` is
// null while the theme's tokens are still loading (the current look shows,
// dimmed by the caller's class).
export function themePreview(look, { cls = '', initial } = {}, ...more) {
  return h('span', { class: `theme-preview ${cls}`.trim(), 'data-look': look, 'aria-hidden': 'true' }, themeMiniature({ initial }), ...more)
}
