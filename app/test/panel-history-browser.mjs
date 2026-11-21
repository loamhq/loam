// Exercise the real page, including the independently mounted Markdown editor.
import assert from 'node:assert/strict'
import { mkdir, readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { filesHost } from './files-host.js'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const out = process.env.PANEL_HISTORY_EVIDENCE
const before = process.env.PANEL_HISTORY_BASELINE === '1'
const text = '# The weekly plan\n\nSet aside Friday morning for a quiet review. Bring the open questions from our conversation.\n\n## Next steps\n\n- Read the notes from Monday.\n- Choose one idea to try next week.\n- Share the revised outline.\n\n' +
  Array.from({ length: 18 }, (_, i) => '## Review ' + (i + 1) + '\n\nKeep the original notes close by. Record the decision and who will follow up.').join('\n\n')
const plugin = new URL('../../plugins/doc-comments/', import.meta.url)
const source = await readFile('public/app.js', 'utf8')
if (out) await mkdir(out, { recursive: true })
try {
  for (const width of [1440, 390]) for (const mode of ['light', 'dark']) {
    const wide = width === 1440
    const app = await oneProductFixture({ APPS_DOMAIN: 'example.com', FILES_CONTENT_ORIGIN: 'https://preview.example.com' })
    const context = await browser.newContext({ viewport: { width, height: wide ? 900 : 844 }, colorScheme: mode, hasTouch: !wide, isMobile: !wide, reducedMotion: 'reduce', serviceWorkers: 'block' })
    try {
      Object.assign(app.routes, Object.fromEntries(Object.entries(filesHost({ texts: { 'vault:Notes/plan.md': text } }).routes).filter(([path]) => path.includes('/files/'))))
      const notebook = { name: 'Notebook', url: 'https://notebook.example.com/', owner: 'you', frame: true, source: 'yours', access: 'private' }
      const calendar = { ...notebook, name: 'Calendar', url: 'https://notebook.example.com/calendar' }
      app.routes['GET /v1/apps'] = (_, res) => res.end(JSON.stringify({ apps: [notebook, calendar], sources: ['yours'] }))
      await context.route(/^https:\/\/(preview|notebook)\.example\.com\//, r => r.fulfill({ contentType: 'text/html', body: `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><style>:root{color-scheme:light dark}body{font:17px/1.6 system-ui;margin:clamp(24px,6vw,84px);color:light-dark(#32372f,#e4e6d9);background:light-dark(#f7f8f2,#262923)}h1{font:2.4em/1.1 Georgia}li{margin:12px 0}</style><h1>Friday review</h1><p>Questions for the weekly planning meeting.</p><ul><li>Which part of the draft needs another read?</li><li>What can we finish before Friday?</li><li>What should we carry into next week?</li></ul>` }))
      const records = await app.f.api('GET', '/threads/a-quieter-week')
      const last = records.messages.at(-1)
      const notes = Array.from({ length: 10 }, (_, i) => ({ ...last, id: 'review-' + i, did: [], text: 'Review note ' + (i + 1) + '. The outline is ready for Friday.' }))
      const messages = [...notes.slice(0, 5), ...records.messages, ...notes.slice(5)]
      last.text += ' Here is [the weekly plan](/files/vault/Notes/plan.md).'
      app.routes['GET /v1/threads/a-quieter-week'] = (_, res) => res.end(JSON.stringify({ ...records, messages }))
      const appearance = { theme: 'latte', mode, size: 100 }
      app.routes['GET /v1/settings/appearance'] = (_, res) => res.end(JSON.stringify({ phone: appearance, desktop: appearance }))
      app.routes['GET /v1/plugins'] = (_, res) => res.end(JSON.stringify({ plugins: [{ id: 'doc-comments', version: '1.0.0', api: 0, layer: 'default', switch: 'plugin', effective: true, on: true, has: { page_main: true } }] }))
      for (const rel of await readdir(plugin, { recursive: true })) {
        if (!/^(page|assets)\/.*\.(js|css)$/.test(rel)) continue
        app.routes['GET /v1/plugins/doc-comments/files/' + rel] = async (_, res) => {
          res.setHeader('content-type', rel.endsWith('.css') ? 'text/css' : 'text/javascript')
          res.end(await readFile(new URL(rel, plugin)))
        }
      }
      const page = await context.newPage()
      page.setDefaultTimeout(8000)
      await page.addInitScript(() => {
        window.traversals = 0
        window.addEventListener('popstate', () => { window.traversals++ }, true)
      })
      const errors = []
      page.on('pageerror', e => { errors.push(e.message); console.error(e.message) })
      await page.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.testPage = page;' }))
      await page.route('**/x/doc-comments/**', route => route.fulfill({ json: { text, revision: 'r1', notes: [], links: [], can: { edit: true, comment: true }, reach: 'everywhere' } }))
      const capture = async name => {
        await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
        if (out) await page.screenshot({ path: join(out, name + '-' + width + '-' + mode + '.png'), animations: 'disabled' })
      }
      const open = async path => {
        await page.goto(app.url + path)
        await page.waitForFunction(() => globalThis.testPage?.state.agentsReady)
      }
      const chatReady = () => page.waitForFunction(() => testPage.threads.st.open && !testPage.threads.st.open.refreshing)
      const main = wide ? page.locator('[data-panel=main]') : page.locator('#app')
      const stream = main.locator('.tt-stream')
      const docBack = main.getByRole('button', { name: 'Back', exact: true })
      const docReady = () => main.locator('.cm-content').waitFor()
      const nativeBack = async () => {
        const mouse = await context.newCDPSession(page)
        try {
          for (const type of ['mousePressed', 'mouseReleased']) {
            await mouse.send('Input.dispatchMouseEvent', { type, button: 'back', buttons: type === 'mousePressed' ? 8 : 0, x: 2, y: 2, clickCount: 1 })
          }
        } finally { await mouse.detach() }
      }
      const backStops = async () => {
        const url = page.url()
        const count = await page.evaluate(() => traversals)
        await nativeBack()
        await page.waitForFunction(({ url, count }) => location.href === url && traversals >= count + 2, { url, count })
          .catch(async error => {
            const actual = await page.evaluate(() => ({ url: location.href, count: traversals, state: history.state }))
            throw new Error(`Back did not stop at ${url} from traversal ${count}. Received ${JSON.stringify(actual)}`, { cause: error })
          })
      }
      await open('/')
      await page.locator('.tt-row[title="A little room for the week"]').click()
      await chatReady()
      await page.locator('#draft').fill('Keep this thought for Friday.')
      await page.waitForFunction(() => !testPage.threads.st.open.landing)
      await main.locator('[data-message-id=m4]').evaluate(n => {
        n.scrollIntoView({ block: 'center' })
        n.closest('.tt-stream').dispatchEvent(new Event('scroll'))
      })
      const link = main.getByText('the weekly plan', { exact: true })
      await link.scrollIntoViewIfNeeded()
      await page.waitForFunction(() => testPage.threads.st.open.atEnd === false)
      const chatTop = await stream.evaluate(n => n.scrollTop)
      assert.ok(chatTop > 50)
      await capture('chat')
      await link.click(wide ? { modifiers: ['Control'] } : {})
      await docReady()
      await capture('doc-from-chat')
      if (!before) {
        await docReady()
        assert.equal(await main.locator('.dc-back,.panel-history-back').count(), 1)
        await page.evaluate(() => testPage.fileField.setSelectionRange(30, 32))
        await main.locator('.dc-scroll').evaluate(n => { n.scrollTop = 377 })
        await docBack.click()
        await chatReady()
        assert.equal(await page.locator('#draft').innerText(), 'Keep this thought for Friday.')
        await page.waitForFunction(top => Math.abs(document.querySelector('.tt-stream').scrollTop - top) < 4, chatTop)
        await main.locator('.tt-head button').first().focus()
        await page.keyboard.press('Control+]')
        await docReady()
        await page.waitForFunction(() => Math.abs(document.querySelector('.dc-scroll').scrollTop - 377) < 3)
        assert.deepEqual(await page.evaluate(() => [testPage.fileField.selectionStart, testPage.fileField.selectionEnd]), [30, 32])
        await page.goBack(); await chatReady()
        await page.locator('#draft').focus()
        await page.keyboard.press('Control+]')
        assert.match(page.url(), /threads\/a-quieter-week/)
        await page.goForward(); await docReady()
        await main.locator('.cm-content').click()
        await page.keyboard.press('Control+[')
        assert.match(page.url(), /files\/vault/)
      }
      if (wide) {
        await page.evaluate(() => testPage.panels.openSide({ kind: 'thread', id: 'general' }))
        await page.locator('[data-panel=side] .tt-head').waitFor()
        await page.evaluate(() => testPage.panels.openSide({ kind: 'file', root: 'vault', path: 'Notes/page.html' }))
        await page.locator('.side-head').waitFor()
        await page.frameLocator('[data-panel=side] iframe').getByText('Friday review').waitFor()
        await main.locator('.dc-scroll').evaluate(n => { n.scrollTop = 0 })
        await capture('side')
        if (!before) {
          const side = page.locator('[data-panel=side]')
          const path = new URL(page.url()).pathname
          await side.getByRole('button', { name: 'Back', exact: true }).click()
          await side.locator('.tt-head').waitFor()
          assert.equal(new URL(page.url()).pathname, path)
          await page.keyboard.press('Control+]')
          await side.locator('.fs-htmlframe').waitFor()
          await side.locator('iframe').evaluate(n => { window.sideFrame = n.contentWindow })
          const mouse = await context.newCDPSession(page)
          const header = await side.locator('.side-head').boundingBox()
          const mouseNavigate = async (button, buttons) => {
            for (const type of ['mousePressed', 'mouseReleased']) {
              await mouse.send('Input.dispatchMouseEvent', { type, button, buttons: type === 'mousePressed' ? buttons : 0,
                x: header.x + header.width / 2, y: header.y + header.height / 2, clickCount: 1 })
            }
          }
          await mouseNavigate('back', 8); await chatReady()
          assert.equal(await side.locator('iframe').evaluate(n => n.contentWindow === window.sideFrame), true)
          await mouseNavigate('forward', 16); await docReady()
          await mouse.detach()
        }
        await page.evaluate(() => testPage.panels.close())
      }
      if (!before) {
        // Opening the same thread again creates another visit. Its new
        // reading position must not overwrite the original history entry.
        await page.evaluate(() => testPage.threads.open('a-quieter-week'))
        await chatReady()
        await stream.evaluate(n => { n.scrollTop = n.scrollHeight; n.dispatchEvent(new Event('scroll')) })
        await page.waitForFunction(() => testPage.threads.st.open.atEnd)
        await main.locator('.tt-head button').first().focus()
        await page.keyboard.press('Meta+['); await docReady()
        await page.goBack(); await chatReady()
        await page.waitForFunction(top => Math.abs(document.querySelector('.tt-stream').scrollTop - top) < 4, chatTop)
      }
      await open('/files/vault/Notes')
      const list = page.locator('.fs-entry .setrow')
      await list.filter({ hasText: 'plan.md' }).click()
      await docReady()
      await capture('doc-from-list')
      if (!before) {
        await docBack.click()
        await list.filter({ hasText: 'plan.md' }).waitFor()
        await page.waitForURL('**/files/vault/Notes')
        await main.locator('.cm-content').waitFor({ state: 'detached' })
        await page.goForward(); await docReady()
      }
      await page.evaluate(() => testPage.goFiles('vault', 'Notes/page.html', { isFile: true }))
      await page.locator('.fs-livehead').waitFor()
      await main.frameLocator('iframe').getByText('Friday review').waitFor()
      await capture('web')
      if (wide) {
        await page.evaluate(() => testPage.panels.openSide({ kind: 'file', root: 'vault', path: 'Notes/page.html' }))
        await page.locator('[data-panel=main] .empty-state, [data-panel=main] .pane-nothing').first().waitFor()
        await capture('empty')
        if (!before) {
          // The former main item now lives in side. The main arrow skips it.
          await main.getByRole('button', { name: 'Back', exact: true }).click()
          await docReady()
          await page.locator('[data-panel=side] .fs-htmlframe').waitFor()
          await page.evaluate(() => testPage.panels.close())
        }
      }
      if (!before) {
        await page.goto('about:blank')
        await open('/files/vault/Notes/plan.md')
        await docReady()
        assert.equal(await docBack.count(), 0)
        if (!wide) {
          await main.getByRole('button', { name: 'Back to Notes', exact: true }).click()
          await list.filter({ hasText: 'plan.md' }).waitFor()
        }
        await open('/threads/general')
        await chatReady()
        assert.equal(await docBack.count(), 0)
        await main.getByRole('button', { name: 'Board', exact: true }).click()
        await page.waitForURL('**/threads/general?view=board')
        await main.locator('.tt-board-head').getByRole('button', { name: 'Back', exact: true }).click()
        await main.locator('.tt-board').waitFor({ state: 'detached' })
        await page.goForward()
        await main.locator('.tt-board').waitFor()
        await main.getByRole('button', { name: 'Close board', exact: true }).click()
        await page.waitForURL('**/threads/general')
        if (wide) await page.getByRole('button', { name: 'Settings', exact: true }).click()
        else await page.evaluate(() => testPage.openSheet({ kind: 'settings', page: 'root' }))
        await page.waitForURL('**/*modal=settings*')
        await backStops()
        await page.keyboard.press('Escape')
        await page.locator('.sheet.settings').waitFor({ state: 'detached' })
        assert.match(page.url(), /threads\/general$/)

        // Browser gestures cannot ask for a save until after traversal.
        // Keep editing restores the source entry and leaves Forward intact.
        await page.evaluate(() => testPage.goFiles('vault', 'Notes/page.html', { isFile: true, mode: 'edit' }))
        await main.locator('.fs-field').waitFor()
        app.routes['PUT /v1/files/text'] = (_, res) => { res.writeHead(503); res.end('{"error":"Save unavailable"}') }
        await main.locator('.fs-field').fill('Keep these edits')
        await page.goBack()
        await page.getByRole('button', { name: 'Keep editing', exact: true }).click()
        await page.waitForURL('**/files/vault/Notes/page.html')
        assert.equal(await main.locator('.fs-field').inputValue(), 'Keep these edits')
        await docBack.click()
        await page.getByRole('button', { name: 'Leave without saving', exact: true }).click()
        await chatReady()
        await page.goForward()
        await main.locator('.fs-field').waitFor()
        assert.notEqual(await main.locator('.fs-field').inputValue(), 'Keep these edits')
      }
      await open('/threads/general')
      await chatReady()
      await main.getByRole('button', { name: 'Board', exact: true }).click()
      await main.locator('.tt-board').waitFor()
      await capture('board')
      await main.getByRole('button', { name: 'Close board', exact: true }).click()
      await main.locator('.tt-board').waitFor({ state: 'detached' })
      await main.getByTitle('Thread details', { exact: true }).click()
      await main.locator('.tt-details-head').waitFor()
      await capture('thread-details')
      await page.evaluate(() => testPage.commands.run('section.open', { id: 'apps' }))
      await page.locator('.apps-row').first().waitFor()
      if (wide) {
        await page.locator('.apps-row').first().click()
        await main.frameLocator('iframe').getByText('Friday review').waitFor()
      }
      await capture('apps')
      if (wide) {
        await page.evaluate(() => testPage.panels.openSide({ kind: 'file', root: 'vault', path: 'Notes/plan.md' }))
        await page.evaluate(() => testPage.panels.openSide({ kind: 'thread', id: 'small-details' }))
        const side = page.locator('[data-panel=side]')
        await side.locator('.tt-head').waitFor()
        await side.getByText('The small details that make a difference', { exact: true }).first().waitFor()
        await capture('side-chat')
        if (!before) {
          await side.getByRole('button', { name: 'Back', exact: true }).focus()
          await page.keyboard.press('Control+[')
          await side.locator('.cm-content').waitFor()
          // A fresh destination abandons the old Forward branch.
          await page.evaluate(() => testPage.panels.openSide({ kind: 'file', root: 'vault', path: 'Notes/page.html' }))
          await page.keyboard.press('Control+]')
          await side.locator('.fs-htmlframe').waitFor()
          await docBack.click()
          await main.getByText('Pick an app', { exact: true }).waitFor()
          await backStops()
        }
      }
      await open('/threads/general')
      await chatReady()
      await page.evaluate(() => testPage.goProfile(testPage.state.agents.list[0].id))
      await main.locator('.profile-body').waitFor()
      await capture('profile')
      await page.evaluate(() => testPage.openSheet({ kind: 'settings', page: 'root' }))
      await page.locator('.sheet.settings').waitFor()
      await capture('settings')
      if (!wide) {
        await page.evaluate(() => testPage.openPhoneActivity())
        await page.locator('.activity-return').waitFor()
        await capture('activity')
        await page.evaluate(() => testPage.goFiles('vault', 'Notes', { isFile: false }))
        await list.filter({ hasText: 'plan.md' }).waitFor()
        await capture('folder')
        await page.evaluate(() => testPage.goChats())
        await page.locator('.tt-row').first().waitFor()
        await capture('chats-list')
      }

      // Use the visible navigation controls so the browser receives the same
      // activation it gets from a tab tap or a rail click.
      await open('/')
      const tabs = page.locator(wide ? '.rail-tabs' : '.navpill')
      const selectTab = name => tabs.getByRole('button', { name, exact: true }).click()
      const noBack = async () => assert.equal(await main.locator('.panel-history-back').count(), 0)
      const vault = page.getByRole('button', { name: /Vault Your notes/ })
      if (wide) {
        await page.locator('.tt-row[title="A little room for the week"]').click()
        await chatReady()
        await main.getByRole('button', { name: 'Board', exact: true }).click()
        await main.locator('.tt-board').waitFor()
      }
      await selectTab('Files')
      await vault.waitFor()
      await capture('tab-files')
      if (!before) { await noBack(); await backStops() }
      await vault.click()
      await list.filter({ hasText: /^Notes/ }).first().click()
      await list.filter({ hasText: 'plan.md' }).waitFor()
      if (!wide && !before) {
        await nativeBack(); await list.filter({ hasText: /^Notes/ }).first().waitFor()
        await page.goForward(); await list.filter({ hasText: 'plan.md' }).waitFor()
        await list.filter({ hasText: 'plan.md' }).click(); await docReady()
        await docBack.click(); await list.filter({ hasText: 'plan.md' }).waitFor()
        await selectTab('Files'); await vault.waitFor()
        await noBack(); await backStops()
        await vault.click()
        await list.filter({ hasText: /^Notes/ }).first().click()
        await list.filter({ hasText: 'plan.md' }).waitFor()
      }
      if (wide) {
        await list.filter({ hasText: 'plan.md' }).click(); await docReady()
        await list.filter({ hasText: 'page.html' }).click()
        await main.locator('.fs-livehead').waitFor()
        if (!before) {
          await docBack.click(); await docReady()
          await docBack.click(); await main.getByText('Pick a file', { exact: true }).waitFor()
          await list.filter({ hasText: 'plan.md' }).waitFor()
          await backStops()
          await page.goForward(); await docReady()
          await page.goForward(); await main.locator('.fs-livehead').waitFor()
        }
      }
      await selectTab('Apps')
      await page.locator('.apps-row').first().waitFor()
      await capture('tab-apps')
      if (!before) { await noBack(); await backStops() }
      if (wide) {
        await page.getByRole('button', { name: 'Open Notebook', exact: true }).click()
        await main.locator('iframe[title=Notebook]').waitFor()
        if (!before) {
          await docBack.click(); await main.getByText('Pick an app', { exact: true }).waitFor()
          await noBack(); await backStops()
          await page.goForward(); await main.locator('iframe[title=Notebook]').waitFor()
          await page.getByRole('button', { name: 'Open Calendar', exact: true }).click()
          await main.locator('iframe[title=Calendar]').waitFor()
          await docBack.click(); await main.locator('iframe[title=Notebook]').waitFor()
        }
      }
      await selectTab('Chats')
      await page.locator('.tt-row[title="A little room for the week"]').waitFor()
      await capture('tab-chats')
      if (!before) {
        await noBack(); await backStops()
        await page.locator('.tt-row[title="A little room for the week"]').click()
        await chatReady()
        if (!wide) {
          await nativeBack()
          await page.locator('.tt-row[title="A little room for the week"]').waitFor()
          await noBack(); await backStops()
        }
      }
      await page.getByRole('button', { name: 'Settings', exact: true }).click()
      const settings = page.locator('.sheet.settings')
      await settings.waitFor()
      await capture('tab-settings')
      if (!before) {
        assert.equal(await settings.locator('.backbtn').count(), 0)
        await settings.getByRole('button', { name: /^Connections/ }).click()
        await page.waitForURL('**/*section=connections')
        await settings.locator('#connections-providers .setrow').filter({ hasText: 'Claude Code' }).click()
        await page.waitForURL('**/*section=provider*')
        await nativeBack(); await page.waitForURL('**/*section=connections')
        if (wide) { await nativeBack(); await page.waitForURL('**/*modal=settings') }
        else { await settings.getByRole('button', { name: 'Settings', exact: true }).click(); await page.waitForURL('**/*modal=settings') }
        await backStops()
        await page.goForward(); await page.waitForURL('**/*section=connections')
        await page.keyboard.press('Escape')
        await settings.waitFor({ state: 'detached' })
        assert.equal(new URL(page.url()).searchParams.has('modal'), false)
        if (!wide) {
          await selectTab('Apps')
          await page.reload()
          await page.locator('.apps-row').first().waitFor()
          await noBack()
          await backStops()
        }
      }
      assert.deepEqual(errors, [])
      console.log(width, mode, 'passed')
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
