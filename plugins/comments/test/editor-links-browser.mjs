// Real Chromium verification of:
// - plain-click link following, cursor parity, hover/caret link chip & Mod-K
//   editing in plugins/comments across Editing, Suggesting, and Viewing;
// - link chip opaque surface, below-by-default placement (flipping above at the
//   bottom of the viewport), hover winning over caret, and prompt hide on leave;
// - single unified header row for Markdown notes in the side panel (title + path,
//   primary actions, one ⋯ holding Swap and Open as the main panel, Close), both
//   beside a note and beside a chat, with the HTML side panel unchanged.
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

const fillerParagraphs = Array.from({ length: 16 }, (_, i) =>
  `Paragraph ${i + 1}. Keep the layout calm and legible so each section breathes across both narrow and wide panes.`,
).join('\n\n')

const plan = `# The weekly plan

- Review the [[other#Focus|companion note]] before Friday.
- Open the [Friday review mockup](${htmlPath}) for layout notes.
- Save the [reading list](${external}) for later.
- Check the [published companion](${vaultOrigin}/Notes/other.md).

## Next steps

- Give the first draft another read.
- Confirm the review checklist.

${fillerParagraphs}

## Closing notes

Check the [[other#Focus|bottom companion link]] before wrapping up.`

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

const headerPluginSource = `export function activate(loam) {
  const H = loam.ui.h
  loam.ui.styles?.add?.('assets/sharing.css')
  const share = loam.places.files.action.add({
    id: 'fixture.share',
    slot: 'action',
    when: c => Boolean((c.resource || c.file)?.root === 'vault'),
    render: () => H('button', { type: 'button', class: 'sh-sharebtn secondary' }, H('span', { text: 'Share' })),
  })
  return () => { share.remove() }
}`

const commentsPluginDir = new URL('../', import.meta.url)
const sharingCssPath = new URL('../../sharing/assets/sharing.css', import.meta.url)

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
    { id: 'header-fixture', version: '1.0.0', api: 0, layer: 'default', switch: 'plugin', effective: true, on: true, has: { page_main: true } },
    { id: 'comments', version: '1.0.0', api: 0, layer: 'default', switch: 'plugin', effective: true, on: true, has: { page_main: true } },
  ]
  app.routes['GET /v1/plugins/header-fixture/files/page/main.js'] = (_, res) => {
    res.setHeader('content-type', 'text/javascript')
    res.end(headerPluginSource)
  }
  app.routes['GET /v1/plugins/header-fixture/files/assets/sharing.css'] = async (_, res) => {
    res.setHeader('content-type', 'text/css')
    res.end(await readFile(sharingCssPath))
  }
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
      assert.equal(await main.locator('.dc-head').count(), 1, 'Files main has exactly one header row')
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

      // 2. Plain-click link following in Editing, Suggesting, Viewing + single side header verification.
      for (const mLabel of ['Editing', 'Suggesting', 'Viewing']) {
        await switchMode(main, mLabel)
        // 2a. Wiki-link plain click -> note beside a note (desktop) or full-screen + Back (phone)
        await wiki.click()
        if (wide) {
          await side.locator('.dc-head.dc-sidehead').waitFor()
          await side.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
          assert.equal(new URL(page.url()).pathname, planPath, `${mLabel}: main URL stays on plan.md`)
          // Single header row in side panel (no duplicate .side-head row above .dc-head!)
          assert.equal(await side.locator('.side-head').count(), 0, 'Markdown side panel has no duplicate .side-head')
          assert.equal(await side.locator('.dc-head.dc-sidehead').count(), 1, 'Markdown side panel has one .dc-head.dc-sidehead')
          assert.equal(await side.locator('.dc-side-heading strong').textContent(), 'other.md')
          assert.equal(await side.locator('.dc-side-heading span').textContent(), 'Your files / Notes')
          assert.equal(await side.locator('.sh-sharebtn').count(), 1, 'single Share button in side header')
          assert.equal(await side.locator('.dc-head [aria-label="More"]').count(), 1, 'single More menu in side header')
          assert.equal(await side.locator('.dc-head [aria-label="Close side panel"]').count(), 1, 'Close button in side header')
          const sideHeadBox = await side.locator('.dc-head.dc-sidehead').boundingBox()
          assert.ok(sideHeadBox.height <= 84, `single side header row height (${sideHeadBox.height}px)`)
          if (mLabel === 'Editing') await capture('note-beside-note')
        } else {
          await page.waitForURL(`**${otherPath}**`)
          await main.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
          if (mLabel === 'Editing') await capture('phone-editing-followed')
          await main.getByRole('button', { name: 'Back', exact: true }).click()
          await page.waitForURL(`**${planPath}**`)
          await main.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
          if (mLabel !== 'Editing') await switchMode(main, mLabel)
        }

        // 2b. Vault Markdown link plain click -> HTML side panel unchanged!
        await mdVault.click()
        if (wide) {
          await side.frameLocator('iframe').getByRole('heading', { name: 'Friday review' }).waitFor()
          assert.equal(new URL(page.url()).pathname, planPath)
          assert.equal(await side.locator('.side-head').count(), 1, 'HTML side panel keeps .side-head')
          assert.equal(await side.locator('.side-head .sh-sharebtn').count(), 1)
          assert.equal(await side.locator('.side-head .fs-comments-btn').count(), 1)
          if (mLabel === 'Editing') await capture('html-beside-note')
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
          await side.locator('.dc-head.dc-sidehead').waitFor()
          await side.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
          assert.equal(unexpectedTab, false, `${mLabel}: published vault URL opens in Loam`)
          if (mLabel !== 'Viewing') {
            await side.locator('.dc-head [aria-label="Close side panel"]').click()
            await side.waitFor({ state: 'detached' })
          }
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

      // 3. Desktop: panel actions inside side ⋯ (Swap & Open as main panel), chip placement (top/below, bottom/flip-above, caret-vs-hover, opaque, prompt hide), Edit & Mod-K.
      if (wide) {
        // Test Swap and Open as the main panel inside the single side ⋯ menu!
        await side.locator('.dc-head [aria-label="More"]').click()
        await side.getByRole('menuitem', { name: 'Swap the two panels' }).waitFor()
        await side.getByRole('menuitem', { name: 'Open as the main panel' }).waitFor()
        await capture('side-more-menu')
        await side.getByRole('menuitem', { name: 'Swap the two panels' }).click()
        await main.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
        await side.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
        // Swap back via side ⋯ -> Open as the main panel (promotes plan.md back to main & closes side)
        await side.locator('.dc-head [aria-label="More"]').click()
        await side.getByRole('menuitem', { name: 'Open as the main panel' }).click()
        await side.waitFor({ state: 'detached' })
        await main.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()

        // Cmd/Ctrl-click in Editing & Suggesting opens in main
        await switchMode(main, 'Editing')
        await wiki.click({ modifiers: ['Control'] })
        await page.waitForURL(`**${otherPath}**`)
        await main.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
        await page.goBack()
        await page.waitForURL(`**${planPath}**`)
        await main.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()

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

        // Hover chip at top/middle of viewport: OPAQUE, placed BELOW the link line!
        await switchMode(main, 'Editing')
        await mdWeb.hover()
        const chip = main.locator('.dc-lchip.on')
        await chip.waitFor()
        const topGeometry = await main.evaluate((el, ext) => {
          const l = el.querySelector(`.dc-link[data-url="${ext}"]`)
          const c = el.querySelector('.dc-lchip.on')
          const cs = getComputedStyle(c)
          return {
            link: l.getBoundingClientRect().toJSON(),
            chip: c.getBoundingClientRect().toJSON(),
            opacity: cs.opacity,
            bg: cs.backgroundColor,
          }
        }, external)
        assert.equal(topGeometry.opacity, '1', 'link chip is immediately opaque (no translucent fade)')
        assert.ok(!topGeometry.bg.startsWith('rgba(') || topGeometry.bg.endsWith(', 1)'), `opaque background: ${topGeometry.bg}`)
        assert.ok(topGeometry.chip.top >= topGeometry.link.bottom - 1, `chip sits BELOW link at top of viewport (chip.top=${topGeometry.chip.top}, link.bottom=${topGeometry.link.bottom})`)
        await capture('chip-hover-below')

        // Caret in Link A (reading list) while hovering Link B (Friday review mockup): hovered link B wins,
        // moving onto chip keeps it open, leaving chip hides it promptly!
        await main.locator('.cm-line').filter({ hasText: 'reading list' }).click({ position: { x: 4, y: 10 } })
        for (let i = 0; i < 30; i++) {
          await page.keyboard.press('ArrowRight')
          if (await main.locator('.dc-lchip.on').count() > 0) break
        }
        assert.match(await chip.locator('.dc-lchip-txt').textContent(), /example\.org\/reading-list/)
        await capture('chip-caret-below')
        // Now hover Link B while caret stays in Link A -> Link B wins!
        await mdVault.hover()
        await page.waitForFunction(() => {
          const t = document.querySelector('[data-panel="main"] .dc-lchip.on .dc-lchip-txt')
          return t && t.textContent.includes('Notes/page.html')
        })
        // Moving onto chip keeps it visible
        await chip.hover()
        await page.waitForTimeout(180)
        assert.equal(await main.locator('.dc-lchip.on').count(), 1, 'chip stays open when pointer moves onto it')
        // Leaving chip onto plain heading hides it promptly
        await main.locator('.dc-h1').hover()
        await page.waitForTimeout(220)
        assert.equal(await main.locator('.dc-lchip.on').count(), 0, 'chip disappears promptly when pointer leaves')

        // Scroll to bottom of document -> link near bottom of viewport flips chip ABOVE!
        await main.locator('.dc-scroll').evaluate(el => { el.scrollTop = el.scrollHeight })
        const bottomWiki = main.locator('.dc-wiki[data-wiki="other#Focus"]').last()
        await bottomWiki.waitFor()
        await bottomWiki.hover()
        await chip.waitFor()
        const bottomGeom = await main.evaluate(() => {
          const all = document.querySelectorAll('[data-panel="main"] .dc-wiki[data-wiki="other#Focus"]')
          const l = all[all.length - 1]
          const c = document.querySelector('[data-panel="main"] .dc-lchip.on')
          const s = document.querySelector('[data-panel="main"] .dc-scroll')
          return {
            link: l.getBoundingClientRect().toJSON(),
            chip: c.getBoundingClientRect().toJSON(),
            scroll: s.getBoundingClientRect().toJSON(),
          }
        })
        assert.ok(bottomGeom.chip.top >= bottomGeom.scroll.top, 'chip stays inside viewport at bottom')
        assert.ok(bottomGeom.chip.bottom <= bottomGeom.scroll.bottom, 'chip never clips below viewport')
        // Scroll so link is within 24px of bottom edge to force flip-above check!
        await main.evaluate(() => {
          const s = document.querySelector('[data-panel="main"] .dc-scroll')
          const all = document.querySelectorAll('[data-panel="main"] .dc-wiki[data-wiki="other#Focus"]')
          const l = all[all.length - 1]
          const lr = l.getBoundingClientRect(), sr = s.getBoundingClientRect()
          s.scrollTop += (lr.bottom - (sr.bottom - 18))
        })
        await bottomWiki.hover()
        await chip.waitFor()
        const flipped = await main.evaluate(() => {
          const all = document.querySelectorAll('[data-panel="main"] .dc-wiki[data-wiki="other#Focus"]')
          const l = all[all.length - 1]
          const c = document.querySelector('[data-panel="main"] .dc-lchip.on')
          return { link: l.getBoundingClientRect().toJSON(), chip: c.getBoundingClientRect().toJSON() }
        })
        assert.ok(flipped.chip.bottom <= flipped.link.top + 1, `chip flips ABOVE near viewport bottom (chip.bottom=${flipped.chip.bottom}, link.top=${flipped.link.top})`)
        await capture('chip-bottom-flip-above')

        // Scroll back to top and test Edit via chip + Mod-K
        await main.locator('.dc-scroll').evaluate(el => { el.scrollTop = 0 })
        await mdWeb.hover()
        await chip.waitFor()
        await chip.getByRole('button', { name: 'Edit' }).click()
        const linkInput = main.locator('.dc-selbar.lnk.on input')
        await linkInput.waitFor()
        assert.equal(await linkInput.inputValue(), external)
        await linkInput.fill(externalUpdated)
        await linkInput.press('Enter')
        await main.locator(`.dc-link[data-url="${externalUpdated}"]`).waitFor()

        // Mod-K back to original URL
        await main.locator('.cm-line').filter({ hasText: 'reading list' }).click({ position: { x: 4, y: 10 } })
        for (let i = 0; i < 30; i++) {
          await page.keyboard.press('ArrowRight')
          if (await main.locator('.dc-lchip.on').count() > 0) break
        }
        await page.keyboard.press('Control+k')
        await linkInput.waitFor()
        assert.equal(await linkInput.inputValue(), externalUpdated)
        await linkInput.fill(external)
        await linkInput.press('Enter')
        await main.locator(`.dc-link[data-url="${external}"]`).waitFor()
      } else {
        // Phone: verify selection/keyboard bar Link edits cleanly and no hover chip appears
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
      }

      // 4. Chat links unchanged + note beside a chat (single header row!) + HTML beside a chat.
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
        assert.equal(await side.locator('.side-head').count(), 0, 'note beside chat has no duplicate .side-head')
        assert.equal(await side.locator('.dc-head.dc-sidehead').count(), 1, 'note beside chat has single .dc-head.dc-sidehead')
        assert.equal(await side.locator('.sh-sharebtn').count(), 1, 'single Share button beside chat')
        await capture('note-beside-chat')

        // Hover chip in side panel sits below link, opaque
        await side.locator(`.dc-link[data-url="${external}"]`).first().hover()
        const sideChip = side.locator('.dc-lchip.on')
        await sideChip.waitFor()
        const sideGeom = await side.evaluate((el, ext) => {
          const l = el.querySelector(`.dc-link[data-url="${ext}"]`)
          const c = el.querySelector('.dc-lchip.on')
          return { link: l.getBoundingClientRect().toJSON(), chip: c.getBoundingClientRect().toJSON() }
        }, external)
        assert.ok(sideGeom.chip.top >= sideGeom.link.bottom - 1, 'side panel chip sits below link')
        await capture('side-chat-chip-below')

        // Plain-click wiki-link & HTML link inside side panel beside chat
        await side.locator('.dc-wiki[data-wiki="other#Focus"]').first().click()
        await side.locator('.dc .cm-content').filter({ hasText: 'Companion note' }).waitFor()
        assert.equal(new URL(page.url()).pathname, threadPath)
        await side.getByRole('button', { name: 'Back', exact: true }).click()
        await side.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
        await side.locator(`.dc-link[data-url="${htmlPath}"]`).first().click()
        await side.frameLocator('iframe').getByRole('heading', { name: 'Friday review' }).waitFor()
        assert.equal(await side.locator('.side-head').count(), 1, 'HTML beside chat keeps .side-head')
        await side.getByRole('button', { name: 'Back', exact: true }).click()
        await side.locator('.dc .cm-content').filter({ hasText: 'The weekly plan' }).waitFor()
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
