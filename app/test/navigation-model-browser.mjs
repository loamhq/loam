import assert from 'node:assert/strict'
import { mkdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { profileHost } from './agent-profile-host.mjs'
import { filesHost } from './files-host.js'
import { listAnswer, readAnswer, row } from './fixtures/files-contract.js'

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
      const thread = () => page.locator('.tt-row[title="A little room for the week"]').click()
      const folder = name => page.locator('.fs-entry .setrow').filter({ has: page.locator('.fs-name').filter({ hasText: name }) }).first()
      const vault = page.getByRole('button', { name: /Vault Your notes/ }).first()
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
      const chooseNotes = async () => { await vault.click(); await folder('Notes').click(); await folder('plan.md').waitFor() }

      const list = wide ? page.locator('.workspace-list') : page.locator('#app')
      const listBack = () => button('Back', list).click()
      const panelBack = () => button('Back', main).click()
      const old = row('vault', 'Notes/Old')
      const outline = row('vault', 'Notes/Old/outline.md')
      const listRoute = app.routes['GET /v1/files/list'], readRoute = app.routes['GET /v1/files/read']
      app.routes['GET /v1/files/list'] = (req, res, url) => url.searchParams.get('path') === old.path
        ? json(res, listAnswer(old, [outline])) : listRoute(req, res, url)
      app.routes['GET /v1/files/read'] = (req, res, url) => url.searchParams.get('path') === outline.path
        ? json(res, readAnswer(outline, '# Meeting outline\n\nDecide what to carry into next week.', 'sha256:outline')) : readRoute(req, res, url)

      // A chat stays mounted through Files → Vault → Notes → Old.
      await start('/'); await thread(); await chatLink.waitFor()
      await snap('chat')
      const chatId = await page.evaluate(() => {
        window.firstChat = navTest.threads.st.open
        return firstChat.thread.id
      })
      if (!wide && before) await panelBack()
      await tab('Files'); await chooseNotes(); await folder('Old').click(); await folder('outline.md').waitFor()
      await snap('browse')
      if (!before) {
        assert.equal(await page.evaluate(() => firstChat === navTest.threads.st.open), true)
        if (wide) await chatLink.waitFor()
      }
      // Open the new document in main, then walk only main.
      await folder('outline.md').click(); await preview.getByText('Meeting outline').waitFor()
      await snap('file')
      await panelBack()
      if (!before) {
        await chatLink.waitFor()
        assert.equal(await page.evaluate(() => navTest.state.mainItem.id), chatId)
      }
      await snap('panel-back')
      if (before) {
        if (wide) await start('/threads/a-quieter-week')
        else await start('/files/vault/Notes')
      }
      if (wide) {
        if (before) { await tab('Files'); await chooseNotes() }
        const target = before ? folder('plan.md') : folder('outline.md')
        await target.click({ modifiers: ['Alt'] })
        const side = page.locator('[data-panel=side]')
        await side.locator('.fs-preview').waitFor()
        if (!before) {
          await chatLink.waitFor()
          await page.evaluate(() => navTest.panels.openSide({kind: 'thread', id: 'general'}))
          await side.locator('.tt-head').waitFor()
          await main.locator('.tt-stream').click()
          await nativeBack(); await side.locator('.fs-preview').waitFor()
          await chatLink.waitFor()
        }
        if (!before) {
          await page.reload(); await side.locator('.fs-preview').waitFor(); await chatLink.waitFor()
          await side.getByRole('button', { name: 'Back', exact: true }).click()
          await side.waitFor({ state: 'hidden' }); await chatLink.waitFor()
          await folder('outline.md').click({ modifiers: ['Alt'] })
          await side.locator('.fs-preview').waitFor()
        }
        await snap('side')
        await page.evaluate(() => navTest.panels.openSide({ kind: 'profile', id: 'sprout' }))
        await side.locator('.profile-body').waitFor()
        if (!before) {
          await side.getByRole('tab', { name: 'Files' }).click()
          await side.getByRole('button', { name: /^Soul(?: |$)/ }).click()
          await side.locator('.fs-preview').waitFor()
          await side.getByRole('button', { name: 'Back', exact: true }).click()
          await side.getByRole('button', { name: /^Soul(?: |$)/ }).waitFor()
          await chatLink.waitFor()
        }
        await snap('side-profile')
        await button('Close side panel', side).click()
      } else if (!before) {
        assert.equal(await page.getByRole('button', { name: 'Open to the side', exact: true }).count(), 0)
        assert.equal(await page.evaluate(() => navTest.panels.openSide({ kind: 'thread', id: 'general' })), false)
        await chatLink.waitFor()
      }
      // Settings → agent file → native Back restores the foreground panel.
      await button('Settings').click(); await settings.waitFor()
      await settings.getByRole('button', { name: /^Agents(?: |$)/ }).click()
      await settings.locator('section[aria-label="Agents"] .setrow').first().click()
      const profile = page.locator('.agent-profile')
      await profile.getByRole('tab', { name: 'Files', exact: true }).click()
      await profile.getByRole('button', { name: /^Soul(?: |$)/ }).click()
      await preview.getByText('Make space for thoughtful work.', { exact: true }).waitFor()
      await snap('settings-file')
      await nativeBack()
      if (!before) {
        await chatLink.waitFor()
        await settings.waitFor({ state: 'detached' })
        if (wide) {
          await tab('Files'); await chooseNotes()
          await panelBack()
          await main.locator('.fs-preview,.tt-head').waitFor({state:'hidden'})
          await folder('plan.md').waitFor()
          await nativeBack(); await folder('Notes').waitFor()
        }
      }

      // Cold file → reload → folder → Vault → Files, where Back stops.
      await start('/files/vault/Notes/plan.md'); await preview.waitFor()
      await page.reload(); await preview.waitFor()
      await panelBack(); await folder('plan.md').waitFor()
      await snap('deep-back')
      await listBack(); await folder('Notes').waitFor()
      await nativeBack(); await vault.waitFor()
      if (!before) {
        await noBack(); await blockedBack(); await vault.waitFor()
        await chooseNotes(); await folder('plan.md').click(); await preview.waitFor()
        await panelBack(); await folder('plan.md').waitFor()
        await listBack(); await folder('Notes').waitFor()
        await listBack(); await vault.waitFor(); await noBack()
        await tab('Chats'); await thread(); await chatLink.waitFor()
        await tab('Files'); await chooseNotes()
        await folder('plan.md').click(); await preview.waitFor()
        await page.reload(); await preview.waitFor()
        await nativeBack(); await chatLink.waitFor()
        await nativeBack(); await folder('Notes').waitFor()
        if (wide) await chatLink.waitFor()
        else {
          await button('Settings').click(); await settings.waitFor()
          await settings.getByRole('button', { name: /^Agents(?: |$)/ }).click()
          await settings.locator('section[aria-label="Agents"] .setrow').first().click()
          await profile.getByRole('tab', { name: 'Files', exact: true }).click()
          await profile.getByRole('button', { name: /^Soul(?: |$)/ }).click()
          await preview.waitFor(); await panelBack()
          await folder('Notes').waitFor()
          await button('Return to open panel').click(); await chatLink.waitFor()
        }
      }
      await start('/threads/a-quieter-week'); await chatLink.waitFor()
      if (!wide && before) await panelBack()
      await tab('Apps'); await page.locator('.apps-row').first().waitFor()
      if (!before && wide) await chatLink.waitFor()
      if (!before && !wide) assert.equal(await page.locator('.panel-history-back:visible').count(), 0)
      await snap('apps')
      assert.deepEqual(errors, [])
      console.log(`${width} ${mode} passed`)
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
