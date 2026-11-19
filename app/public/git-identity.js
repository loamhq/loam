import { h } from './ui.js'

// Settings › GitHub: "Your name on agents' work" (2026-11-18). The name and
// address new commits by the owner's agents carry. One host setting,
// GET/PATCH /api/settings/git, the same route a persona's
// `settings set git.identity …` takes (the host's lib/loam_git_identity.py).
//
// Three choices, one of them on: Default (null: the one connected GitHub
// account's name and no-reply address), a typed name, or Neutral
// ("<project> engineer", no note). Under Default, with GitHub connected, one
// box says whether pull requests may also be opened as the owner: that is
// the explicit "github" choice, and nothing else changes who opens one.
export function createGitIdentity({ api, changed }) {
  const st = { value: undefined, error: '', done: '', busy: false, typing: false, name: '', email: '' }
  async function load() {
    if (st.value !== undefined || st.busy) return
    st.busy = true
    try { st.value = await api('GET', '/settings/git') } catch (error) { st.error = error.message; st.value = null }
    finally { st.busy = false; changed() }
  }
  async function save(identity, done) {
    if (st.busy) return
    st.busy = true; st.error = ''; st.done = ''; changed()
    try { st.value = await api('PATCH', '/settings/git', { identity }); st.done = done; st.typing = false }
    catch (error) { st.error = error.message }
    finally { st.busy = false; changed() }
  }
  function choiceOf(value) {
    const ident = value?.identity
    if (ident === 'neutral') return 'neutral'
    if (ident?.source === 'typed') return 'typed'
    if (ident?.source === 'github') return 'github'
    return 'default'
  }
  function how(value, choice) {
    const eff = value?.effective
    if (choice === 'neutral') return 'Neutral, by your choice: commits carry “<project> engineer”.'
    if (choice === 'typed') return 'The name and address you typed.'
    if (choice === 'github') return `Your GitHub account${eff?.login ? ` (${eff.login})` : ''}, with its private no-reply address.`
    if (eff?.source === 'github') return `Default: your connected GitHub account (${eff.login}), with its private no-reply address.`
    if (eff?.source === 'git-config') return 'Default: this computer’s own git name, whose address is a GitHub no-reply.'
    return ''
  }
  function view({ github = false } = {}) {
    if (st.value === undefined) load()
    const value = st.value
    const choice = choiceOf(value)
    const eff = value?.effective
    const pick = (id, text, onclick) => h('button', { type: 'button', class: (id === choice || (id === 'default' && choice === 'github')) ? 'on' : '', 'aria-pressed': String(id === choice || (id === 'default' && choice === 'github')), text, onclick, disabled: st.busy })
    const asMe = github && (choice === 'default' || choice === 'github') && (eff?.source === 'github' || choice === 'github')
    return h('section', { class: 'card git-identity', 'aria-labelledby': 'git-identity-title' },
      h('h3', { class: 'setrow-title', id: 'git-identity-title', text: 'Your name on agents’ work' }),
      value === undefined ? h('p', { class: 'note', role: 'status', text: 'Reading…' })
        : h('p', { class: 'setrow-sub git-identity-now', text: eff ? `${eff.name} · ${eff.email}` : '<project> engineer' }),
      value ? (how(value, choice) ? h('p', { class: 'note', text: how(value, choice) }) : null) : null,
      value?.prompt ? h('p', { class: 'note warn git-identity-prompt', role: 'status', text: value.prompt }) : null,
      h('div', { class: 'segmented git-identity-choice', role: 'group', 'aria-label': 'Whose name' },
        pick('default', 'Default', () => { if (choice !== 'default') save(null, 'Back to the default.') }),
        pick('typed', 'Type a name', () => { st.typing = true; st.name = value?.identity?.name ?? eff?.name ?? ''; st.email = choice === 'typed' ? value.identity.email : ''; changed() }),
        pick('neutral', 'Neutral', () => { if (choice !== 'neutral') save('neutral', 'Neutral: commits carry the engineer’s name.') })),
      asMe ? h('label', { class: 'note git-identity-asme' },
        h('input', { type: 'checkbox', class: 'git-identity-asme-box', checked: choice === 'github', disabled: st.busy, onchange: event => save(event.target.checked ? 'github' : null, event.target.checked ? 'Pull requests on your account’s repositories open as you.' : 'Pull requests open as the Loam app.') }),
        ' Also open pull requests as me') : null,
      st.typing ? h('form', { class: 'git-identity-form', onsubmit: event => { event.preventDefault(); save({ name: st.name, email: st.email }, 'Saved.') } },
        h('label', { for: 'git-identity-name', text: 'Name' }),
        h('input', { id: 'git-identity-name', class: 'field-input', value: st.name, autocomplete: 'name', oninput: event => { st.name = event.target.value } }),
        h('label', { for: 'git-identity-email', text: 'Email (it will be public on every commit)' }),
        h('input', { id: 'git-identity-email', class: 'field-input', type: 'email', value: st.email, autocomplete: 'email', oninput: event => { st.email = event.target.value } }),
        h('div', { class: 'ghpills' }, h('button', { class: 'pillbtn primary', type: 'submit', text: 'Save', disabled: st.busy }),
          h('button', { class: 'pillbtn content-mark', type: 'button', text: 'Cancel', onclick: () => { st.typing = false; changed() }, disabled: st.busy }))) : null,
      h('p', { class: 'note', text: 'New commits only; nothing already pushed changes. A project’s own person, or a repository’s own name, comes first.' }),
      st.error ? h('p', { class: 'note warn', role: 'alert', text: st.error }) : null,
      st.done ? h('p', { class: 'note', role: 'status', text: st.done }) : null)
  }
  return { st, view, load, save }
}
