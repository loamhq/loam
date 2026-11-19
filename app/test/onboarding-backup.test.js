import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { text, all } from './browser.js'
import { welcomeScreen, createOnboardingBackup } from '../public/onboarding.js'
import { createPluginSettings } from '../public/plugin-settings.js'
import { settingsHome, settingsSection } from '../public/app-shape.js'
import { h } from '../public/ui.js'

const providers = [
  { id: 'claude-code', name: 'Claude Code', connected: true, models: [{ id: 'fable', name: 'Fable' }] },
  { id: 'codex', name: 'Codex', connected: false, models: [] },
  { id: 'antigravity', name: 'Antigravity', connected: false, models: [] },
]
const signIn = { card: p => ({ nodeType: 1, childNodes: [], textContent: p.name }) }

function backupRow(on = false) {
  return {
    id: 'git-backup',
    key: 'shipped:git-backup:',
    name: 'Git backup',
    version: '1.0.0',
    author: 'Loam contributors',
    layer: 'shipped',
    switch: 'plugin',
    effective: true,
    broken: null,
    on,
    source: { kind: 'loam' },
    touches: { network: [{ host: 'the git host you connect', why: 'Sends your history.' }], record: 'write', memory: 'write', server: true },
  }
}

test('onboarding offers backup step when git-backup is available and keeps direct flow when absent', () => {
  installDom()
  const noPlugin = welcomeScreen({ providers, signIn: { card: () => null }, continueWith() {}, retry() {} })
  assert.match(text(noPlugin), /Meet your first agent/)

  const pluginLoader = { st: { rows: [backupRow(false)] } }
  const backup = createOnboardingBackup({ api: async () => ({}), pluginLoader })
  const withPlugin = welcomeScreen({ providers, signIn: { card: () => null }, continueWith() {}, retry() {}, backup })
  assert.match(text(withPlugin), /Continue/)
  assert.doesNotMatch(text(withPlugin), /Meet your first agent/)
  backup.stop()
})

test('onboarding backup step supports non-GitHub remotes, local folder paths, startup retries, and one-click skip', async t => {
  installDom()
  const row = backupRow(false)
  const pluginLoader = { st: { rows: [row] } }
  const apiCalls = []
  const requests = []
  let statusCalls = 0
  let connectAttempts = 0
  const treeState = {
    vault: { id: 'vault', name: 'Vault', level: 'off', remote: null },
    spaces: { id: 'spaces', name: 'AI Spaces', level: 'off', remote: null },
  }
  const statusPayload = () => ({ ok: true, trees: [treeState.vault, treeState.spaces] })

  const backup = createOnboardingBackup({
    pluginLoader,
    delay: async () => {},
    api: async (method, path, body) => {
      apiCalls.push({ method, path, body })
      if (path === '/plugins/git-backup') row.on = Boolean(body.on)
      return { plugin: row }
    },
    request: async (url, options) => {
      const body = options?.body ? JSON.parse(options.body) : null
      requests.push({ url, method: options?.method, body })
      if (url.endsWith('/status')) {
        statusCalls++
        return { ok: true, status: 200, json: async () => statusPayload() }
      }
      if (url.endsWith('/connect')) {
        connectAttempts++
        if (connectAttempts === 1) return { ok: false, status: 502, json: async () => null }
        const { tree, remote } = body
        const isLocal = remote.startsWith('/') || remote.startsWith('file://')
        treeState[tree] = {
          id: tree,
          name: tree === 'vault' ? 'Vault' : 'AI Spaces',
          level: 'wait',
          remote,
          host: isLocal ? 'folder' : 'other',
          public_key: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey',
          steps: isLocal
            ? ['Test this location.']
            : ['Add this key to the repository as a deploy key (or an SSH key that can reach only this repository), with write access.', 'Then come back and test the connection.'],
          add_key_at: null,
          web: null,
          test: null,
        }
        return { ok: true, status: 200, json: async () => ({ ok: true, ...treeState[tree], status: statusPayload() }) }
      }
      if (url.endsWith('/test')) {
        const { tree } = body
        treeState[tree] = { ...treeState[tree], level: 'ok', test: { ok: true, words: 'Connected.' }, pushing: false, last_pushed_ago: 'just now' }
        return { ok: true, status: 200, json: async () => ({ ok: true, words: 'Connected.', status: statusPayload() }) }
      }
      throw new Error(`unexpected request ${url}`)
    },
  })
  t.after(() => backup.stop())

  let skipped = 0
  let continued = 0
  const draw = () => welcomeScreen({ providers, signIn, continueWith: () => continued++, skipBackup: () => { backup.skipCleanup(); skipped++ }, retry() {}, step: 'backup', backup })

  const initial = draw()
  const initialWords = text(initial)
  assert.match(initialWords, /Back up your Loam/)
  assert.match(initialWords, /GitHub, GitLab, Codeberg, your own server, or any git remote/)
  assert.match(initialWords, /Skip for now/)
  assert.doesNotMatch(initialWords, /Are you sure/)

  // Open Vault address form, verify portable guidance, connect an SSH remote on a custom server.
  const connectBtns = all(initial, 'pillbtn').filter(b => text(b) === 'Connect a repository')
  assert.equal(connectBtns.length, 2)
  connectBtns[0].fire('click')

  const formView = draw()
  const formText = text(formView)
  assert.match(formText, /SSH address, a repository page URL, or a local git path/)
  await backup.connect('vault', 'ssh://git@git.example.com:2222/sam/vault.git')

  assert.equal(apiCalls.length, 1)
  assert.deepEqual(apiCalls[0], { method: 'PATCH', path: '/plugins/git-backup', body: { on: true } })
  assert.equal(connectAttempts, 2, 'retries 502 while the newly started plugin server comes up')

  const keyView = draw()
  const keyWords = text(keyView)
  assert.match(keyWords, /Give Loam a key to this repository/)
  assert.match(keyWords, /ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey/)
  assert.match(keyWords, /I’ve added it: test/)

  await backup.test('vault')
  assert.equal(backup.anyConnected(), true)
  const doneView = draw()
  assert.match(text(doneView), /Meet your first agent/)
  assert.doesNotMatch(text(doneView), /Skip for now/)

  // Connecting AI Spaces via a local filesystem path skips deploy-key instructions.
  await backup.connect('spaces', '/srv/backups/spaces.git')
  const localKeyView = draw()
  assert.match(text(localKeyView), /This is a path on this machine, so no SSH deploy key is needed/)
  assert.match(text(localKeyView), /Test connection/)
  assert.doesNotMatch(text(localKeyView), /Give Loam a key/)
  await backup.test('spaces')
  assert.match(text(draw()), /You’re backed up\. Let’s say hello\./)
  assert.equal(statusCalls, 0)
})

test('skipping onboarding backup records Git backup as off, so the offer is made once', async t => {
  installDom()
  const row = backupRow(false)
  const patches = []
  const api = async (method, path, body) => { patches.push({ method, path, body }); row.on = Boolean(body.on); row.chosen = true; return { plugin: row } }
  const backup = createOnboardingBackup({
    pluginLoader: { st: { rows: [row] } },
    api,
    request: async () => ({ ok: false, status: 400, json: async () => ({ error: 'Bad remote.' }) }),
  })
  t.after(() => backup.stop())

  assert.equal(backup.offered(), true)
  // Skipping without opening a form says "off" in so many words: the switch is chosen.
  backup.skipCleanup()
  assert.deepEqual(patches, [{ method: 'PATCH', path: '/plugins/git-backup', body: { on: false } }])
  assert.equal(backup.offered(), false)

  // If onboarding enabled the plugin during a failed connect, skipping turns it back off.
  row.chosen = false
  const backup2 = createOnboardingBackup({
    pluginLoader: { st: { rows: [row] } },
    api,
    request: async () => ({ ok: false, status: 400, json: async () => ({ error: 'Bad remote.' }) }),
  })
  t.after(() => backup2.stop())
  await backup2.connect('vault', 'not-a-repo')
  assert.equal(row.on, true)
  backup2.skipCleanup()
  assert.deepEqual(patches.at(-1), { method: 'PATCH', path: '/plugins/git-backup', body: { on: false } })
  assert.equal(row.on, false)

  // A remote already saved (connected, key not yet tested) leaves it on.
  const before = patches.length
  const backup3 = createOnboardingBackup({
    pluginLoader: { st: { rows: [{ ...row, on: true }] } },
    api,
    request: async () => ({ ok: true, status: 200, json: async () => ({ ok: true, trees: [{ id: 'vault', name: 'Vault', level: 'wait', remote: 'git@host:you/vault.git' }, { id: 'spaces', name: 'AI Spaces', level: 'off', remote: null }] }) }),
  })
  t.after(() => backup3.stop())
  await backup3.refresh()
  backup3.skipCleanup()
  assert.equal(patches.length, before)
})

test('the backup offer stands only while Git backup is off and its switch was never set', () => {
  const offer = row => createOnboardingBackup({ api: async () => ({}), pluginLoader: { st: { rows: row ? [row] : [] } } }).offered()
  assert.equal(offer({ ...backupRow(false), chosen: false }), true, 'a fresh install')
  assert.equal(offer(backupRow(false)), true, 'a host older than `chosen` still offers on the welcome path')
  assert.equal(offer({ ...backupRow(false), chosen: true }), false, 'skipped once')
  assert.equal(offer({ ...backupRow(true), chosen: true }), false, 'backup on (this box)')
  assert.equal(offer({ ...backupRow(true), chosen: false }), false, 'on by any other route')
  assert.equal(offer({ ...backupRow(false), broken: 'bad manifest' }), false, 'broken')
  assert.equal(offer(null), false, 'no plugin')
  installDom()
  const backup = createOnboardingBackup({ api: async () => ({}), pluginLoader: { st: { rows: [{ ...backupRow(false), chosen: true }] } } })
  const screen = welcomeScreen({ providers, signIn: { card: () => null }, continueWith() {}, retry() {}, backup })
  assert.match(text(screen), /Meet your first agent/, 'a chosen switch: the welcome goes straight to Hello')
})

test('Settings plainly says when off-box backup is off and leaves connected Git backup unchanged', async () => {
  installDom()
  const offRow = backupRow(false)
  const loader = {
    st: { rows: [offRow], loaded: true, loading: false, error: '', failures: new Map(), writing: new Set() },
    toggle: async () => {},
    remove: async () => ({}),
    refresh: async () => {},
  }
  const host = {
    api: async () => ({ plugin: offRow }),
    registry: { entries: () => [], overrides: () => [], emit() {}, invoke() {} },
    settingsBack: (label) => h('button', { type: 'button', text: label }),
    settingsHead: (title) => h('h2', { text: title }),
  }
  const ui = createPluginSettings({ loader, theme: { value: { theme: 'latte' } }, host, changed() {} })

  const renderText = () => ui.view().filter(Boolean).map(text).join(' ')
  const listOff = renderText()
  assert.match(listOff, /Git backup/)
  assert.match(listOff, /Off-box backup is off/)

  await ui.open(offRow)
  const detailOff = renderText()
  assert.match(detailOff, /Off-box backup is off/)
  assert.match(detailOff, /Nothing is backed up off this machine yet/)
  const enableSwitch = ui.view().filter(Boolean).flatMap(n => all(n, 'schedule-switch'))[0]
  assert.equal(enableSwitch.getAttribute('aria-label'), 'Enable Git backup')
  assert.equal(enableSwitch.getAttribute('aria-checked'), 'false')

  // Connected / enabled owner: list row and detail header remain unchanged.
  const onRow = backupRow(true)
  loader.st.rows = [onRow]
  ui.st.selected = null
  const listOn = renderText()
  assert.match(listOn, /Enabled/)
  assert.doesNotMatch(listOn, /Off-box backup is off/)

  host.api = async () => ({ plugin: onRow })
  host.registry = {
    entries: () => [{ id: 'git-backup', group: 'plugin', owner: 'git-backup', title: 'Backup', entries: 'own' }],
    overrides: () => [],
    emit() {},
    invoke: () => h('p', { text: 'Everything is backed up.' }),
  }
  await ui.open(onRow)
  const detailOn = renderText()
  assert.doesNotMatch(detailOn, /Off-box backup is off/)
  assert.match(detailOn, /Everything is backed up\./)

  // Direct Settings aliases resolve to Plugins.
  assert.equal(settingsHome('backup'), 'plugins')
  assert.equal(settingsHome('git-backup'), 'plugins')
  assert.equal(settingsSection('backup'), 'plugins')
})
