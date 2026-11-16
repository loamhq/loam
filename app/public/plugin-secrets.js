import { h } from './ui.js'

// Only status crosses back from the host. A pasted value lives in this form
// until submit, then is cleared before the request is sent. Never persist it.
export function createSecretSettings({ id, request, redraw = () => {} }) {
  const st = { rows: null, error: '', message: '', busy: false, loading: false }
  const path = `/plugins/${encodeURIComponent(id)}/secrets`
  async function load() {
    st.loading = true
    try { st.rows = (await request('GET', path)).secrets || [] }
    catch (e) { st.error = e.message }
    finally { st.loading = false; redraw() }
  }
  async function act(method, name, body, suffix = '') {
    if (st.busy) return
    st.busy = true; st.error = ''; st.message = ''; redraw()
    try {
      const out = await request(method, `${path}/${encodeURIComponent(name)}${suffix}`, body)
      st.message = out.checked?.why || (method === 'DELETE' ? (out.source === 'box' ? 'Using the key supplied by this box.' : 'Key forgotten.') : '')
      await load()
    } catch (e) { st.error = e.body?.checked?.why || e.body?.error || e.message }
    finally { st.busy = false; redraw() }
  }
  function view() {
    if (st.rows === null && !st.loading && !st.error) load()
    return h('section', { class: 'plugin-secret-settings', 'aria-label': `${id} API key` },
      st.loading ? h('p', { class: 'note', role: 'status', text: 'Reading key status…' }) : null,
      st.rows?.map(row => h('form', { key: row.name, onsubmit: e => { e.preventDefault(); const input = e.currentTarget.querySelector('input'); const value = input.value; input.value = ''; act('PUT', row.name, { value }) } },
        h('label', { for: `${id}-${row.name}`, text: row.label }),
        h('p', { class: 'note', text: row.set ? row.source === 'box' ? 'Using the key supplied by this box.' : 'A key is saved for this plugin.' : 'No key is set.' }),
        row.checked?.why ? h('p', { class: `note${row.checked.ok ? '' : ' warn'}`, text: row.checked.why }) : null,
        row.refused?.why && row.refused.why !== row.checked?.why ? h('p', { class: 'note warn', text: row.refused.why }) : null,
        h('input', { id: `${id}-${row.name}`, class: 'field-input', type: 'password', autocomplete: 'off', spellcheck: 'false', placeholder: 'Paste a key', 'aria-label': `${row.label} for ${id}`, disabled: st.busy }),
        h('div', { class: 'profile-actions' },
          h('button', { class: 'pillbtn primary', type: 'submit', disabled: st.busy, text: st.busy ? 'Checking…' : 'Save key' }),
          h('button', { class: 'pillbtn', type: 'button', disabled: st.busy || !row.set, text: 'Check again', onclick: () => act('POST', row.name, {}, '/check') }),
          h('button', { class: 'pillbtn', type: 'button', disabled: st.busy || !row.set, text: 'Forget', onclick: () => act('DELETE', row.name) })))),
      st.message ? h('p', { role: 'status', class: 'note', text: st.message }) : null,
      st.error ? h('p', { role: 'alert', class: 'note warn', text: st.error }) : null)
  }
  return { st, load, act, view }
}
