// Install from a link in a real browser (2026-11-18), against the fixture
// broker (test/link-install-fixture.mjs), at 390 and 1440, light and dark:
//
//   - one way in from Settings › Plugins (a row beside "Find more") and one
//     from the Market's foot ("Have a link? Install from a link"); both open
//     the same sheet, which is the Market's install sheet;
//   - one field: a paste reads by itself (no button), with a calm "Looking
//     at github.com/…"; a repository of several plugins lists them to pick;
//     a pick is what it is (name, maker, version, cover or icon) and what it
//     touches, then one Install, whose call names exactly what was shown;
//   - a branch link says in a line that it installs the commit it is on;
//   - each refusal is the host's own sentence, in place, with no id: not a
//     GitHub link (said without asking the host), nothing there or private,
//     no plugin in it, a broken manifest;
//   - once installed the plugin is marked "From a link" in Settings › Plugins
//     and its page says where it came from;
//   - no page error, no sideways scroll.
//
//   LOAM_PLAYWRIGHT=…/playwright/index.mjs LOAM_CHROME=/usr/bin/chromium-headless-shell node test/link-install-browser.mjs
//   LINK_EVIDENCE=<folder> also saves a capture of every state.
import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { linkFixture } from './link-install-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const source = await readFile('public/app.js', 'utf8')
const evidence = process.env.LINK_EVIDENCE
if (evidence) await mkdir(evidence, { recursive: true })

try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const label = `${width} ${mode}`
    const app = await linkFixture({ mode }), errors = []
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={page,state,schedule};' }))
    const page = await context.newPage(); page.setDefaultTimeout(12000)
    page.on('pageerror', e => errors.push(e.message))
    const shot = async name => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      await page.waitForTimeout(250)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${label} ${name}: sideways overflow`)
      if (evidence) await page.screenshot({ path: path.join(evidence, `${name}-${width}-${mode}.png`) })
    }
    const sheet = page.locator('.market-install')
    const field = page.locator('#market-link-field')
    const posts = () => app.writes.filter(w => w.link)
    const paste = async text => {
      await field.focus()
      await page.evaluate(text => {
        const input = document.querySelector('#market-link-field'), data = new DataTransfer()
        data.setData('text/plain', text)
        input.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true }))
        input.value = text
        input.dispatchEvent(new Event('input', { bubbles: true }))
      }, text)
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()

      // Settings › Plugins: the row, beside Find more.
      await page.evaluate(() => __cap.page.goSettings('plugins'))
      const door = page.locator('.market-link-door .setrow')
      await door.waitFor()
      assert.equal(await page.locator('#plugin-url, .plugin-install-options').count(), 0, `${label}: no old form`)
      await door.scrollIntoViewIfNeeded()
      await shot('1-settings-plugins')
      await door.click()
      await field.waitFor()
      assert.equal(await sheet.locator('.sheet-title').textContent(), 'Install from a link')
      assert.equal(await page.evaluate(() => document.activeElement?.id), 'market-link-field', `${label}: the field has focus`)
      assert.equal(await sheet.locator('input').count(), 1, `${label}: one field`)
      await shot('2-sheet-empty')

      // Not a GitHub link: said at once, without asking the host.
      await paste('https://gitlab.com/someone/theme')
      await sheet.locator('.market-link-error', { hasText: 'That isn’t a GitHub link' }).waitFor()
      assert.equal(posts().length, 0, `${label}: nothing sent for a link that is not GitHub's`)
      await shot('3-error-not-github')

      // A bare repository: reads by itself, and lists the plugins in it.
      app.delay.ms = 900
      await paste('https://github.com/loamhq/plugins')
      await sheet.locator('.market-reading', { hasText: 'Looking at github.com/loamhq/plugins' }).waitFor()
      await shot('4-reading')
      await sheet.locator('.market-link-pick').first().waitFor()
      assert.deepEqual(posts().at(-1).body, { url: 'https://github.com/loamhq/plugins' }, `${label}: the link as pasted`)
      assert.equal(await sheet.locator('.market-link-pick').count(), 4)
      await shot('5-choose')

      // Pick a theme with a cover.
      await sheet.locator('.market-link-pick', { hasText: 'Autumn' }).click()
      await sheet.locator('.sheet-title', { hasText: 'Get Autumn?' }).waitFor()
      assert.deepEqual(posts().at(-1).body, { url: 'loamhq/plugins', path: 'autumn' }, `${label}: a pick asks for that folder`)
      await sheet.locator('.market-install-cover img').waitFor()
      assert.match(await sheet.locator('.market-install-src').textContent(), /version 1\.0\.1 · from github\.com\/loamhq\/plugins/)
      await sheet.locator('.market-touch').first().waitFor()
      await shot('6-found-theme')
      await sheet.locator('.market-link-back', { hasText: 'Pick another' }).click()
      await sheet.locator('.market-link-pick', { hasText: 'Syncthing' }).click()
      await sheet.locator('.sheet-title', { hasText: 'Install Syncthing?' }).waitFor()
      await sheet.locator('.market-install-head img.market-tile-img').waitFor()
      await shot('7-found-extension')
      await sheet.locator('.pillbtn.primary', { hasText: 'Install' }).click()
      await sheet.locator('.market-install-done').waitFor()
      assert.deepEqual(posts().at(-1).body, { url: 'loamhq/plugins', path: 'syncthing', ref: 'syncthing-v1.0.0', via: 'link', confirm: 'ok-syncthing-syncthing-v1.0.0' }, `${label}: the yes names exactly what was shown`)
      await shot('8-installed')
      await sheet.locator('.pillbtn', { hasText: 'See it in Plugins' }).click()

      // Its page says where it came from; the list marks it as from a link.
      await page.locator('.plugin-from-link', { hasText: 'Installed from a link: github.com/loamhq/plugins/syncthing, at syncthing-v1.0.0' }).waitFor()
      await shot('9-detail')
      await page.getByRole('button', { name: 'Back to Plugins', exact: true }).click()
      await page.locator('.plugin-mark-link').first().waitFor()
      const marked = await page.locator('.plugin-list-item', { has: page.locator('.plugin-mark-link') }).locator('.setrow-title').allTextContents()
      assert.deepEqual(marked, ['Syncthing'], `${label}: only the plugin from a link is marked`)
      await page.locator('.plugin-list-item', { hasText: 'Syncthing' }).scrollIntoViewIfNeeded()
      await shot('10-marked')

      // A branch: installed at its commit, said in a line.
      await page.evaluate(() => __cap.page.openLinkInstall({ link: 'https://github.com/loamhq/plugins/tree/main/dictation' }))
      await sheet.locator('.market-link-note', { hasText: 'main is a branch' }).waitFor()
      await shot('11-branch')
      await sheet.locator('.market-link-back').click()
      assert.equal(await field.inputValue(), 'https://github.com/loamhq/plugins/tree/main/dictation', `${label}: Change link keeps the link`)

      // Each refusal, in plain words, in place.
      for (const [link, says, name] of [
        ['github.com/friend/secret-plugin', 'Nothing could be read at github.com/friend/secret-plugin', '12-error-unreachable'],
        ['friend/notes', 'There’s no Loam plugin in github.com/friend/notes', '13-error-no-plugin'],
        ['https://github.com/friend/half-done/', 'has a PLUGIN.md Loam can’t read', '14-error-manifest']]) {
        await field.fill('')
        await paste(link)
        await sheet.locator('.market-link-error', { hasText: says }).waitFor()
        await shot(name)
      }
      await sheet.locator('.pillbtn', { hasText: 'Cancel' }).click()
      await sheet.waitFor({ state: 'detached' })

      // The Market's foot: the same sheet.
      await page.evaluate(() => __cap.page.openMarket({}))
      const line = page.locator('.market-link-line')
      await line.waitFor()
      await line.scrollIntoViewIfNeeded()
      await shot('15-market-foot')
      await line.locator('button').click()
      await field.waitFor()
      assert.equal(await field.inputValue(), '', `${label}: a fresh sheet`)
      await paste('friend/lantern')
      await sheet.locator('.market-link-note', { hasText: 'no release yet' }).waitFor()
      await shot('16-no-release')
      assert.deepEqual(errors, [], `${label}: page errors`)
      console.log(`ok ${label}`)
    } finally {
      await context.close(); await app.close()
    }
  }
} finally {
  await browser.close()
}
process.exit(0)
