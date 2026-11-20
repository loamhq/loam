// Panels own their content: all ordered rail pairs, mount identity and URL history.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), reports = []
const out = process.env.PANEL_ITEMS_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const width of [1440, 1920]) for (const beside of [false, true]) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture(), label = `${width}-${beside ? 'side' : 'single'}-${mode}`, checks = [], errors = []
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: mode, reducedMotion: 'reduce', serviceWorkers: 'block' })
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', error => errors.push(error.message))
    const apps = ['Garden', 'Notebook'].map(name => ({ name, url: `https://${name.toLowerCase()}.example.com/`, owner: app.f.agents[0].id, frame: true, source: 'yours', access: 'private' }))
    for (const [key, handler] of Object.entries(filesHost().routes)) if (key.includes('/v1/files/')) app.routes[key] = handler
    app.routes['GET /v1/apps'] = (req, res) => json(res, { apps, sources: ['yours'] })
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { desktop: { theme: 'latte', mode, size: 100 } })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__sidebar = { state, panels, threads, sidePanel, page, apps, schedule };' }))
    await context.route(/^https:\/\/(garden|notebook)\.example\.com\//, route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><style>body{font:18px system-ui;padding:32px;background:${mode === 'dark' ? '#242722;color:#e4e7dc' : '#faf8f4;color:#35352b'}}button{padding:12px}</style><h1>${new URL(route.request().url()).hostname.split('.')[0]}</h1><p>Keep this place while choosing the next item.</p><button id="counter" onclick="this.textContent=Number(this.textContent)+1">0</button>` }))
    const main = page.locator('[data-panel=main]'), side = page.locator('[data-panel=side]')
    const rail = id => page.locator(`.rail-tabs [title=${id}]`)
    const capture = async name => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      if (out) await page.screenshot({ path: path.join(out, `${name}-${label}.png`), animations: 'disabled' })
    }
    const ready = async kind => {
      if (kind === 'chats') { await main.locator('.tt-stream').waitFor(); await page.waitForFunction(() => !__sidebar.threads.st.open.loading && !__sidebar.threads.st.open.refreshing) }
      if (kind === 'files') { await main.locator('.fs-preview').waitFor(); await page.waitForFunction(() => !__sidebar.page.files.st.file.loading) }
      if (kind === 'apps') await main.frameLocator('iframe').locator('#counter').waitFor()
    }
    const select = async kind => {
      if (kind === 'chats') await page.evaluate(() => __sidebar.threads.open('a-quieter-week'))
      if (kind === 'files') await page.evaluate(() => __sidebar.page.goFiles('vault', 'Notes/plan.md', { isFile: true }))
      if (kind === 'apps') await page.evaluate(app => __sidebar.panels.openMain({ kind: 'app', app }), apps[0])
      await ready(kind)
    }
    const saveIdentity = async kind => {
      await page.evaluate(kind => { window.__kept = { kind, item: kind === 'chats' ? __sidebar.threads.st.open : kind === 'files' ? __sidebar.page.files.st.file : __sidebar.apps.st.selected,
        node: document.querySelector('[data-panel=main] .tt-stream, [data-panel=main] .fs-file, [data-panel=main] iframe'), url: location.href,
        side: document.querySelector('[data-panel=side] iframe'), sideWindow: document.querySelector('[data-panel=side] iframe')?.contentWindow } }, kind)
    }
    const preserved = async () => {
      assert.deepEqual(await page.evaluate(() => ({ node: __kept.node.isConnected, item: __kept.item === (__kept.kind === 'chats' ? __sidebar.threads.st.open : __kept.kind === 'files' ? __sidebar.page.files.st.file : __sidebar.apps.st.selected), url: location.href === __kept.url,
        side: !__kept.side || (__kept.side === document.querySelector('[data-panel=side] iframe') && __kept.sideWindow === __kept.side.contentWindow) })), { node: true, item: true, url: true, side: true })
      if (beside) assert.equal(await side.frameLocator('iframe').locator('#counter').innerText(), '1')
    }
    const listShown = async id => {
      await page.waitForFunction(id => __sidebar.state.navigator === id, id.toLowerCase() === 'chats' ? 'chats' : id.toLowerCase())
      await page.waitForFunction(() => !__sidebar.state.collapsed && !__sidebar.page.store.sidebarHidden)
      const target = id === 'Chats' ? '.tt-list' : id === 'Files' ? '.fs-folders' : '.apps-list'
      if (id === 'Files') await page.locator('.listcol .fs-homehead,.listcol .fs-head').waitFor()
      else await page.locator(`.listcol${target},.listcol ${target}`).waitFor()
      await page.waitForFunction(() => document.querySelector('.listcol') && !document.querySelector('.listcol').hasAttribute('inert'))
      assert.equal(await page.locator('.listcol').getAttribute('inert'), null)
      assert.ok((await page.locator('.listcol').boundingBox()).width > 250)
    }
    try {
      await page.goto(`${app.url}/threads/a-quieter-week`)
      await page.waitForFunction(() => globalThis.__sidebar?.state.agentsReady)
      if (beside) {
        await page.evaluate(app => __sidebar.panels.openSide({ kind: 'app', app }), apps[1])
        await side.frameLocator('iframe').locator('#counter').click()
      }
      for (const kind of ['chats', 'files', 'apps']) {
        await select(kind)
        await page.evaluate(() => __sidebar.page.setCollapsed(false))
        await page.locator('.listcol').getByTitle('Hide sidebar for a focused view').click()
        await page.waitForFunction(() => __sidebar.state.collapsed && __sidebar.page.store.sidebarHidden)
        await saveIdentity(kind)
        await rail({ chats: 'Chats', files: 'Files', apps: 'Apps' }[kind]).click()
        await listShown({ chats: 'Chats', files: 'Files', apps: 'Apps' }[kind])
        await capture(`after-${kind}`)
          await preserved()
          for (const from of ['Chats', 'Files', 'Apps']) for (const to of ['Chats', 'Files', 'Apps']) for (const hidden of [false, true]) {
            await rail(from).click(); await listShown(from); await preserved()
            if (hidden) await page.locator('.listcol').getByTitle('Hide sidebar for a focused view').click()
            await rail(to).click(); await listShown(to); await preserved()
            assert.ok((await rail(to).getAttribute('class')).includes(' on'))
            checks.push(`${kind}: ${from} → ${to}, list ${hidden ? 'hidden' : 'visible'}, panels retained`)
          }
          const other = { chats: 'Files', files: 'Apps', apps: 'Chats' }[kind]
          await rail(other).click(); await listShown(other); await preserved()
          await capture(`after-${kind}-browse`)
          checks.push(`${kind}: all three rail choices reveal their list and preserve main/side identity and address`)
      }
      {
        app.routes['PUT /v1/files/text'] = (req, res) => { res.writeHead(503, { 'content-type': 'application/json' }); res.end('{"error":"This file could not be saved."}') }
        await select('files'); await page.evaluate(() => { __sidebar.page.files.edit('Keep this unsaved file.'); __sidebar.schedule() })
        await rail('Chats').click(); await listShown('Chats')
        assert.equal(await page.evaluate(() => __sidebar.page.files.st.file.draft), 'Keep this unsaved file.')
        assert.equal(await page.getByRole('button', { name: 'Keep editing', exact: true }).count(), 0)
        await page.locator('.tt-row').first().click()
        await page.getByRole('button', { name: 'Keep editing', exact: true }).waitFor()
        await page.getByRole('button', { name: 'Keep editing', exact: true }).click()
        assert.equal(await page.evaluate(() => __sidebar.page.files.st.file.draft), 'Keep this unsaved file.')
        checks.push('Rail browsing retains an unsaved file; choosing a different document still asks before leaving')
        await page.evaluate(() => __sidebar.page.files.reload()); await ready('files')
        await rail('Apps').click(); await listShown('Apps')
        await page.getByRole('button', { name: 'Open Garden', exact: true }).click(); await ready('apps')
        await rail('Chats').click(); await listShown('Chats')
        await page.locator('.tt-row').first().click(); await ready('chats')
        await rail('Apps').click(); await listShown('Apps')
        assert.equal(await page.locator('.apps-list .apps-row[aria-current=page]').count(), 0)
        await rail('Files').click(); await listShown('Files')
        await page.locator('.listcol .fs-entry .setrow').filter({ hasText: 'plan.md' }).click(); await ready('files')
        assert.equal(await page.evaluate(() => __sidebar.state.navigator), 'files')
        if (beside) assert.equal(await side.frameLocator('iframe').locator('#counter').innerText(), '1')
        checks.push('Choosing rows from each revealed list still opens that item in main and leaves side running')
        await rail('Chats').click(); await listShown('Chats'); await saveIdentity('files')
        await page.evaluate(() => window.dispatchEvent(new Event('focus')))
        await page.waitForFunction(() => __sidebar.threads.st.listLive)
        await app.f.api('PATCH', '/threads/a-quieter-week', { title: 'Updated while browsing beside a file' })
        await page.locator('.listcol .tt-row[title="Updated while browsing beside a file"]').waitFor()
        await preserved()
        checks.push('The visible Chats list keeps its live updates while main holds a file')
        for (const kind of ['chats', 'files', 'apps']) {
          await select(kind)
          const address = new URL(page.url()).pathname + new URL(page.url()).search
          if (kind === 'apps') assert.equal(new URL(page.url()).pathname, '/apps')
          await rail(kind === 'chats' ? 'Apps' : 'Chats').click()
          assert.equal(new URL(page.url()).pathname + new URL(page.url()).search, address)
          await page.reload(); await page.waitForFunction(() => globalThis.__sidebar?.state.agentsReady); await ready(kind)
          if (beside) await side.frameLocator('iframe').locator('#counter').waitFor()
          const next = kind === 'chats' ? 'files' : 'chats'
          await select(next)
          await page.goBack(); await page.waitForFunction(address => location.pathname + location.search === address, address); await ready(kind)
          await page.goForward(); await ready(next)
          await page.goBack(); await ready(kind)
          checks.push(`${kind}: reload and Back/Forward restore main and side from the address after rail browsing`)
        }

      }
      if (beside) {
        await select('chats')
        await page.evaluate(app => __sidebar.panels.openSide({ kind: 'app', app }), apps[1])
        await select('files')
        await page.evaluate(app => __sidebar.panels.openSide({ kind: 'app', app }), apps[0])
        await page.goBack(); await ready('chats')
        await page.waitForFunction(() => (__sidebar.panels.st.side.app?.url ?? __sidebar.panels.st.side.url).includes('garden'))
        await page.goForward(); await ready('files')
        await page.waitForFunction(() => (__sidebar.panels.st.side.app?.url ?? __sidebar.panels.st.side.url).includes('garden'))
        checks.push('Back/Forward preserves the current side item while traversing main')
      }
      const registerNotes = () => {
        const scope = __sidebar.page.registry.scope({ id: 'fixture-notes' })
        scope.places.rail.section.add({ id: 'notes', title: 'Notes', icon: 'file', list: () => __sidebar.page.h('div', { class: 'list-head' }, 'Plugin notes') })
        scope.places.panel.item.add({ id: 'fixture-note', render: ({ item, panel }) => __sidebar.page.h('article', { class: 'fixture-note' }, `${panel}: ${item.id}`) })
      }
      await page.evaluate(registerNotes)
      await page.evaluate(() => __sidebar.page.commands.run('panel.open', { item: { kind: 'fixture-note', id: 'One' } }))
      await main.locator('.fixture-note').waitFor()
      await page.getByTitle('Notes', { exact: true }).click()
      assert.equal(await main.locator('.fixture-note').innerText(), 'main: One')
      await page.evaluate(() => __sidebar.page.commands.run('panel.open', { item: { kind: 'fixture-note', id: 'Two' }, panel: 'side' }))
      assert.equal(await side.locator('.fixture-note').innerText(), 'side: Two')
      await rail('Files').click()
      assert.equal(await main.locator('.fixture-note').innerText(), 'main: One')
      assert.equal(await side.locator('.fixture-note').innerText(), 'side: Two')
      await page.reload(); await page.waitForFunction(() => globalThis.__sidebar?.state.agentsReady)
      await page.evaluate(registerNotes)
      await main.locator('.fixture-note').waitFor(); await side.locator('.fixture-note').waitFor()
      assert.equal(await main.locator('.fixture-note').innerText(), 'main: One')
      assert.equal(await side.locator('.fixture-note').innerText(), 'side: Two')
      checks.push('A plugin registers an independent list and item kind for either slot, with identities restored after reload')
      assert.deepEqual(errors, [])
      reports.push({ width, beside, mode, checks, errors })
      console.log(`${label}: ${checks.length} checks passed`)
    } catch (error) {
      if (out) await capture('failure')
      console.error('Page errors:', errors); throw error
    } finally { await context.close(); await app.close() }
  }
  if (out) await writeFile(path.join(out, 'after.json'), JSON.stringify(reports, null, 2) + '\n')
  console.log(JSON.stringify({ cases: reports.length, checks: reports.reduce((sum, r) => sum + r.checks.length, 0), result: 'passed' }))
} finally { await browser.close() }
