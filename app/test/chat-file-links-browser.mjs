import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import { filesHost } from './files-host.js'
import { row } from './fixtures/files-contract.js'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const output = process.env.CHAT_LINKS_EVIDENCE
const baseline = process.env.CHAT_LINKS_BASELINE === '1'
const previewOrigin = 'https://preview.example.com'
const external = 'https://example.org/reading-list'
const threadPath = '/threads/a-quieter-week'
const htmlPath = '/files/vault/Notes/page.html'
const notePath = '/files/vault/Notes/plan.md'
const imagePath = '/files/vault/Notes/pic.png'
const pdfPath = '/files/vault/Notes/report.pdf'
const plan = `# The weekly plan

Leave a little room after Friday's review to think through what we learned.

## Next steps

- Give the first draft another read.
- Write down one thing we want to try next week.

Bring the [reference image](${imagePath}) and the [one-page brief](${pdfPath}).`
const mockup = `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: light dark; background: light-dark(#f3f4eb,#252b24); color: light-dark(#2d382d,#e6eadc); }
  body { margin: clamp(24px,6vw,68px); font: 16px/1.6 system-ui; }
  p { max-width: 36ch; } h1 { font: 400 clamp(36px,6vw,60px)/1.02 Georgia; margin: 18px 0; }
  small { letter-spacing: .12em; text-transform: uppercase; } li { margin-block: 8px; }
</style>
<small>Studio planning</small><h1>Friday review</h1><p>Set aside an hour to look back on the week and choose where to spend our attention next.</p>
<ul><li>Read the short first draft.</li><li>Bring one open question.</li><li>Leave the afternoon free.</li></ul>
<script>document.body.dataset.ready = 'true'</script>`

if (output) await mkdir(output, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const wide = width === 1440
    const app = await oneProductFixture({ FILES_CONTENT_ORIGIN: previewOrigin })
    const context = await browser.newContext({ viewport: { width, height: wide ? 900 : 844 }, colorScheme: mode,
      hasTouch: !wide, isMobile: !wide, reducedMotion: 'reduce' })
    try {
      const host = filesHost({ texts: { 'vault:Notes/plan.md': plan } })
      Object.assign(app.routes, Object.fromEntries(Object.entries(host.routes).filter(([path]) => path.includes('/files/'))))
      app.routes['GET /v1/files/resolve'] = (req, res, url) => {
        const ref = url.searchParams.get('ref')
        if (![htmlPath, imagePath, pdfPath].includes(ref)) return host.routes['GET /v1/files/resolve'](req, res, url)
        res.end(JSON.stringify({ status: 'found', resource: row('vault', ref.slice('/files/vault/'.length)) }))
      }
      const record = await app.f.api('GET', threadPath)
      record.messages.find(message => message.id === 'm4').text = `The [[plan#Next steps|weekly plan]] is ready. Open the [Friday review mockup](${htmlPath}) to see how it looks, or save the [reading list](${external}) for later.`
      app.routes[`GET /v1${threadPath}`] = (_, res) => res.end(JSON.stringify(record))
      const appearance = { theme: 'latte', mode, size: 100 }
      app.routes['GET /v1/settings/appearance'] = (_, res) => res.end(JSON.stringify({ phone: appearance, desktop: appearance }))

      let previewRequests = 0
      await context.route(`${previewOrigin}/**`, route => {
        previewRequests++
        return route.fulfill({ contentType: 'text/html', body: mockup })
      })
      await context.route(external, route => route.fulfill({ body: 'Reading list' }))
      await context.route(`${app.url}/sw.js`, route => route.abort())
      const page = await context.newPage()
      page.setDefaultTimeout(8000)
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      const capture = async (name) => {
        if (!output) return
        await page.evaluate(() => document.fonts.ready)
        await page.screenshot({ path: join(output, `${name}-${width}-${mode}.png`), animations: 'disabled' })
      }
      await page.goto(app.url + threadPath)
      const chat = page.locator('.tt-stream')
      const note = chat.getByRole('link', { name: 'weekly plan' })
      const html = chat.getByRole('link', { name: 'Friday review mockup' })
      await html.scrollIntoViewIfNeeded()
      await page.evaluate(() => document.fonts.ready)
      await page.mouse.move(0, 0)
      const linkLayout = () => html.evaluate(link => {
        const line = document.createRange()
        line.selectNodeContents(link.parentNode)
        return [...line.getClientRects()].map(rect => rect.toJSON())
      })
      const rest = await linkLayout()
      await capture('chat')
      if (wide) {
        await html.hover()
        assert.deepEqual(await linkLayout(), rest)
        if (!baseline) assert.equal(await chat.locator('.filelink-side').count(), 0)
        await capture('hover')
      }

      const viewer = wide ? page.getByRole('region', { name: 'Side panel' }) : page.locator('.screen.files')
      const frame = viewer.frameLocator('iframe')
      const heading = frame.getByRole('heading', { name: 'Friday review' })
      const actions = viewer.getByRole('button', { name: wide ? 'Item actions' : 'This file', exact: true })
      if (wide && baseline) await chat.locator('a:has-text("Friday review mockup") + .filelink-side').click()
      else if (wide) await html.click()
      else await html.tap()
      await heading.waitFor()
      await frame.locator('body[data-ready=true]').waitFor()
      assert.equal(new URL(page.url()).pathname, wide ? threadPath : htmlPath)
      await capture('html')
      await actions.click()
      const menu = page.locator('.fs-sheet')
      await menu.waitFor()
      await capture('menu')
      if (baseline) {
        if (wide) {
          await page.keyboard.press('Escape')
          await note.hover()
          await chat.locator('a:has-text("weekly plan") + .filelink-side').click()
          await viewer.locator('.fs-preview').waitFor()
          await capture('note')
        }
        continue
      }

      assert.equal(await viewer.locator('.fs-htmlreload, .fs-htmlopen').count(), 0)
      if (wide) {
        const header = await viewer.locator('.side-head').boundingBox()
        const iframe = await viewer.locator('iframe').boundingBox()
        assert.equal(iframe.y, header.y + header.height, 'the preview starts immediately below the side header')
      }
      const reload = menu.getByRole('button', { name: 'Reload', exact: true })
      const open = menu.getByRole('link', { name: 'Open in new tab', exact: true })
      assert.equal(await open.getAttribute('href'), await viewer.locator('iframe').getAttribute('src'))
      const oldRequests = previewRequests
      await reload.click()
      await menu.waitFor({ state: 'hidden' })
      await heading.waitFor()
      assert.equal(previewRequests, oldRequests + 1)
      await actions.click()
      const [popup] = await Promise.all([context.waitForEvent('page'), open.click()])
      await popup.getByRole('heading', { name: 'Friday review' }).waitFor()
      await popup.close()
      await menu.waitFor({ state: 'hidden' })

      if (wide) {
        await note.click()
        await viewer.locator('.fs-preview').getByRole('heading', { name: 'Next steps' }).waitFor()
        await capture('note')
        assert.equal(await viewer.locator('.side-head').count(), 1)
        assert.equal(await viewer.locator('.fs-edhead').count(), 0)
        await actions.click()
        await menu.getByRole('button', { name: 'Edit', exact: true }).click()
        await viewer.locator('.fs-field').waitFor()
        await actions.click()
        await menu.getByRole('button', { name: 'Preview', exact: true }).click()
        await viewer.locator('.fs-preview').waitFor()
        for (const [link, preview] of [['reference image', '.fs-img'], ['one-page brief', '.fs-pdf']]) {
          await viewer.getByRole('link', { name: link, exact: true }).click()
          await viewer.locator(preview).waitFor()
          assert.equal(await viewer.locator('.fs-edhead').count(), 0)
          await actions.click()
          await menu.getByRole('link', { name: 'Download', exact: true }).waitFor()
          await page.keyboard.press('Escape')
          await viewer.getByRole('button', { name: 'Back', exact: true }).click()
          await viewer.locator('.fs-preview').waitFor()
        }
        await note.click({ modifiers: ['Meta'] })
        await page.waitForURL(url => url.pathname === notePath)
        await viewer.locator('.fs-preview').waitFor()
        await page.goBack()
        await html.click({ modifiers: ['Control'] })
        await page.waitForURL(url => url.pathname === htmlPath)
        await page.goBack()
        const [tab] = await Promise.all([context.waitForEvent('page'), html.click({ button: 'middle' })])
        await tab.waitForURL(`**${htmlPath}`)
        await tab.close()
      } else {
        await page.goBack()
        await note.tap()
        await viewer.locator('.fs-preview').getByRole('heading', { name: 'Next steps' }).waitFor()
        await page.goBack()
      }
      const [web] = await Promise.all([context.waitForEvent('page'), chat.getByRole('link', { name: 'reading list' }).click()])
      await web.waitForURL(external)
      await web.close()
      assert.equal(new URL(page.url()).pathname, threadPath)
      assert.deepEqual(errors, [])
      console.log(`${width} ${mode} passed`)
    } finally {
      await context.close()
      await app.close()
    }
  }
} finally {
  await browser.close()
}
