// Git backup's page: one section in Settings › Plugins › Git backup.
//
// Everything it shows and does is the plugin's own server's, at
// /x/git-backup/ (server/main.py): the health of each tree, connecting one
// (address, key, test), backing up now, how often. The plugin's three
// settings.entry values are the same ones an agent sets through
// `bin/settings set plugin.git-backup.<key>`; this page draws them its own
// way, so it tells the host not to draw them again (`entries: 'own'`).

const BASE = '/x/git-backup/'
const TREE_ICON = { vault: 'folder', spaces: 'users' }
const POLL_MS = 2500
const STALE_MS = 30000

export function activate(loam) {
  const { h, icon } = loam.ui
  const host = loam.ui.host
  loam.ui.styles?.add?.('page/style.css')
  const st = { status: null, error: '', at: 0, loading: false, open: {}, input: {}, busy: {}, said: {}, copied: null, confirm: null }
  let disposed = false, timer = null

  const redraw = () => { if (!disposed) host?.schedule?.() }
  async function call(method, path, body) {
    const res = await fetch(BASE + path, { method, credentials: 'same-origin', headers: body ? { 'content-type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
    let out = null
    try { out = await res.json() } catch { out = null }
    if (!res.ok || !out) throw new Error(out?.error || (res.status === 502 ? 'Git backup is starting. Try again in a moment.' : `The plugin answered ${res.status}.`))
    if (out.status) take(out.status)
    return out
  }
  function take(status) { st.status = status; st.at = Date.now(); st.error = ''; poll() }
  async function refresh() {
    if (st.loading) return
    st.loading = true
    try { take(await call('GET', 'status')) } catch (e) { st.error = e.message } finally { st.loading = false; redraw() }
  }
  function poll() {
    clearTimeout(timer)
    const busy = st.status?.trees?.some(t => t.pushing)
    if (busy && !disposed) timer = setTimeout(refresh, POLL_MS)
  }
  async function act(tree, kind, path, body, after) {
    st.busy[tree] = kind; st.said[tree] = null; redraw()
    try { const out = await call('POST', path, body); after?.(out) }
    catch (e) { st.said[tree] = { ok: false, words: e.message } }
    finally { st.busy[tree] = null; redraw() }
  }
  async function copy(tree, text) {
    try { await navigator.clipboard.writeText(text) }
    catch {
      const area = h('textarea', { class: 'gb-offscreen', 'aria-hidden': 'true' }); area.value = text
      document.body.append(area); area.select(); try { document.execCommand('copy') } catch {} area.remove()
    }
    st.copied = tree; redraw()
    setTimeout(() => { if (st.copied === tree) { st.copied = null; redraw() } }, 1800)
  }

  const glyph = (name, size = 16) => (icon[name] ?? icon.folder)(size)
  const btn = (text, onclick, cls = 'pillbtn content-mark', extra = {}) => h('button', { class: cls, type: 'button', onclick, ...extra }, text)

  function pill(t) {
    const words = { ok: t.last_pushed_ago ? `Backed up ${t.last_pushed_ago}` : 'Backed up', fail: 'Not backing up', wait: t.public_key && !(t.test?.ok) ? 'One step left' : 'Starting', off: 'Not connected' }[t.level]
    return h('span', { class: `gb-pill gb-${t.level}${t.pushing ? ' gb-moving' : ''}` }, h('span', { class: 'gb-dot', 'aria-hidden': 'true' }), t.pushing ? 'Backing up…' : words)
  }

  function said(tree) {
    const s = st.said[tree]
    return s ? h('p', { class: `gb-said ${s.ok ? 'gb-said-ok' : 'gb-said-bad'}`, role: s.ok ? 'status' : 'alert' }, s.ok ? h('span', { class: 'gb-tick', 'aria-hidden': 'true' }) : null, s.words) : null
  }

  function connectForm(t) {
    const id = `gb-remote-${t.id}`
    const busy = st.busy[t.id] === 'connect'
    return h('form', { class: 'gb-form', key: `form-${t.id}`, onsubmit: e => {
      e.preventDefault()
      const remote = (st.input[t.id] ?? '').trim()
      if (!remote) { st.said[t.id] = { ok: false, words: 'Paste the repository’s address first.' }; redraw(); return }
      act(t.id, 'connect', 'connect', { tree: t.id, remote }, () => { st.open[t.id] = false })
    } },
    h('label', { for: id, class: 'gb-label', text: 'Repository address' }),
    h('input', { id, key: id, class: 'field-input gb-mono', type: 'text', inputmode: 'url', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', placeholder: `git@github.com:you/${t.id === 'vault' ? 'my-vault' : 'my-ai-spaces'}.git`, value: st.input[t.id] ?? '', disabled: busy, oninput: e => { st.input[t.id] = e.currentTarget.value } }),
    h('p', { class: 'note', text: `An empty, private repository, used for your ${t.name} alone. On GitHub: New repository, choose Private, and add nothing to it. Its page’s address works too.` }),
    said(t.id),
    h('div', { class: 'gb-actions' },
      h('button', { class: 'pillbtn primary', type: 'submit', disabled: busy }, busy ? 'Checking…' : 'Continue'),
      t.remote ? null : btn('Cancel', () => { st.open[t.id] = false; st.said[t.id] = null; redraw() }, 'pillbtn content-mark', { disabled: busy })))
  }

  function keyStep(t) {
    const busy = st.busy[t.id] === 'test'
    const where = t.host === 'github' ? 'GitHub' : t.host === 'gitlab' ? 'GitLab' : t.host === 'gitea' ? 'your host' : null
    return h('div', { class: 'gb-key', key: `key-${t.id}` },
      h('p', { class: 'gb-step-title' }, 'Give Loam a key to this repository'),
      h('p', { class: 'note', text: 'It opens this one repository and nothing else in your account, and it never leaves this machine.' }),
      h('div', { class: 'gb-keybox' },
        h('code', { class: 'gb-keytext', text: t.public_key }),
        h('button', { class: 'pillbtn content-mark gb-copy', type: 'button', 'aria-label': 'Copy the key', onclick: () => copy(t.id, t.public_key) }, st.copied === t.id ? glyph('check', 15) : glyph('copy', 15), st.copied === t.id ? 'Copied' : 'Copy')),
      h('ol', { class: 'gb-steps' }, ...(t.steps ?? []).map(s => h('li', { text: s }))),
      said(t.id),
      h('div', { class: 'gb-actions' },
        t.add_key_at ? h('a', { class: 'pillbtn content-mark', href: t.add_key_at, target: '_blank', rel: 'noopener noreferrer' }, `Open deploy keys${where ? ` on ${where}` : ''}`, glyph('external', 14)) : null,
        btn(busy ? 'Testing…' : 'I’ve added it: test', () => act(t.id, 'test', 'test', { tree: t.id }, out => {
          st.said[t.id] = out.ok ? { ok: true, words: 'Connected. The first backup has started.' } : { ok: false, words: out.words }
        }), 'pillbtn primary', { disabled: busy })),
      h('button', { class: 'gb-quiet', type: 'button', onclick: () => { st.open[t.id] = true; st.input[t.id] = t.remote; redraw() } }, 'Use another repository'))
  }

  function held(t) {
    if (!t.held?.length) return null
    const mb = n => `${Math.round(n / 1e6).toLocaleString()} MB`
    return h('details', { class: 'gb-held' },
      h('summary', {}, `${t.held.length === 1 ? 'One file is' : `${t.held.length} files are`} too large to back up`),
      h('p', { class: 'note', text: `Git hosts refuse files over ${mb(t.limit_bytes)}, so ${t.held.length === 1 ? 'it stays' : 'they stay'} on this machine only, untouched. Move ${t.held.length === 1 ? 'it' : 'them'} elsewhere, or keep a copy another way.` }),
      h('ul', { class: 'gb-files' }, ...t.held.slice(0, 20).map(f => h('li', {}, h('span', { class: 'gb-mono', text: f.path }), h('span', { class: 'gb-size', text: mb(f.bytes) })))),
      t.held.length > 20 ? h('p', { class: 'note', text: `And ${t.held.length - 20} more.` }) : null)
  }

  function healthy(t) {
    const busy = st.busy[t.id]
    const confirm = st.confirm === t.id
    return h('div', { class: 'gb-body' },
      h('p', { class: 'gb-where' }, t.web ? h('a', { href: t.web, target: '_blank', rel: 'noopener noreferrer', class: 'gb-repo' }, repoName(t), glyph('external', 13)) : h('span', { class: 'gb-repo gb-mono', text: t.remote })),
      t.level === 'fail' ? h('div', { class: 'gb-failbox', role: 'alert' },
        h('p', { class: 'gb-fail-words', text: t.failing.words }),
        t.failing.files?.length ? h('p', { class: 'note gb-mono', text: t.failing.files.join(', ') }) : null,
        h('p', { class: 'note', text: `Since ${when(t.failing.since)}. ${t.last_pushed_ago ? `Last good backup ${t.last_pushed_ago}.` : 'Nothing has reached the repository yet.'} Everything is still kept on this machine.` }),
        t.failing.detail ? h('details', { class: 'gb-detail' }, h('summary', {}, 'What the host said'), h('pre', { text: t.failing.detail })) : null) : null,
      t.level === 'fail' && ['key', 'read-only'].includes(t.failing.kind) && t.public_key ? keyStep(t) : null,
      t.level === 'fail' && t.failing.kind === 'key' && !t.public_key ? h('div', { class: 'gb-actions' },
        btn(busy === 'connect' ? 'Making a key…' : 'Make a key for this repository', () => act(t.id, 'connect', 'connect', { tree: t.id, remote: t.remote }), 'pillbtn primary', { disabled: Boolean(busy) })) : null,
      t.level === 'ok' ? h('p', { class: 'note', text: t.waiting_commits ? `${t.waiting_commits} newer ${t.waiting_commits === 1 ? 'change waits' : 'changes wait'} for the next backup.` : 'Everything on this machine is in the repository.' }) : null,
      held(t),
      said(t.id),
      confirm
        ? h('div', { class: 'gb-confirm' }, h('p', { text: `Stop backing up your ${t.name}? The repository and everything in it stay as they are.` }),
            h('div', { class: 'gb-actions' }, btn('Keep backing up', () => { st.confirm = null; redraw() }), btn('Stop', () => act(t.id, 'off', 'disconnect', { tree: t.id }, () => { st.confirm = null }), 'pillbtn primary danger')))
        : h('div', { class: 'gb-actions' },
            btn(busy === 'now' || t.pushing ? 'Backing up…' : t.level === 'fail' ? 'Try again' : 'Back up now', () => act(t.id, 'now', 'now', { tree: t.id }), 'pillbtn content-mark', { disabled: Boolean(busy) || t.pushing }),
            h('button', { class: 'gb-quiet', type: 'button', onclick: () => { st.confirm = t.id; redraw() } }, 'Stop backing up')))
  }

  function card(t) {
    let body
    if (st.open[t.id] || (!t.remote && st.open[t.id] !== false && st.input[t.id])) body = connectForm(t)
    else if (!t.remote) body = h('div', { class: 'gb-body' }, h('p', { class: 'note', text: t.id === 'vault' ? 'Every note and conversation, with its history.' : 'Every agent’s workspace and memory, with its history.' }), said(t.id),
      h('div', { class: 'gb-actions' }, btn('Connect a repository', () => { st.open[t.id] = true; st.said[t.id] = null; redraw() }, 'pillbtn')))
    else if (t.level === 'wait' && t.public_key && !t.test?.ok) body = keyStep(t)
    else body = healthy(t)
    return h('section', { class: `gb-card gb-card-${t.level}`, key: `card-${t.id}`, 'aria-label': t.name },
      h('header', { class: 'gb-card-head' }, h('span', { class: 'gb-card-icon', 'aria-hidden': 'true' }, glyph(TREE_ICON[t.id], 18)), h('h4', { class: 'gb-card-title', text: t.name }), pill(t)),
      body)
  }

  function often(s) {
    const id = 'gb-every'
    return h('div', { class: 'gb-often' },
      h('label', { for: id, class: 'gb-label', text: 'Back up' }),
      h('select', { id, class: 'field-input gb-select', onchange: e => act('every', 'every', 'every', { every: e.currentTarget.value }) },
        ...Object.entries(s.choices).map(([v, words]) => h('option', { value: v, selected: v === s.every, text: words[0].toUpperCase() + words.slice(1) }))))
  }

  function hero(s) {
    const level = s.summary.level
    return h('div', { class: `gb-hero gb-hero-${level}` },
      h('span', { class: 'gb-orb', 'aria-hidden': 'true' }, h('span')),
      h('div', {}, h('p', { class: 'gb-hero-words', text: s.summary.words }),
        h('p', { class: 'note', text: level === 'off' ? 'Copy your vault and your agents to private repositories of your own, so nothing is lost with this machine.' : `To private repositories of yours, ${s.every_words}.` })))
  }

  function view() {
    if (!st.status && !st.loading && !st.error) refresh()
    else if (st.status && Date.now() - st.at > STALE_MS && !st.loading) refresh()
    if (!st.status) return h('div', { class: 'gb' }, st.error ? h('p', { class: 'note warn', role: 'alert', text: st.error }) : h('p', { class: 'note', role: 'status', text: 'Reading your backup…' }),
      st.error ? btn('Try again', () => { st.error = ''; refresh() }) : null)
    const s = st.status
    return h('div', { class: 'gb' }, hero(s), st.error ? h('p', { class: 'note warn', role: 'alert', text: st.error }) : null,
      h('div', { class: 'gb-cards' }, ...s.trees.map(card)),
      s.trees.some(t => t.remote) ? often(s) : null,
      h('p', { class: 'note gb-floor' }, 'With or without this, Loam keeps every change on this machine every 15 minutes. To bring everything back on a new Loam, connect the same repositories there and ask an agent to restore them.'))
  }

  const repoName = t => (t.web || '').replace(/^https:\/\//, '')
  const when = iso => { const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) }

  loam.places.settings.section.add({ id: 'git-backup', group: 'plugin', title: 'Backup', entries: 'own', render: () => view() })
  loam.events?.on?.('page.visible', () => { if (st.status) refresh() })
  return () => { disposed = true; clearTimeout(timer) }
}
