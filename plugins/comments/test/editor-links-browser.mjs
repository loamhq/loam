// Real Chromium verification of plain-click link following, cursor parity,
// hover/caret link chip editing, and Mod-K editing in the calm Markdown editor
// (plugins/comments) across Editing, Suggesting, and Viewing modes, in Files
// main and beside a chat, at 390 and 1440, light and dark.
//
// Run:
//   LOAM_PLAYWRIGHT=<playwright index.mjs> LOAM_CHROME=<chromium> \
//     node plugins/comments/test/editor-links-browser.mjs
// Set EDITOR_LINKS_EVIDENCE=<dir> to save screenshots for each state.
import assert from 'node:assert/strict'
import { mkdir, readFile, readdir } from 'node:fs/promises'
import { join } from 'node:path'
import { filesHost } from '../../../app/test/files-host.js'
import { row, readAnswer } from '../../../app/test/fixtures/files-contract.js'
import { oneProductFixture } from '../../../app/test/one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const output = process.env.EDITOR_LINKS_EVIDENCE
const previewOrigin = 'https://preview.example.com'
const vaultOrigin = 'https://vault.example.test'
const external = 'https://example.org/reading-list'
const externalUpdated = 'https://example.org/reading-list-v2'
const threadPath = '/threads/a-quieter-week'
const htmlPath = '/files/vault/Notes/page.html'
const planPath = '/files/vault/Notes/plan.md'
const otherPath = '/files/vault/Notes/other.md'

const plan = `# The weekly plan

Review the [[other#Focus|companion note]] before Friday, or open the [Friday review mockup](${htmlPath}).

Save the [reading list](${external}) for later, or check the [published companion](${vaultOrigin}/Notes/other.md).

## Next steps

- Give the first draft another read.
- Confirm the review checklist.`

const other = `# Companion note

Keep the open questions short so the review has room to breathe.

## Focus

- Pick one change to try first.
- Write down what surprised us.`

const mockup = `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: light dark; background: light-dark(#f3f4eb,#252b24); color: light-dark(#2d382d,#e6eadc); }
  body { margin: clamp(24px,6vw,68px); font: 16px/1.6 system-ui; }
  p { max-width: 36ch; } h1 { font: 400 clamp(36px,6vw,60px)/1.02 Georgia; margin: 18px 0; }
  small { letter-spacing: .12em; text-transform: uppercase; } li { margin-block: 8px; }
</style>
<small>Studio planning</small><h1>Friday review</h1><p>Set aside an hour to look back on the week and choose where to spend our attention next.</p>
<ul><li>Read the short first draft.</li><li>Bring one open question.</li></ul>
<script>document.body.dataset.ready = 'true'</script>`

const commentsPluginDir = new URL('../', import.meta.url)

async function mountFixture({ mode }) {
  const app = await oneProductFixture({ FILES_CONTENT_ORIGIN: previewOrigin, FILES_VAULT_URLS: vaultOrigin })
  const texts = {
    'vault:Notes/plan.md': plan,
    'vault:Notes/other.md': other,
  }
  const host = filesHost({ texts })
  Object.assign(app.routes, Object.fromEntries(Object.entries(host.routes).filter(([path]) => path.includes('/files/'))))
  const byRef = {
    '[[plan]]': { resource: row('vault', 'Notes/plan.md'), fragment: null },
    '[[plan#Next steps]]': { resource: row('vault', 'Notes/plan.md'), fragment: 'Next steps' },
    '[[other#Focus]]': { resource: row('vault', 'Notes/other.md'), fragment: 'Focus' },
    '[[other#Summary]]': { resource: row('vault', 'Notes/other.md'), fragment: 'Summary' },
    'Notes/other.md': { resource: row('vault', 'Notes/other.md'), fragment: null },
    [planPath]: { resource: row('vault', 'Notes/plan.md'), fragment: null },
    [otherPath]: { resource: row('vault', 'Notes/other.md'), fragment: null },
    [htmlPath]: { resource: row('vault', 'Notes/page.html'), fragment: null },
  }
  app.routes['GET /v1/files/config'] = (_, res) => res.end(JSON.stringify({ vault_urls: [vaultOrigin] }))
  app.routes['GET /v1/files/stat'] = (req, res, url) => {
    const root = url.searchParams.get('root'), p = url.searchParams.get('path')
    res.end(JSON.stringify(row(root, p)))
  }
  app.routes['GET /v1/files/resolve'] = (req, res, url) => {
    const ref = url.searchParams.get('ref')
    if (ref in byRef) return res.end(JSON.stringify({ status: 'found', ...byRef[ref] }))
    return host.routes['GET /v1/files/resolve'](req, res, url)
  }
  app.routes['GET /v1/files/read'] = (req, res, url) => {
    const root = url.searchParams.get('root'), p = url.searchParams.get('path')
    const key = `${root}:${p}`
    if (key in texts) return res.end(JSON.stringify(readAnswer(row(root, p), texts[key], 'sha256:r1')))
    return host.routes['GET /v1/files/read'](req, res, url)
  }
  const record = await app.f.api('GET', threadPath)
  record.messages.find(message => message.id === 'm4').text =
    `The [[plan#Next steps|weekly plan]] is ready. Open the [Friday review mockup](${htmlPath}) to see how it looks, or save the [reading list](${external}) for later.`
  app.routes[`GET /v1${threadPath}`] = (_, res) => res.end(JSON.stringify(record))
  const appearance = { theme: 'latte', mode, size: 100 }
  app.routes['GET /v1/settings/appearance'] = (_, res) => res.end(JSON.stringify({ phone: appearance, desktop: appearance }))

  const plugins = [
    { id: 'comments', version: '1.0.0', api: 0, layer: 'default', switch: 'plugin', effective: true, on: true, has: { page_main: true } },
  ]
  for (const rel of await readdir(commentsPluginDir, { recursive: true })) {
    if (!/^(page|assets)\/.*\.(js|css)$/.test(rel)) continue
    app.routes['GET /v1/plugins/comments/files/' + rel] = async (_, res) => {
      res.setHeader('content-type', rel.endsWith('.css') ? 'text/css' : 'text/javascript')
      res.end(await readFile(new URL(rel, commentsPluginDir)))
    }
  }
  app.routes['GET /v1/plugins'] = (_, res) => res.end(JSON.stringify({ plugins }))
  return { app, texts }
}

async function switchMode(container, label) {
  await container.locator('.dc-mpill').click()
  await container.getByRole('menuitemradio', { name: new RegExp(label) }).click()
  const cls = label === 'Editing' ? 'mode-edit' : label === 'Suggesting' ? 'mode-suggest' : 'mode-view'
  await container.locator(`.dc.${cls}`).waitFor()
}

if (output) await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const wide = width === 1440
    const { app, texts } = await mountFixture({ mode })
    const docStore = new Map(Object.entries(texts))
    const context = await browser.newContext({
      viewport: { width, height: wide ? 900 : 844 },
      colorScheme: mode,
      hasTouch: !wide,
      isMobile: !wide,
      reducedMotion: 'reduce',
    })
    try {
      await context.route(`${previewOrigin}/**`, route => route.fulfill({ contentType: 'text/html', body: mockup }))
      await context.route('https://example.org/**', route => route.fulfill({ body: 'Reading list' }))
      await context.route(`${app.url}/sw.js`, route => route.abort())
      await context.route('**/x/comments/**', async route => {
        const req = route.request()
        const url = new URL(req.url())
        const p = url.searchParams.get('path') || 'Notes/plan.md'
        const key = `vault:${p}`
        if (url.pathname.endsWith('/config')) {
          return route.fulfill({ json: { reach: 'everywhere' } })
        }
        if (url.pathname.endsWith('/revision')) {
          return route.fulfill({ json: { revision: 'r1', notes: 'n0' } })
        }
        if (req.method() === 'POST' || req.method() === 'PUT') {
          const body = req.postDataJSON?.() || {}
          if (typeof body.text === 'string' && url.pathname.endsWith('/save')) {
            docStore.set(key, body.text)
          }
          return route.fulfill({
            json: {
              text: docStore.get(key) || plan,
              revision: 'r1',
              notes: [],
              can: { edit: true, suggest: true, comment: true, accept: true, resolve_any: true },
              me: { id: 'owner', name: 'Owner', kind: 'owner' },
            },
          })
        }
        return route.fulfill({
          json: {
            text: docStore.get(key) || plan,
            revision: 'r1',
            notes: [],
            can: { edit: true, suggest: true, comment: true, accept: true, resolve_any: true },
            me: { id: 'owner', name: 'Owner', kind: 'owner' },
          },
        })
      })

      const page = await context.newPage()
      await page.addInitScript(() => localStorage.setItem('loam-ui.file-panel', 'closed'))
      page.setDefaultTimeout(10000)
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      const capture = async (name) => {
        if (!output) return
        await page.evaluate(async () => {
          await document.fonts.ready
          await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))
        })
        await page.screenshot({ path: join(output, `${name}-${width}-${mode}.png`), animations: 'disabled' })
      }

      const main = wide ? page.locator('[data-panel="main"]') : page.locator('.screen.files')
      const side = page.getByRole('region', { name: 'Side panel' })

      // 1. Open plan.md in Files main (starts in Editing mode).
      await page.goto(app.url + planPath)
      await main.locator('.dc.mode-edit .cm-content').waitFor()
      const wiki = main.locator('.dc-wiki[data-wiki="other#Focus"]').first()
      const mdVault = main.locator('.dc-link[data-url="/files/vault/Notes/page.html"]').first()
      const mdWeb = main.locator(`.dc-link[data-url="${external}"]`).first()
      const mdPublished = main.locator(`.dc-link[data-url="${vaultOrigin}/Notes/other.md"]`).first()
      await wiki.waitFor()
      await mdVault.waitFor()
      await mdWeb.waitFor()
      await mdPublished.waitFor()

      // Check pointer cursor & attributes across Editing, Suggesting, Viewing.
      for (const mLabel of ['Editing', 'Suggesting', 'Viewing']) {
        if (mLabel !== 'Editing') await switchMode(main, mLabel)
        const cursors = await main.evaluate(() => {
          const w = document.querySelector('[data-panel="main"] .dc-wiki, .screen.files .dc-wiki')
          const l = document.querySelector('[data-panel="main"] .dc-link, .screen.files .dc-link')
          return {
            wiki: w ? getComputedStyle(w).cursor : null,
            link: l ? getComputedStyle(l).cursor : null,
            hasDataUrl: Boolean(l?.getAttribute('data-url')),
            hasDataWiki: Boolean(w?.getAttribute('data-wiki')),
          }
        })
        assert.deepEqual(cursors, { wiki: 'pointer', link: 'pointer', hasDataUrl: true, hasDataWiki: true }, `cursor/attrs in ${mLabel}`)
      }

      // 2. Test plain-click link following in Editing, Suggesting, and Viewing from Files main.
      for (const mLabel of ['Editing', 'Suggesting', 'Viewing']) {
        await switchMode(main, mLabel)
        // 2a. Wiki-link plain click
        await wiki.click()
        if (wide) {
          await side.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
          assert.equal(new URL(page.url()).pathname, planPath, `${mLabel}: main URL stays on plan.md`)
          if (mLabel === 'Editing') await capture('main-editing-wiki-side')
          if (mLabel === 'Suggesting') await capture('main-suggesting-wiki-side')
          if (mLabel === 'Viewing') await capture('main-viewing-wiki-side')
        } else {
          await page.waitForURL(`**${otherPath}**`)
          await main.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
          if (mLabel === 'Editing') await capture('phone-editing-followed')
          await main.getByRole('button', { name: 'Back', exact: true }).click()
          await page.waitForURL(`**${planPath}**`)
          await main.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
          if (mLabel !== 'Editing') await switchMode(main, mLabel)
        }

        // 2b. Vault Markdown link plain click -> HTML mockup
        await mdVault.click()
        if (wide) {
          await side.frameLocator('iframe').getByRole('heading', { name: 'Friday review' }).waitFor()
          assert.equal(new URL(page.url()).pathname, planPath)
        } else {
          await page.waitForURL(`**${htmlPath}**`)
          await main.frameLocator('iframe').getByRole('heading', { name: 'Friday review' }).waitFor()
          await main.getByRole('button', { name: 'Back', exact: true }).click()
          await page.waitForURL(`**${planPath}**`)
          await main.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
          if (mLabel !== 'Editing') await switchMode(main, mLabel)
        }

        // 2c. Web link plain click -> opens new tab (through host.openFileRef)
        const [extTab] = await Promise.all([context.waitForEvent('page'), mdWeb.click()])
        await extTab.waitForURL(external)
        await extTab.close()

        // 2d. Published vault URL plain click -> routed through host.openFileRef (opens in Loam, NOT new tab!)
        let unexpectedTab = false
        const onPage = () => { unexpectedTab = true }
        context.on('page', onPage)
        await mdPublished.click()
        if (wide) {
          await side.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
          assert.equal(unexpectedTab, false, `${mLabel}: published vault URL opens in Loam, not a new tab`)
        } else {
          await page.waitForURL(`**${otherPath}**`)
          assert.equal(unexpectedTab, false, `${mLabel}: published vault URL opens in Loam on phone`)
          await main.getByRole('button', { name: 'Back', exact: true }).click()
          await page.waitForURL(`**${planPath}**`)
          await main.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
          if (mLabel !== 'Editing') await switchMode(main, mLabel)
        }
        context.off('page', onPage)
      }

      // 3. Desktop Cmd/Ctrl-click in Editing and Suggesting opens in main; hover chip + Edit + Mod-K.
      if (wide) {
        // Close side panel first for clean main-view shots
        await side.getByRole('button', { name: 'Close side panel' }).click()
        await switchMode(main, 'Editing')

        // Cmd/Ctrl-click wiki-link in Editing opens in main
        await wiki.click({ modifiers: ['Control'] })
        await page.waitForURL(`**${otherPath}**`)
        await main.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
        await page.goBack()
        await page.waitForURL(`**${planPath}**`)
        await main.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()

        // Cmd/Ctrl-click vault Markdown link in Suggesting opens in main
        await switchMode(main, 'Suggesting')
        await mdVault.click({ modifiers: ['Control'] })
        await page.waitForURL(`**${htmlPath}**`)
        await main.frameLocator('iframe').getByRole('heading', { name: 'Friday review' }).waitFor()
        await page.goBack()
        await page.waitForURL(`**${planPath}**`)
        await main.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()

        // Hover chip does NOT show in Viewing mode
        await switchMode(main, 'Viewing')
        await mdWeb.hover()
        await page.waitForTimeout(160)
        assert.equal(await main.locator('.dc-lchip.on').count(), 0, 'no link chip in Viewing mode')

        // Hover chip in Editing mode + edit by chip
        await switchMode(main, 'Editing')
        await mdWeb.hover()
        const chip = main.locator('.dc-lchip.on')
        await chip.waitFor()
        assert.match(await chip.locator('.dc-lchip-txt').textContent(), /example\.org\/reading-list/)
        await capture('main-hover-chip')

        await chip.getByRole('button', { name: 'Edit' }).click()
        const linkInput = main.locator('.dc-selbar.lnk.on input')
        await linkInput.waitFor()
        assert.equal(await linkInput.inputValue(), external)
        await capture('main-edit-linkbar')
        await linkInput.fill(externalUpdated)
        await linkInput.press('Enter')
        await main.locator(`.dc-link[data-url="${externalUpdated}"]`).waitFor()

        // Edit wiki-link by chip + verify round-trip back
        await wiki.hover()
        await chip.waitFor()
        assert.equal((await chip.locator('.dc-lchip-txt').textContent()).trim(), 'other#Focus')
        await chip.getByRole('button', { name: 'Edit' }).click()
        await linkInput.waitFor()
        assert.equal(await linkInput.inputValue(), 'other#Focus')
        await linkInput.fill('other#Summary')
        await linkInput.press('Enter')
        await main.locator('.dc-wiki[data-wiki="other#Summary"]').waitFor()
        await main.locator('.dc-wiki[data-wiki="other#Summary"]').hover()
        await chip.waitFor()
        await chip.getByRole('button', { name: 'Edit' }).click()
        await linkInput.waitFor()
        await linkInput.fill('other#Focus')
        await linkInput.press('Enter')
        await main.locator('.dc-wiki[data-wiki="other#Focus"]').waitFor()

        // Move caret into a link by keyboard -> chip shows -> Mod-K opens link bar and edits back
        await main.locator('.cm-line').filter({ hasText: 'reading list' }).click({ position: { x: 4, y: 10 } })
        let chipShownByCaret = false
        for (let i = 0; i < 30; i++) {
          await page.keyboard.press('ArrowRight')
          if (await main.locator('.dc-lchip.on').count() > 0) {
            chipShownByCaret = true
            break
          }
        }
        assert.equal(chipShownByCaret, true, 'moving caret into link by keyboard shows link chip')
        await page.keyboard.press('Control+k')
        await linkInput.waitFor()
        assert.equal(await linkInput.inputValue(), externalUpdated)
        await capture('main-modk-linkbar')
        await linkInput.fill(external)
        await linkInput.press('Enter')
        await main.locator(`.dc-link[data-url="${external}"]`).waitFor()

        // Reset docStore to original plan for the chat-side walk
        docStore.set('vault:Notes/plan.md', plan)
      } else {
        // Phone: verify selection/keyboard bar Link edits a link cleanly and no hover chip appears
        await switchMode(main, 'Editing')
        await capture('phone-editing-main')
        await main.locator('.cm-line').filter({ hasText: 'reading list' }).click({ position: { x: 4, y: 10 } })
        assert.equal(await main.locator('.dc-lchip.on').count(), 0, 'no hover chip on touch phone')
        for (let i = 0; i < 20; i++) {
          await page.keyboard.press('ArrowRight')
          if (await main.locator('.dc-kbar button[aria-label="Link"].on').count() > 0) break
        }
        await main.locator('.dc-kbar button[aria-label="Link"]').click()
        const kInput = main.locator('.dc-kbar.dc-klink input')
        await kInput.waitFor()
        assert.equal(await kInput.inputValue(), external)
        await capture('phone-kbar-link-edit')
        await kInput.fill(externalUpdated)
        await kInput.press('Enter')
        await main.locator(`.dc-link[data-url="${externalUpdated}"]`).waitFor()
        docStore.set('vault:Notes/plan.md', plan)
      }

      // 4. Chat links unchanged + note beside a chat in Editing, Suggesting, Viewing (desktop) & full-screen (phone).
      await page.goto(app.url + threadPath)
      docStore.set('vault:Notes/plan.md', plan)
      const stream = page.locator('.tt-stream')
      const chatNote = stream.getByRole('link', { name: 'weekly plan' })
      const chatWeb = stream.getByRole('link', { name: 'reading list' })
      await chatNote.scrollIntoViewIfNeeded()
      assert.equal(await stream.locator('.filelink-side').count(), 0, 'chat links have no .filelink-side')
      const [chatExtTab] = await Promise.all([context.waitForEvent('page'), chatWeb.click()])
      await chatExtTab.waitForURL(external)
      await chatExtTab.close()

      await chatNote.click()
      if (wide) {
        await side.locator('.dc.mode-edit .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
        await capture('side-chat-editing')

        // Side panel beside a chat: plain-click wiki-link & Markdown link in Editing, Suggesting, Viewing.
        for (const mLabel of ['Editing', 'Suggesting', 'Viewing']) {
          await switchMode(side, mLabel)
          // Plain-click wiki-link navigates side panel; chat stays untouched
          await side.locator('.dc-wiki[data-wiki="other#Focus"]').first().click()
          await side.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
          assert.equal(new URL(page.url()).pathname, threadPath, `side ${mLabel}: chat URL stays`)
          if (mLabel === 'Suggesting') await capture('side-chat-suggesting-followed')
          await side.getByRole('button', { name: 'Back', exact: true }).click()
          await side.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
          if (mLabel !== 'Editing') await switchMode(side, mLabel)

          // Plain-click vault Markdown link navigates side panel
          await side.locator(`.dc-link[data-url="${htmlPath}"]`).first().click()
          await side.frameLocator('iframe').getByRole('heading', { name: 'Friday review' }).waitFor()
          await side.getByRole('button', { name: 'Back', exact: true }).click()
          await side.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
          if (mLabel !== 'Editing') await switchMode(side, mLabel)

          // Plain-click web link opens new tab
          const [sideExt] = await Promise.all([
            context.waitForEvent('page'),
            side.locator(`.dc-link[data-url="${external}"]`).first().click(),
          ])
          await sideExt.waitForURL(external)
          await sideExt.close()
        }

        // Hover chip & Edit inside the narrow side panel on desktop!
        await switchMode(side, 'Editing')
        await side.locator(`.dc-link[data-url="${external}"]`).first().hover()
        const sideChip = side.locator('.dc-lchip.on')
        await sideChip.waitFor()
        await capture('side-chat-hover-chip')
        await sideChip.getByRole('button', { name: 'Edit' }).click()
        const sideInput = side.locator('.dc-selbar.lnk.on input')
        await sideInput.waitFor()
        assert.equal(await sideInput.inputValue(), external)
        await sideInput.press('Escape')

        // Cmd/Ctrl-click in side panel (Editing) opens in main
        await side.locator('.dc-wiki[data-wiki="other#Focus"]').first().click({ modifiers: ['Control'] })
        await page.waitForURL(`**${otherPath}**`)
        await page.goBack()
        await page.waitForURL(`**${threadPath}**`)
      } else {
        await page.waitForURL(`**${planPath}**`)
        await main.locator('.dc.mode-edit .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
        await capture('phone-chat-to-editor')
        await main.locator('.dc-wiki[data-wiki="other#Focus"]').first().click()
        await page.waitForURL(`**${otherPath}**`)
        await main.getByRole('button', { name: 'Back', exact: true }).click()
        await page.waitForURL(`**${planPath}**`)
        await main.getByRole('button', { name: 'Back', exact: true }).click()
        await page.waitForURL(`**${threadPath}**`)
      }

      assert.deepEqual(errors, [])
      console.log(`editor-links ${width} ${mode} passed`)
    } finally {
      await context.close()
      await app.close()
    }
  }
} finally {
  await browser.close()
}
