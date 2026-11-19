import { h, icon } from './ui.js'
import { copyText } from './clipboard.js'

const BACKUP_BASE = '/x/git-backup/'
const BACKUP_POLL_MS = 2500
const BACKUP_START_RETRIES = 10
const BACKUP_START_DELAY_MS = 400
const DEFAULT_TREES = [
  { id: 'vault', name: 'Vault', level: 'off', remote: null },
  { id: 'spaces', name: 'AI Spaces', level: 'off', remote: null },
]
const TREE_SUB = {
  vault: 'Every note and conversation, with its history.',
  spaces: 'Every agent’s workspace and memory, with its history.',
}
const TREE_ICON = { vault: 'folder', spaces: 'users' }

// The runtime marks the starter thread. Its preset is the only content before
// the person's first reply; a used thread never sends someone back to setup.
export function untouchedHello(record) {
  return record?.thread?.hello === true && record.has_more === false
    && record.messages?.length > 0 && record.messages.every(m => m.preset === true)
}

/**
 * Controller for the optional backup step in first setup, talking to the
 * existing Git backup plugin (`git-backup`) and its `/x/git-backup/` server.
 */
export function createOnboardingBackup({ api, request = globalThis.fetch, pluginLoader, changed = () => {}, delay = ms => new Promise(r => setTimeout(r, ms)) }) {
  const st = { status: null, loading: false, error: '', open: {}, input: {}, busy: {}, said: {}, copied: null, enabledHere: false }
  let timer = null, copyTimer = null, stopped = false

  const row = () => pluginLoader?.st?.rows?.find(r => r.id === 'git-backup' && r.effective !== false && !r.broken) ?? null
  const available = () => Boolean(row())
  const trees = () => st.status?.trees?.length ? st.status.trees : DEFAULT_TREES
  const anyConnected = () => trees().some(t => Boolean(t.remote) && (Boolean(t.test?.ok) || t.level === 'ok' || Boolean(t.pushing)))
  const anyRemote = () => trees().some(t => Boolean(t.remote))

  function schedulePoll() {
    clearTimeout(timer)
    if (stopped) return
    if (trees().some(t => t.pushing)) timer = setTimeout(refresh, BACKUP_POLL_MS)
  }

  function take(status) {
    st.status = status
    st.error = ''
    schedulePoll()
  }

  async function call(method, path, body, { retryStartup = false } = {}) {
    const attempts = retryStartup ? BACKUP_START_RETRIES : 1
    for (let attempt = 0; attempt < attempts; attempt++) {
      const res = await request(BACKUP_BASE + path, {
        method,
        credentials: 'same-origin',
        headers: body ? { 'content-type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
      })
      if ((res.status === 502 || res.status === 503) && attempt + 1 < attempts) {
        await delay(BACKUP_START_DELAY_MS)
        continue
      }
      let out = null
      try { out = await res.json() } catch { out = null }
      if (!res.ok || !out) {
        throw new Error(out?.error || (res.status === 502 || res.status === 503 ? 'Git backup is starting. Try again in a moment.' : `The backup service answered ${res.status}.`))
      }
      if (out.status) take(out.status)
      return out
    }
  }

  async function refresh() {
    if (st.loading || stopped) return
    const plugin = row()
    if (!plugin?.on) return
    st.loading = true
    changed()
    try {
      const out = await call('GET', 'status', undefined, { retryStartup: true })
      take(out)
    } catch (e) {
      st.error = e.message
    } finally {
      st.loading = false
      changed()
    }
  }

  async function ensureEnabled() {
    const plugin = row()
    if (!plugin) throw new Error('Git backup is not available on this host.')
    if (plugin.on) return
    await api('PATCH', '/plugins/git-backup', { on: true })
    st.enabledHere = true
  }

  async function connect(tree, rawRemote) {
    const remote = String(rawRemote ?? '').trim()
    if (!remote) {
      st.said[tree] = { ok: false, words: 'Paste the repository’s address first.' }
      changed()
      return
    }
    st.busy[tree] = 'connect'
    st.said[tree] = null
    changed()
    try {
      const wasOff = !row()?.on
      await ensureEnabled()
      const out = await call('POST', 'connect', { tree, remote }, { retryStartup: wasOff || st.enabledHere })
      if (!out.status) {
        const current = trees().map(t => t.id === tree ? { ...t, ...out, level: 'wait', test: null } : t)
        take({ ok: true, trees: current })
      }
      st.open[tree] = false
    } catch (e) {
      st.said[tree] = { ok: false, words: e.message }
    } finally {
      st.busy[tree] = null
      changed()
    }
  }

  async function test(tree) {
    st.busy[tree] = 'test'
    st.said[tree] = null
    changed()
    try {
      const out = await call('POST', 'test', { tree })
      if (!out.status) {
        const current = trees().map(t => t.id === tree ? { ...t, level: out.ok ? 'ok' : t.level, test: { ok: out.ok, words: out.words }, pushing: Boolean(out.pushing) } : t)
        take({ ok: true, trees: current })
      }
      st.said[tree] = out.ok
        ? { ok: true, words: 'Connected. The first backup has started.' }
        : { ok: false, words: out.words }
    } catch (e) {
      st.said[tree] = { ok: false, words: e.message }
    } finally {
      st.busy[tree] = null
      changed()
    }
  }

  async function copy(tree, text) {
    if (!(await copyText(text))) return
    st.copied = tree
    changed()
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      if (st.copied === tree) { st.copied = null; changed() }
    }, 1800)
  }

  function openForm(tree, initial = null) {
    st.open[tree] = true
    if (initial !== null) st.input[tree] = initial
    st.said[tree] = null
    changed()
  }

  function closeForm(tree) {
    st.open[tree] = false
    st.said[tree] = null
    changed()
  }

  function setInput(tree, value) {
    st.input[tree] = value
  }

  function skipCleanup() {
    if (st.enabledHere && !anyRemote()) {
      api('PATCH', '/plugins/git-backup', { on: false }).catch(() => {})
    }
    stop()
  }

  function stop() {
    stopped = true
    clearTimeout(timer)
    clearTimeout(copyTimer)
  }

  return { st, available, trees, anyConnected, anyRemote, refresh, connect, test, copy, openForm, closeForm, setInput, skipCleanup, stop }
}

function backupPill(t) {
  const words = {
    ok: t.last_pushed_ago ? `Backed up ${t.last_pushed_ago}` : 'Connected',
    fail: 'Not backing up',
    wait: t.host === 'folder' ? 'Ready to test' : t.public_key && !(t.test?.ok) ? 'One step left' : 'Starting',
    off: 'Not connected',
  }[t.level] ?? 'Not connected'
  return h('span', { class: `welcome-backup-pill welcome-backup-${t.level}${t.pushing ? ' welcome-backup-moving' : ''}` },
    h('span', { class: 'welcome-backup-dot', 'aria-hidden': 'true' }),
    t.pushing ? 'Backing up…' : words)
}

function backupSaid(backup, tree) {
  const s = backup.st.said[tree]
  if (!s) return null
  return h('p', { class: `welcome-backup-said ${s.ok ? 'welcome-backup-said-ok' : 'welcome-backup-said-bad'}`, role: s.ok ? 'status' : 'alert' },
    s.ok ? h('span', { class: 'welcome-backup-tick', 'aria-hidden': 'true' }) : null,
    s.words)
}

function backupForm(backup, t) {
  const id = `welcome-backup-remote-${t.id}`
  const busy = backup.st.busy[t.id] === 'connect'
  return h('form', {
    class: 'welcome-backup-form welcome-backup-flow',
    key: `welcome-backup-form-${t.id}`,
    onsubmit: event => {
      event.preventDefault()
      backup.connect(t.id, backup.st.input[t.id] ?? '')
    },
  },
  h('label', { for: id, class: 'welcome-backup-label', text: 'Repository address' }),
  h('input', {
    id,
    key: id,
    class: 'field-input welcome-backup-mono',
    type: 'text',
    inputmode: 'url',
    autocomplete: 'off',
    autocapitalize: 'off',
    spellcheck: 'false',
    placeholder: `git@host:you/${t.id === 'vault' ? 'vault' : 'ai-spaces'}.git`,
    value: backup.st.input[t.id] ?? '',
    disabled: busy,
    oninput: event => backup.setInput(t.id, event.currentTarget.value),
  }),
  h('p', {
    class: 'note',
    text: `An empty, private repository for your ${t.name} alone. Paste an SSH address, a repository page URL, or a local git path. On GitHub: New repository, choose Private, and add nothing to it.`,
  }),
  backupSaid(backup, t.id),
  h('div', { class: 'welcome-backup-actions' },
    h('button', { class: 'pillbtn primary', type: 'submit', disabled: busy, text: busy ? 'Checking…' : 'Continue' }),
    h('button', { class: 'pillbtn content-mark', type: 'button', disabled: busy, text: 'Cancel', onclick: () => backup.closeForm(t.id) })))
}

function backupKeyStep(backup, t) {
  const busy = backup.st.busy[t.id] === 'test'
  const where = t.host === 'github' ? 'GitHub' : t.host === 'gitlab' ? 'GitLab' : t.host === 'gitea' ? 'your host' : null
  if (t.host === 'folder') {
    return h('div', { class: 'welcome-backup-key welcome-backup-flow', key: `welcome-backup-key-${t.id}` },
      h('p', { class: 'welcome-backup-step-title', text: 'Test this repository location' }),
      h('p', { class: 'note welcome-backup-mono', text: t.remote }),
      h('p', { class: 'note', text: 'This is a path on this machine, so no SSH deploy key is needed.' }),
      backupSaid(backup, t.id),
      h('div', { class: 'welcome-backup-actions' },
        h('button', { class: 'pillbtn primary', type: 'button', disabled: busy, text: busy ? 'Testing…' : 'Test connection', onclick: () => backup.test(t.id) })),
      h('button', { class: 'welcome-backup-quiet', type: 'button', onclick: () => backup.openForm(t.id, t.remote) }, 'Use another repository'))
  }
  return h('div', { class: 'welcome-backup-key welcome-backup-flow', key: `welcome-backup-key-${t.id}` },
    h('p', { class: 'welcome-backup-step-title', text: 'Give Loam a key to this repository' }),
    h('p', { class: 'note', text: 'It opens this one repository and nothing else in your account, and it never leaves this machine.' }),
    h('div', { class: 'welcome-backup-keybox' },
      h('code', { class: 'welcome-backup-keytext', text: t.public_key || '' }),
      h('button', {
        class: 'pillbtn content-mark welcome-backup-copy',
        type: 'button',
        'aria-label': 'Copy the key',
        onclick: () => backup.copy(t.id, t.public_key),
      },
      backup.st.copied === t.id ? icon.check(15) : icon.copy(15),
      backup.st.copied === t.id ? 'Copied' : 'Copy')),
    h('ol', { class: 'welcome-backup-steps' }, ...(t.steps ?? []).map(step => h('li', { text: step }))),
    backupSaid(backup, t.id),
    h('div', { class: 'welcome-backup-actions' },
      t.add_key_at ? h('a', { class: 'pillbtn content-mark', href: t.add_key_at, target: '_blank', rel: 'noopener noreferrer' }, `Open deploy keys${where ? ` on ${where}` : ''}`, icon.external(14)) : null,
      h('button', { class: 'pillbtn primary', type: 'button', disabled: busy, text: busy ? 'Testing…' : 'I’ve added it: test', onclick: () => backup.test(t.id) })),
    h('button', { class: 'welcome-backup-quiet', type: 'button', onclick: () => backup.openForm(t.id, t.remote) }, 'Use another repository'))
}

function backupConnected(backup, t) {
  const repoLabel = (t.web || '').replace(/^https:\/\//, '') || t.remote
  return h('div', { class: 'welcome-backup-body' },
    h('p', { class: 'welcome-backup-where' },
      t.web
        ? h('a', { class: 'welcome-backup-repo', href: t.web, target: '_blank', rel: 'noopener noreferrer' }, repoLabel, icon.external(13))
        : h('span', { class: 'welcome-backup-repo welcome-backup-mono', text: repoLabel })),
    h('p', { class: 'note', text: t.pushing ? 'The first backup is running now.' : t.last_pushed_ago ? 'Everything on this machine is in the repository.' : 'Connected. Backups run automatically.' }),
    backupSaid(backup, t.id),
    h('button', { class: 'welcome-backup-quiet', type: 'button', onclick: () => backup.openForm(t.id, t.remote) }, 'Use another repository'))
}

function backupCard(backup, t) {
  let body
  if (backup.st.open[t.id] || (!t.remote && backup.st.open[t.id] !== false && backup.st.input[t.id])) body = backupForm(backup, t)
  else if (!t.remote) {
    body = h('div', { class: 'welcome-backup-body' },
      h('p', { class: 'note', text: TREE_SUB[t.id] }),
      backupSaid(backup, t.id),
      h('div', { class: 'welcome-backup-actions' },
        h('button', { class: 'pillbtn', type: 'button', text: 'Connect a repository', onclick: () => backup.openForm(t.id) })))
  } else if (t.level === 'wait' && !t.test?.ok) body = backupKeyStep(backup, t)
  else body = backupConnected(backup, t)

  const iconFn = icon[TREE_ICON[t.id]] ?? icon.folder
  return h('section', { class: 'card welcome-backup-card', key: `welcome-backup-${t.id}`, 'aria-label': t.name },
    h('header', { class: 'welcome-backup-head' },
      h('span', { class: 'welcome-backup-icon', 'aria-hidden': 'true' }, iconFn(18)),
      h('h2', { class: 'welcome-backup-title', text: t.name }),
      backupPill(t)),
    body)
}

function backupStep({ backup, continueWith, skipBackup }) {
  const ready = backup.anyConnected()
  const trees = backup.trees()
  const bothConnected = trees.every(t => Boolean(t.remote) && (Boolean(t.test?.ok) || t.level === 'ok' || Boolean(t.pushing)))
  return h('main', { class: 'welcome welcome-backup', key: 'welcome-backup' }, h('div', { class: 'welcome-inner' },
    h('div', { class: 'welcome-mark', 'aria-hidden': 'true', text: '✳' }),
    h('p', { class: 'kicker', text: 'Keep everything safe' }),
    h('h1', { class: 'display', text: 'Back up your Loam' }),
    h('p', { class: 'welcome-intro', text: 'Copy your vault and your agents to private git repositories of your own, so nothing is lost if this machine goes away.' }),
    h('p', { class: 'note', text: 'Optional. Works with GitHub, GitLab, Codeberg, your own server, or any git remote this machine can reach.' }),
    backup.st.error ? h('p', { class: 'reason', role: 'alert', text: backup.st.error }) : null,
    h('div', { class: 'welcome-backup-cards' }, trees.map(t => backupCard(backup, t))),
    h('div', { class: 'welcome-continue' },
      ready
        ? h('button', { class: 'primary', type: 'button', text: 'Meet your first agent', onclick: continueWith })
        : h('button', { class: 'pillbtn secondary', type: 'button', text: 'Skip for now', onclick: skipBackup ?? continueWith }),
      h('p', {
        class: 'note',
        role: 'status',
        text: ready
          ? (bothConnected ? 'You’re backed up. Let’s say hello.' : 'You can also connect the other repository in Settings › Plugins later.')
          : 'You can turn backup on anytime in Settings › Plugins.',
      }))))
}

export function welcomeScreen({ providers, signIn, continueWith, retry, error = '', checking = false, step = 'providers', backup = null, skipBackup = null }) {
  if (!checking && step === 'backup' && backup?.available()) {
    return backupStep({ backup, continueWith, skipBackup })
  }
  const choices = providers.filter(p => ['claude-code', 'codex', 'antigravity'].includes(p.id))
  const connected = choices.some(p => p.connected)
  const hasBackupStep = Boolean(backup?.available())
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
      h('div', { class: 'welcome-continue' }, h('button', { class: 'primary', disabled: !connected, text: hasBackupStep ? 'Continue' : 'Meet your first agent', onclick: continueWith }),
        h('p', { class: 'note', role: 'status', text: connected ? (hasBackupStep ? 'You’re connected.' : 'You’re connected. Let’s say hello.') : 'Connect one provider to continue.' }))]))
}
