import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import { fakeBroker, appAgainst, closing } from './harness.js'
import { row, rootsAnswer, listAnswer, readAnswer } from './fixtures/files-contract.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const out = process.env.AGENT_FILE_EVIDENCE
const baseline = process.env.AGENT_FILE_BASELINE === '1'
const hello = { id: 't-hello', title: 'Hello', default: 'iris', pinned: true, hello: true, unread: 0, members: [{ persona: 'iris' }] }
const iris = { id: 'iris', name: 'Iris', face: null }
const ref = (file = '') => ({ root: 'spaces', path: file ? `iris/${file}` : 'iris', exists: true })
const files = { workspace: ref(), soul: ref('soul.md'), config: ref('config.md'), memory: { map: ref('map.md'), folder: ref('memory') }, skills: { ...ref('skills'), count: 1 } }
const documents = {
  [files.soul.path]: '# Iris\n\nI help you get to know Loam. We can take it one step at a time.',
  [files.config.path]: '# Config\n\nAsk before making changes.',
  [files.memory.map.path]: '# Map\n\nRead the most recent notes when a conversation resumes.',
  'iris/memory/first-note.md': '# First note\n\nLeave space for the next question.',
  'iris/skills/orientation.md': '# Orientation\n\nShow people where to find their files.',
}
const resource = path => row('spaces', path)
const json = (body, status = 200) => (_, res) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const stream = body => (_, res) => { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(`data: ${JSON.stringify(body)}\n\n`) }

function routes(mode, state) {
  const listing = () => ({ threads: state.deleted ? [] : [hello], agents: [iris] })
  const message = { id: 'm1', author: 'iris', kind: 'message', preset: true, text: 'Hello. I’m Iris, your first agent. What should I call you?\n\nHere are my [soul file](/files/spaces/iris/soul.md), [config file](/files/spaces/iris/config.md), and [memory map](/files/spaces/iris/map.md).' }
  const record = { thread: hello, messages: [message], has_more: false, turn: null }
  const appearance = { theme: 'latte', mode, size: 100 }
  const documentAt = url => url.searchParams.get('path') || ''
  return {
    'GET /v1/owner': json({ id: 'you', name: 'Sam' }),
    'GET /v1/agents': json({ agents: [iris] }),
    'GET /v1/agents/iris': json({ agent: iris, identity: { id: iris.id, display: iris.name }, files, defaults: {}, status: {}, schedule: [], plugins: [] }),
    'GET /v1/agents/drafts': json({ drafts: [] }),
    'GET /v1/providers': json({ providers: [{ id: 'claude-code', name: 'Claude Code', connected: true }] }),
    'GET /v1/settings/appearance': json({ phone: appearance, desktop: appearance }),
    'GET /v1/threads': (req, res) => json(listing())(req, res),
    'GET /v1/threads/events': (req, res) => stream({ type: 'threads.snapshot', ...listing() })(req, res),
    'GET /v1/threads/t-hello': (req, res) => (state.deleted ? json({ error: 'Thread not found' }, 404) : json(record))(req, res),
    'GET /v1/threads/t-hello/events': stream({ type: 'thread.snapshot', turns: [], pending: [] }),
    'GET /v1/sessions': json({ sessions: [] }),
    'GET /v1/engineers': json({ engineers: [], default: { effective: {} } }),
    'GET /v1/system/reviewer': json({ name: 'Nightly reviewer' }),
    'GET /v1/system/persona': json({}),
    'GET /v1/plugins': json({ plugins: [] }),
    'GET /v1/files/roots': json({ ...rootsAnswer(), shortcuts: [] }),
    'GET /v1/files/stat': (req, res, url) => json(resource(documentAt(url)))(req, res),
    'GET /v1/files/resolve': (req, res, url) => {
      const path = url.searchParams.get('ref')?.replace('/files/spaces/', '')
      json({ status: 'found', resource: resource(path) })(req, res)
    },
    'GET /v1/files/read': (req, res, url) => {
      const file = documentAt(url)
      json(readAnswer(resource(file), documents[file], 'sha256:fixture'))(req, res)
    },
    'GET /v1/files/list': (req, res, url) => {
      const path = documentAt(url)
      const prefix = path ? `${path}/` : ''
      const children = new Set(Object.keys(documents).filter(file => file.startsWith(prefix)).map(file => prefix + file.slice(prefix.length).split('/')[0]))
      json(listAnswer(resource(path), [...children].map(resource)))(req, res)
    },
  }
}

if (out) await mkdir(out, { recursive: true })
try {
  for (const width of [1440, 390]) for (const mode of ['light', 'dark']) {
    const wide = width === 1440
    const state = { deleted: false }
    const broker = await fakeBroker(routes(mode, state)), app = await appAgainst(broker)
    const context = await browser.newContext({ viewport: { width, height: wide ? 900 : 844 }, colorScheme: mode, hasTouch: !wide, isMobile: !wide, reducedMotion: 'reduce', serviceWorkers: 'block' })
    try {
      const page = await context.newPage()
      page.setDefaultTimeout(6000)
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      const button = name => page.getByRole('button', { name, exact: true })
      const profile = page.locator('.agent-profile')
      const agentRow = page.locator('section[aria-label="Agents"] .setrow')
      const back = () => button('Back').click()
      const ready = () => page.getByRole('log', { name: 'Thread messages' }).getByText('What should I call you?', { exact: false }).waitFor()
      const fileReady = () => page.locator('.fs-preview').waitFor()
      const visible = locator => baseline ? locator.waitFor({ timeout: 900 }).then(() => true, () => false) : locator.waitFor().then(() => true)
      const capture = async name => {
        if (!out) return
        await page.evaluate(() => document.fonts.ready)
        await page.screenshot({ path: join(out, `${name}-${width}-${mode}.png`), animations: 'disabled' })
      }
      const launch = async () => { await page.goto('about:blank'); await page.goto(app.url) }
      const openSettingsProfile = async () => {
        await button('Settings').click()
        await page.locator('.sheet.settings').getByRole('button', { name: /^Agents(?: |$)/ }).click()
        await agentRow.click()
        await profile.waitFor()
      }
      const filesTab = profile.getByRole('tab', { name: 'Files', exact: true })
      const cases = [
        ['read-soul', 'Read soul', files.soul, false],
        ['soul', 'Soul', files.soul, false],
        ['config', 'Config', files.config, false],
        ['map', 'Every run', files.memory.map, false],
        ['workspace', 'Open workspace', files.workspace, true],
        ['memory', 'Memory folder', files.memory.folder, true],
        ['skills', 'Skills', files.skills, true],
      ]
      for (const [key, label, destination, folder] of cases) {
        await launch()
        await ready()
        if (!wide) await back()
        const panel = new URL(page.url()).pathname
        await openSettingsProfile()
        if (key !== 'read-soul') await filesTab.click()
        await profile.getByRole('button', { name: new RegExp(`^${label}(?: |$)`) }).click()
        if (folder) await page.locator('.fs-folder').getByText(({ workspace: 'soul.md', memory: 'first-note.md', skills: 'orientation.md' })[key], { exact: true }).waitFor()
        else await fileReady()
        await capture(`settings-${key}`)
        if (!baseline) assert.equal(new URL(page.url()).pathname, `/files/${destination.root}/${destination.path}`)
        if (folder && wide && baseline) await page.goBack()
        else if (key === 'config') await page.goBack()
        else await back()
        if (baseline) await visible(profile)
        else {
          await page.waitForURL(url => url.pathname === panel && !url.searchParams.has('modal'))
          await profile.waitFor({ state: 'detached' })
          if (wide) await ready()
          else await page.locator('.tt-row').first().waitFor()
        }
        if (key === 'read-soul' || key === 'workspace') await capture(`settings-${key}-back`)
        if (key === 'read-soul') await capture('settings-read-soul-back-again')
      }

      if (!baseline) {
        await page.goto(`${app.url}/panel?kind=profile&id=iris`)
        await filesTab.click()
        await profile.getByRole('button', { name: /^Soul(?: |$)/ }).click()
        await fileReady(); await back()
        await profile.waitFor()
        assert.equal(await filesTab.getAttribute('aria-selected'), 'true')
      }

      const openThreadFile = name => page.getByRole('link', { name, exact: true }).click(wide ? { modifiers: ['Control'] } : {})
      for (const [key, name] of [['soul', 'soul file'], ['config', 'config file'], ['map', 'memory map']]) {
        await launch()
        await ready()
        await openThreadFile(name)
        await fileReady()
        await capture(`thread-${key}`)
        await back()
        await ready()
        if (key === 'soul') await capture('thread-soul-back')
        await back()
        await page.locator('.tt-row').first().waitFor()
        if (key === 'soul') await capture('thread-soul-back-again')
      }

      if (wide) {
        await launch()
        await ready()
        await page.getByRole('link', { name: 'soul file', exact: true }).click()
        const side = page.getByRole('region', { name: 'Side panel' })
        await side.getByText('soul.md', { exact: true }).waitFor()
        await page.getByRole('link', { name: 'config file', exact: true }).click()
        await side.getByText('config.md', { exact: true }).waitFor()
        await side.getByRole('button', { name: 'Back', exact: true }).click()
        await side.getByText('soul.md', { exact: true }).waitFor()
        await ready()
        await capture('thread-side-back')
      }

      await launch()
      await ready()
      await openThreadFile('soul file')
      await fileReady()
      state.deleted = true
      await back()
      if (baseline) await page.getByRole('alert').filter({ hasText: 'This thread was deleted.' }).waitFor()
      else {
        await page.waitForURL(url => url.pathname === '/')
        await page.getByText(wide ? 'Choose an item' : 'Room for a new thought', { exact: true }).waitFor()
        await page.goForward()
        await fileReady()
        await page.goBack()
        await page.waitForURL(url => url.pathname === '/')
        assert.equal(await page.getByRole('button', { name: 'Refresh thread' }).count(), 0)
      }
      await capture('missing-thread-back')

      if (!baseline) {
        state.deleted = false
        await page.goto(`${app.url}/threads/t-missing`)
        await page.waitForURL(url => url.pathname === '/')
        await page.locator('.tt-row').first().waitFor()
        assert.ok(broker.seen.some(request => request.path === '/v1/threads/t-missing'), 'a starter install routes an explicit thread URL before recovering')
        if (wide) {
          const side = encodeURIComponent(JSON.stringify({ kind: 'thread', id: 't-missing' }))
          await page.goto(`${app.url}/threads/t-hello?side=${side}`)
          await ready()
          await page.waitForURL(url => !url.searchParams.has('side'))
          assert.equal(new URL(page.url()).pathname, '/threads/t-hello')
          await openSettingsProfile()
          await button('Read soul').click()
          await fileReady()
          state.deleted = true
          await back()
          await page.waitForURL(url => url.pathname === '/' && !url.searchParams.has('modal'))
          await profile.waitFor({ state: 'detached' })
          await page.getByText('Choose an item', { exact: true }).waitFor()
        }
        assert.deepEqual(errors, [])
      }
      console.log(`${baseline ? 'BASELINE' : 'PASS'} ${width} ${mode}: Settings profile files, thread-panel file links, Back and missing thread`)
    } finally {
      await context.close()
      await closing(app, broker)()
    }
  }
} finally {
  await browser.close()
}
