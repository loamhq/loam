import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { deleteHost } from './fixtures/files-delete.js'

const baseline = process.env.FILES_DELETE_BASELINE
const fixtureModule = baseline ? pathToFileURL(join(baseline, 'test/one-product-fixture.mjs')) : './one-product-fixture.mjs'
const { oneProductFixture } = await import(fixtureModule)
const out = process.env.FILES_DELETE_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture()
    const host = deleteHost({ supported: !baseline })
    host.texts.set('Notes/Taken.md', '# Meeting notes\n\n[Open the planning note](/files/vault/Notes/plan.md)')
    host.routes['GET /v1/files/resolve'] = (req, res) => {
      const resource = host.entries.get('Notes/plan.md')
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(resource ? { status: 'found', resource } : { status: 'missing' }))
    }
    Object.assign(app.routes, host.routes)
    app.routes['GET /v1/settings/appearance'] = (req, res) => {
      const look = { mode, theme: 'latte', size: 100 }
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ phone: look, desktop: look }))
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 }, colorScheme: mode,
      reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
    const page = await context.newPage(), errors = []
    page.setDefaultTimeout(8000)
    page.on('pageerror', error => errors.push(error.message))
    page.on('dialog', dialog => { errors.push(`Unexpected native dialog: ${dialog.message()}`); dialog.dismiss() })
    const button = name => page.getByRole('button', { name, exact: true })
    const row = name => page.locator('.fs-name').getByText(name, { exact: true })
    const menu = page.locator('.fs-sheet:popover-open')
    const confirm = page.locator('.fs-delete-confirm:popover-open')
    const shot = async name => {
      if (!out) return
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ path: join(out, `${name}-${width}-${mode}.png`), animations: 'disabled' })
    }
    const seeDelete = async () => {
      await button('Delete').waitFor()
      assert.equal(await menu.locator('.sheetrow').last().innerText(), 'Delete')
    }
    try {
      await page.goto(`${app.url}/files/vault/Notes`)
      await page.waitForFunction(() => document.documentElement.dataset.theme === (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'))
      await button('Actions for plan.md').click()
      await menu.waitFor()
      if (!baseline) await seeDelete()
      else assert.equal(await button('Delete').count(), 0)
      await shot('row-menu')
      if (!baseline) {
        await button('Delete').click()
        await confirm.waitFor()
        assert.match(await confirm.innerText(), /plan\.md/)
        assert.equal(await button('Cancel').evaluate(node => document.activeElement === node), true)
        await shot('row-confirm')
        await button('Cancel').click()
        await confirm.waitFor({ state: 'detached' })
        assert.equal(host.state.calls.length, 0)
      } else await page.keyboard.press('Escape')
      await menu.waitFor({ state: 'detached' })

      await row('Taken.md').click()
      await page.getByRole('link', { name: 'Open the planning note' }).click()
      await page.waitForURL('**/files/vault/Notes/plan.md')
      await page.getByText('The next useful step', { exact: true }).waitFor()
      await button('This file').click()
      await menu.waitFor()
      if (!baseline) await seeDelete()
      else assert.equal(await button('Delete').count(), 0)
      await shot('header-menu')
      if (baseline) continue
      await button('Delete').click()
      await confirm.waitFor()
      await shot('header-confirm')
      host.state.refuse = true
      await button('Delete file').click()
      await confirm.getByText('The vault refused this deletion.').waitFor()
      assert.equal(host.entries.has('Notes/plan.md'), true)
      assert.match(new URL(page.url()).pathname, /plan\.md$/)
      host.state.refuse = false
      await button('Delete file').click()
      await page.waitForURL('**/files/vault/Notes/Taken.md')
      await page.getByText('Meeting notes', { exact: true }).waitFor()
      await page.getByText('Deleted plan.md.', { exact: true }).waitFor()
      await confirm.waitFor({ state: 'detached' })
      assert.equal(host.entries.has('Notes/plan.md'), false)
      assert.equal(host.state.calls.length, 2)
      assert.equal(await page.evaluate(() => [...history.state.workspace.visits, ...history.state.workspace.forward].some(step => step.href.endsWith('/plan.md'))), false)
      await shot('header-undo')
      await button('Undo').click()
      await page.getByText('Restored plan.md.', { exact: true }).waitFor()
      assert.equal(host.entries.has('Notes/plan.md'), true)
      assert.match(new URL(page.url()).pathname, /Taken\.md$/)

      if (width === 1440) {
        await button('Actions for plan.md').waitFor()
        await row('page.html').click({ modifiers: ['Alt'] })
        const side = page.getByRole('region', { name: 'Side panel' })
        await side.locator('.side-heading strong').getByText('page.html', { exact: true }).waitFor()
        await page.getByTitle('Show sidebar', { exact: true }).click()
        await row('pic.png').click()
        await row('pic.png').click({ modifiers: ['Alt'] })
        await side.locator('.side-heading strong').getByText('pic.png', { exact: true }).waitFor()
        await side.getByRole('button', { name: 'Item actions', exact: true }).click()
        await seeDelete()
        await button('Delete').click()
        await button('Delete file').click()
        await side.locator('.side-heading strong').getByText('page.html', { exact: true }).waitFor()
        await page.getByText('Meeting notes', { exact: true }).waitFor()
        assert.equal(host.entries.has('Notes/pic.png'), false)
        assert.equal(await page.evaluate(() => new URL(location.href).searchParams.get('side') && JSON.parse(new URL(location.href).searchParams.get('side')).path), 'Notes/page.html')
      } else {
        await page.goBack()
        await button('Actions for Taken.md').waitFor()
        await button('Actions for plan.md').waitFor()
      }
    } finally {
      await context.close()
      await app.close()
      assert.deepEqual(errors, [])
    }
    console.log(`PASS ${width} ${mode}`)
  }
} finally { await browser.close() }
