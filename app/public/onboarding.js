import { h } from './ui.js'

// The runtime marks the starter thread. Its preset is the only content before
// the person's first reply; a used thread never sends someone back to setup.
export function untouchedHello(record) {
  return record?.thread?.hello === true && record.has_more === false
    && record.messages?.length > 0 && record.messages.every(m => m.preset === true)
}

export function welcomeScreen({ providers, signIn, continueWith, retry, error = '', checking = false }) {
  const choices = providers.filter(p => ['claude-code', 'codex', 'antigravity'].includes(p.id))
  const connected = choices.some(p => p.connected)
  return h('main', { class: 'welcome', key: 'welcome' }, h('div', { class: 'welcome-inner' },
    h('div', { class: 'welcome-mark', 'aria-hidden': 'true', text: '✳' }),
    h('p', { class: 'kicker', text: 'A little room for possibility' }),
    h('h1', { class: 'display', text: checking ? 'Making room for you…' : 'Welcome to Loam' }),
    checking ? h('p', { class: 'note', role: 'status', text: 'Getting your space ready.' }) : [
      h('p', { class: 'welcome-intro', text: 'Your agents are ready to meet you. First, connect an AI provider so they can talk with you.' }),
      h('p', { class: 'note', text: 'Use your own Claude Code, Codex or Antigravity account. Choose one to start; you can add more in Settings later.' }),
      h('div', { class: 'welcome-providers' }, choices.map(p => signIn.card(p, { onboarding: true }))),
      error ? h('p', { class: 'reason', role: 'alert', text: error }) : null,
      !choices.length ? h('button', { class: 'pillbtn', text: 'Try again', onclick: retry }) : null,
      h('div', { class: 'welcome-continue' }, h('button', { class: 'primary', disabled: !connected, text: 'Meet your first agent', onclick: continueWith }),
        h('p', { class: 'note', role: 'status', text: connected ? 'You’re connected. Let’s say hello.' : 'Connect one provider to continue.' }))]))
}
