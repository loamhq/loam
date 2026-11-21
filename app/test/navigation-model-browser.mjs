import assert from 'node:assert/strict'
import { mkdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { profileHost } from './agent-profile-host.mjs'
import { filesHost } from './files-host.js'
import { readAnswer, row } from './fixtures/files-contract.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const before = process.env.NAVIGATION_BASELINE === '1', out = process.env.NAVIGATION_EVIDENCE
const source = await readFile('public/app.js', 'utf8')
const json = (res, data) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(data)) }
if (out) await mkdir(out, { recursive: true })
try {
  for (const width of [1440, 390]) for (const mode of ['light', 'dark']) {
    const wide = width === 1440
    const app = await profileHost()
    const context = await browser.newContext({ viewport: { width, height: wide ? 900 : 844 }, colorScheme: mode, hasTouch: !wide, isMobile: !wide, reducedMotion: 'reduce', serviceWorkers: 'block' })
    try {
      const plan = '# Weekly plan\n\nRead the [planning chat](/threads/a-quieter-week) before Friday.'
      const routes = filesHost({ texts: { 'vault:Notes/plan.md': plan } }).routes
      for (const [key, handler] of Object.entries(routes)) if (key.includes('/files/')) app.routes[key] = handler
      app.routes['GET /v1/files/read'] = (req, res, url) => {
        if (url.searchParams.get('root') !== 'spaces') return routes['GET /v1/files/read'](req, res, url)
        json(res, readAnswer(row('spaces', 'sprout/soul.md'), '# Soul\n\nMake space for thoughtful work.', 'sha256:fixture'))
      }
      const records = await app.f.api('GET', '/threads/a-quieter-week')
      records.messages.at(-1).text += ' Here is [the weekly plan](/files/vault/Notes/plan.md).'
      records.board = { ...records.board, markdown: '# Board\n\n- [ ] [Read the outline](/files/vault/Notes/plan.md)\n' }
      app.routes['GET /v1/threads/a-quieter-week'] = (_, res) => json(res, records)
      const look = { theme: 'latte', mode, size: 100 }
      app.routes['GET /v1/settings/appearance'] = (_, res) => json(res, { phone: look, desktop: look })
      const apps = ['Notebook', 'Calendar'].map(name => ({ name, url: `https://notebook.example.com/${name}`, owner: 'you', frame: true, source: 'yours', access: 'private' }))
      app.routes['GET /v1/apps'] = (_, res) => json(res, { apps, sources: ['yours'] })
      await context.route('https://notebook.example.com/**', r => r.fulfill({ contentType: 'text/html', body: '<h1>Friday review</h1>' }))
      await context.route('**/app.js', r => r.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.navTest = { page, state, panels, threads };' }))
      const page = await context.newPage()
      const errors = []
      page.on('pageerror', e => errors.push(e.message))
      page.setDefaultTimeout(7000)
      await page.addInitScript(() => {
        globalThis.popCount = 0
        addEventListener('popstate', () => { globalThis.popCount++ }, true)
      })
      const main = wide ? page.locator('[data-panel=main]') : page.locator('#app')
      const button = (name, scope = page) => scope.getByRole('button', { name, exact: true })
      const tab = name => button(name, page.locator(wide ? '.rail-tabs' : '.navpill').filter({ visible: true })).click()
      const settings = page.locator('.sheet.settings')
      const enroute = path => page.waitForURL(url => url.pathname === path)
      const thread = () => page.locator('.tt-row[title="A little room for the week"]').click()
      const folder = name => page.locator('.fs-entry .setrow').filter({ has: page.locator('.fs-name').filter({ hasText: name }) }).first()
      const vault = page.getByRole('button', { name: /Vault Your notes/ }).first()
      const back = () => button('Back', wide ? page.locator('.desk') : main).click()
      const noBack = () => page.locator('.panel-history-back:visible').first().waitFor({ state: 'hidden' })
      const snap = async name => {
        if (!out) return
        await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
        await page.screenshot({ path: join(out, `${name}-${width}-${mode}.png`), animations: 'disabled' })
      }
      const nativeBack = async () => {
        const mouse = await context.newCDPSession(page)
        try {
          for (const type of ['mousePressed', 'mouseReleased']) await mouse.send('Input.dispatchMouseEvent', { type, button: 'back', buttons: type === 'mousePressed' ? 8 : 0, x: 2, y: 2, clickCount: 1 })
        } finally { await mouse.detach() }
      }
      const blockedBack = async () => {
        const count = await page.evaluate(() => popCount)
        await nativeBack()
        await page.waitForFunction(was => popCount >= was + 2, count)
      }
      const start = async path => {
        await page.goto(app.url + path)
        await page.waitForFunction(() => globalThis.navTest?.state.agentsReady)
      }
      const preview = main.locator('.fs-preview')
      const chatLink = main.getByRole('link', { name: 'the weekly plan', exact: true })
      const path = () => new URL(page.url()).pathname
      const chooseNotes = async () => { await vault.click(); await folder('Notes').click(); await folder('plan.md').waitFor() }

      await start('/')
      await tab('Files'); await chooseNotes()
      if (wide) { await folder('plan.md').click(); await preview.waitFor() }
      await tab('Apps'); await page.locator('.apps-row').first().waitFor()
      await snap('place-apps')
      if (!before) {
        await noBack(); await blockedBack(); assert.equal(path(), '/apps')
        if (wide) {
          await button('Open Notebook').click(); await main.locator('iframe[title=Notebook]').waitFor()
          await button('Open Calendar').click(); await main.locator('iframe[title=Calendar]').waitFor()
          await back(); await main.locator('iframe[title=Notebook]').waitFor()
          await nativeBack(); await main.getByText('Pick an app', { exact: true }).waitFor()
        } else {
          const opened = context.waitForEvent('page')
          await button('Open Notebook in browser').click()
          const external = await opened
          await external.waitForURL('https://notebook.example.com/Notebook')
          await external.close()
          assert.equal(path(), '/apps')
        }
      }
      await tab('Chats'); await page.locator('.tt-row').first().waitFor()
      await snap('place-chats')
      if (!before) { await noBack(); await blockedBack(); assert.equal(path(), '/') }
      await tab('Files')
      if (!before || !wide) await vault.waitFor()
      await snap('place-files')
      if (!before) { await noBack(); await blockedBack(); assert.equal(path(), '/files') }
      if (before && wide) await start('/files/vault/Notes')
      else await chooseNotes()
      await folder('plan.md').waitFor()
      await snap('files-folder')
      if (!before) {
        await back(); await enroute('/files/vault'); await folder('Notes').waitFor()
        await nativeBack(); await enroute('/files'); await vault.waitFor(); await noBack()
        await chooseNotes()
      }
      await folder('plan.md').click(); await preview.waitFor()
      if (!before) {
        await page.reload(); await preview.waitFor()
        await back(); await enroute('/files/vault/Notes'); await folder('plan.md').waitFor()
      } else if (!wide) { await page.goto(app.url + '/files/vault/Notes'); await folder('plan.md').waitFor() }

      if (wide) { await tab('Chats'); await thread(); await chatLink.waitFor() }
      const panelBefore = path()
      const historyBefore = await page.evaluate(() => history.length)
      const entryBefore = await page.evaluate(() => history.state?.panelIndex)
      await button('Settings').click(); await settings.waitFor()
      await settings.getByRole('button', { name: /^Agents(?: |$)/ }).click()
      await settings.locator('section[aria-label="Agents"] .setrow').first().click()
      const profile = page.locator('.agent-profile')
      await profile.waitFor()
      await snap('settings-agent')
      if (!before) assert.equal(await page.evaluate(() => history.length), historyBefore)
      await profile.getByRole('tab', { name: 'Files', exact: true }).click()
      await profile.getByRole('button', { name: /^Soul(?: |$)/ }).click()
      await preview.getByText('Make space for thoughtful work.', { exact: true }).waitFor()
      if (!before) assert.equal(await page.evaluate(() => history.state.panelIndex), entryBefore + 1)
      await snap('settings-file')
      await nativeBack()
      if (!before) {
        await enroute(panelBefore)
        await settings.waitFor({ state: 'detached' })
        assert.equal(new URL(page.url()).searchParams.has('modal'), false)
        if (wide) await chatLink.waitFor()
        else await folder('plan.md').waitFor()
        assert.equal(await page.evaluate(() => history.state.panelIndex), entryBefore)
      } else await profile.waitFor()
      await snap('settings-file-back')

      if (!before) {
        await start('/threads/a-quieter-week')
        await chatLink.waitFor()
        await chatLink.click(wide ? { modifiers: ['Control'] } : {})
        await preview.waitFor(); await enroute('/files/vault/Notes/plan.md')
        await preview.getByRole('link', { name: 'planning chat' }).click()
        await enroute('/threads/a-quieter-week'); await chatLink.waitFor()
        await nativeBack(); await preview.waitFor()
        await back(); await chatLink.waitFor()
        await button('Board', main).click()
        await main.getByRole('link', { name: 'Read the outline' }).click()
        await preview.waitFor(); await nativeBack()
        await main.locator('.tt-board').waitFor()
        await back(); await chatLink.waitFor()
        await nativeBack(); await enroute('/'); await page.locator('.tt-row').first().waitFor(); await noBack()

        if (wide) {
          await thread(); await chatLink.click()
          const side = page.locator('[data-panel=side]')
          await side.locator('.fs-preview').waitFor()
          await page.evaluate(() => navTest.panels.openSide({ kind: 'thread', id: 'general' }))
          await side.locator('.tt-head').waitFor()
          await nativeBack()
          await side.locator('.fs-preview').waitFor()
          assert.equal(path(), '/threads/a-quieter-week')
          await button('Close side panel', side).click()
          await back(); await enroute('/')
        }

        await button('Settings').click(); await settings.waitFor()
        const count = await page.evaluate(() => history.length)
        await settings.getByRole('button', { name: /^Connections/ }).click()
        await settings.locator('#connections-providers .setrow').filter({ hasText: 'Claude Code' }).first().click()
        await page.waitForURL('**/*section=provider*')
        await nativeBack(); await page.waitForURL('**/*section=connections')
        await nativeBack(); await page.waitForURL('**/*modal=settings')
        await nativeBack(); await settings.waitFor({ state: 'detached' })
        assert.equal(await page.evaluate(() => history.length), count)
        assert.equal(path(), '/')
      }
      await start('/files/vault/Notes/plan.md')
      await preview.waitFor()
      await snap('deep-file')
      if (!before) {
        await page.reload(); await preview.waitFor()
        await back(); await enroute('/files/vault/Notes'); await folder('plan.md').waitFor()
        await nativeBack(); await enroute('/files/vault'); await folder('Notes').waitFor()
        await nativeBack(); await enroute('/files'); await vault.waitFor(); await noBack()
        await blockedBack(); assert.equal(path(), '/files')
      }
      await start('/threads/a-quieter-week?view=board')
      await main.locator('.tt-board').waitFor()
      await snap('deep-board')
      await start('/?modal=settings&section=connections')
      await settings.getByRole('heading', { name: 'Connections', exact: true }).waitFor()
      await snap('settings-section')
      await page.keyboard.press('Escape')
      await settings.waitFor({ state: 'detached' })
      const count = await page.evaluate(() => history.length)
      if (wide) await button('Activity').click()
      else {
        await button('Settings').click(); await settings.waitFor()
        await settings.getByRole('button', { name: /^Activity/ }).click()
      }
      await page.locator('.activity-page').waitFor()
      await snap('activity')
      if (!before) {
        await nativeBack()
        await page.locator('.activity-page').waitFor({ state: 'detached' })
        assert.equal(await page.evaluate(() => history.length), count)
      }
      assert.deepEqual(errors, [])
      console.log(`${width} ${mode} passed`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
