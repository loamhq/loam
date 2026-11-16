import { label } from './label.js'
export function activate(loam) {
  const h = loam.ui.h, image = new URL('../assets/proof.svg', import.meta.url).href
  loam.places.rail.section.add({ id: 'proof', title: 'Proof', icon: 'puzzle', render: () => h('main', { class: 'proof-section' }, h('h1', { text: label }), h('img', { src: image, alt: 'Proof asset', width: 44, height: 44 })) })
  loam.places.app.overlay.add({ id: 'proof.overlay', render: () => h('aside', { class: 'proof-overlay', style: { position: 'fixed', right: '8px', top: '8px', padding: '8px', borderRadius: '12px', background: 'var(--surface-pane)', color: 'var(--text)', zIndex: '1', pointerEvents: 'none' }, text: 'Proof active' }) })
  loam.commands.register({ id: 'proof.greet', title: 'Proof greeting', run: () => { const count = loam.settings.get('greetings', 0) + 1; loam.settings.set('greetings', count); return `Hello ${count}` } })
  loam.places.message.renderer.add({ id: 'proof.message', component: 'core.message', render: ({ message }) => h('article', { class: 'proof-message tt-message', 'data-message-id': message.id, text: `Proof: ${message.text || message.kind}` }) })
}
