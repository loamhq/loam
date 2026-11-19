import { h } from './ui.js'

// Settings › GitHub: "Your name on agents' work" (2026-11-18). The name and
// address new commits by the owner's agents carry. One host setting,
// GET/PATCH /api/settings/git, the same route a persona's
// `settings set git.identity …` takes (the host's lib/loam_git_identity.py).
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
  function view({ github = false } = {}) {
    if (st.value === undefined) load()
    const ident = st.value?.identity
    const button = (text, onclick, cls = 'pillbtn content-mark') => h('button', { class: cls, type: 'button', text, onclick, disabled: st.busy })
    return h('section', { class: 'card git-identity', 'aria-labelledby': 'git-identity-title' },
      h('h3', { class: 'setrow-title', id: 'git-identity-title', text: 'Your name on agents’ work' }),
      st.value === undefined ? h('p', { class: 'note', role: 'status', text: 'Reading…' })
        : ident ? h('p', { class: 'setrow-sub git-identity-now', text: `${ident.name} · ${ident.email}${ident.source === 'github' ? ' · from GitHub' : ''}` })
          : h('p', { class: 'note', text: 'Not set. New commits by your agents carry the engineer’s name. Choose yours:' }),
      !st.typing ? h('div', { class: 'ghpills' },
        github ? button(ident?.source === 'github' ? 'Read from GitHub again' : 'Use my GitHub account', () => save('github', 'Your GitHub name and private no-reply address are set.'), ident ? 'pillbtn content-mark' : 'pillbtn primary') : null,
        button('Type a name', () => { st.typing = true; st.name = ident?.name ?? ''; st.email = ident?.source === 'typed' ? ident.email : ''; changed() }),
        ident ? button('Clear', () => save(null, 'Cleared.')) : null) : null,
      st.typing ? h('form', { class: 'git-identity-form', onsubmit: event => { event.preventDefault(); save({ name: st.name, email: st.email }, 'Saved.') } },
        h('label', { for: 'git-identity-name', text: 'Name' }),
        h('input', { id: 'git-identity-name', class: 'field-input', value: st.name, autocomplete: 'name', oninput: event => { st.name = event.target.value } }),
        h('label', { for: 'git-identity-email', text: 'Email' }),
        h('input', { id: 'git-identity-email', class: 'field-input', type: 'email', value: st.email, autocomplete: 'email', oninput: event => { st.email = event.target.value } }),
        h('div', { class: 'ghpills' }, h('button', { class: 'pillbtn primary', type: 'submit', text: 'Save', disabled: st.busy }),
          button('Cancel', () => { st.typing = false; changed() }))) : null,
      h('p', { class: 'note', text: 'New commits only; nothing already pushed changes. A project’s own person or a repository’s own name comes first.' }),
      st.error ? h('p', { class: 'note warn', role: 'alert', text: st.error }) : null,
      st.done ? h('p', { class: 'note', role: 'status', text: st.done }) : null)
  }
  return { st, view, load, save }
}
