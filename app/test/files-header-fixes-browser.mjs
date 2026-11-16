// Local fixture evidence only. Playwright and Chromium are supplied externally.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'

const out = process.env.FILES_HEADER_EVIDENCE
assert.ok(out, 'Set FILES_HEADER_EVIDENCE to a retained evidence directory')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const reports = [], baseline = process.env.FILES_HEADER_BASE_REF
const source = await readFile('public/app.js', 'utf8')
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }

async function measure(page, fixture = false) {
  await page.evaluate(() => document.fonts.ready)
  return page.locator(fixture ? '.header-fixture .tt-head' : '.tt-head').evaluate(head => {
    const face = head.querySelector(':scope > .who > *, :scope > .agentbtn > *').getBoundingClientRect()
    const title = head.querySelector('.headtitle-text, .tt-title .display').getBoundingClientRect()
    const iconCenter = face.y + face.height / 2, textCenter = title.y + title.height / 2
    return { iconCenter, textCenter, delta: Math.abs(iconCenter - textCenter), textHeight: title.height }
  })
}

try {
  for (const width of [390, 1440]) for (const look of ['latte', 'plain']) for (const mode of ['light', 'dark']) {
    const app = await oneProductFixture(), label = `${width}-${look}-${mode}`
    const files = filesHost()
    for (const [key, handler] of Object.entries(files.routes)) if (key.includes('/v1/files/')) app.routes[key] = handler
    const values = { phone: { mode, theme: look, size: 100 }, desktop: { mode, theme: look, size: 100 } }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
    app.routes['GET /v1/activity'] = (req, res) => json(res, { jobs: { slots: [], landing: [], queue: [] }, items: [], total: 0, next_offset: null })
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
    const page = await context.newPage(), errors = [], report = { label }
    page.setDefaultTimeout(10000)
    page.on('pageerror', error => errors.push(error.message))
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__fixes = { state, store, files, threads, goFiles, goSettings, goActivity, closeSheet };' }))
    if (baseline) for (const file of ['chat-ui.js', 'style.css', 'threads.css', 'files.js', 'store.js', 'page/files.js', 'page/settings.js']) {
      const body = execFileSync('git', ['show', `${baseline}:public/${file}`], { encoding: 'utf8' })
      await context.route(`**/${file}`, route => route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body }))
    }
    const shot = async name => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      await page.screenshot({ path: path.join(out, `${name}-${label}.png`), animations: 'disabled' })
    }
    try {
      await page.goto(`${app.url}/threads/a-quieter-week`)
      await page.waitForFunction(() => globalThis.__fixes)
      await page.waitForFunction(() => __fixes.threads.st.open && !__fixes.threads.st.open.loading)
      report.appearance = await page.evaluate(() => ({ look: document.documentElement.dataset.look, mode: document.documentElement.dataset.theme }))
      assert.deepEqual(report.appearance, { look, mode })
      await page.locator('.tt-head .tt-title').waitFor()
      report.noSubtitle = await measure(page)
      if (!baseline) {
        assert.ok(report.noSubtitle.delta <= 1, `${label}: ${JSON.stringify(report.noSubtitle)}`)
        assert.equal(await page.locator('.tt-head .headtitle-who').count(), 0)
      }
      await shot('thread')
      if (!baseline) {
        // Exercise the shared header's real subtitle branch. Runtime threads
        // currently supply none; this fixture keeps that branch measurable.
        await page.evaluate(async wide => {
          const { chatHeader } = await import('/chat-ui.js')
          const old = document.querySelector('.tt-head'), face = old.querySelector(':scope > .who > *, :scope > .agentbtn > *').cloneNode(true)
          const rect = old.getBoundingClientRect(), holder = document.createElement('div')
          holder.className = 'tt-thread header-fixture'
          Object.assign(holder.style, { position: 'fixed', top: `${rect.y}px`, left: `${rect.x}px`, width: `${rect.width}px`, height: 'auto', zIndex: '9999', background: 'var(--paper-page)' })
          holder.append(chatHeader({ wide, face, title: 'A quieter week', subtitle: 'Ready for your next thought', onRename() {},
            back: old.querySelector('.tt-back')?.cloneNode(true), actions: [...old.querySelectorAll(':scope > .iconbtn')].filter(n => !n.classList.contains('tt-back')).map(n => n.cloneNode(true)),
            props: { class: `${wide ? 'chat-head' : 'thread-head'} tt-head` }, titleProps: { class: `${wide ? 'chattitle' : 'headtitle'} tt-title` } }))
          document.body.append(holder)
        }, width === 1440)
        report.withSubtitle = await measure(page, true)
        assert.ok(report.withSubtitle.delta <= 1, `${label} subtitle: ${JSON.stringify(report.withSubtitle)}`)
        assert.ok(report.withSubtitle.textHeight > report.noSubtitle.textHeight)
        await page.locator('.header-fixture .headtitle-who').waitFor()
        await page.locator('.header-fixture').screenshot({ path: path.join(out, `thread-subtitle-${label}.png`) })
      }
      await page.goto(`${app.url}/files`)
      await page.waitForFunction(() => globalThis.__fixes)
      await page.locator('.fs-host-shortcut').nth(1).waitFor()
      if (!baseline) {
        await page.evaluate(() => { __fixes.store.files = { ...__fixes.store.files, shortcuts: [{ root: 'vault', path: 'Notes/plan.md', kind: 'file' }, { root: 'vault', path: 'Notes' }] }; __fixes.goFiles(null, '') })
        await page.locator('.fs-shortcut').nth(1).waitFor()
      }
      await shot('files-home')
      if (baseline) { reports.push(report); continue }
      const target = page.getByRole('button', { name: 'Shortcut actions for System notes', exact: true })
      await target.click()
      await page.locator('.fs-shortcuttoggle').waitFor()
      await shot('folder-menu')
      await page.locator('.fs-shortcuttoggle').click()
      await target.waitFor({ state: 'detached' })
      // File and personal-folder controls take exactly the same removal path.
      for (const name of ['plan.md', 'Notes']) {
        await page.getByRole('button', { name: `Shortcut actions for ${name}`, exact: true }).click()
        await page.locator('.fs-shortcuttoggle').click()
        await page.getByRole('button', { name: `Shortcut actions for ${name}`, exact: true }).waitFor({ state: 'detached' })
      }
      await page.reload()
      await page.locator('.fs-host-shortcut').waitFor()
      assert.equal(await page.locator('.fs-host-shortcut').count(), 1)
      assert.equal(await page.locator('.fs-shortcut').count(), 0)
      await page.getByRole('button', { name: 'Shortcut actions for App', exact: true }).click()
      await page.locator('.fs-shortcuttoggle').click()
      await page.locator('.fs-noshort').waitFor()
      await page.reload()
      await page.locator('.fs-noshort').waitFor()
      await shot('files-unpinned')
      report.removal = 'Suggested and personal folders and a file removed through the same control; reload retained removals and empty state.'
      // Re-pin a dismissed suggestion through the existing folder menu.
      await page.evaluate(() => __fixes.goFiles('vault', '00_System'))
      await page.locator('.fs-menu').click()
      assert.match(await page.locator('.fs-shortcuttoggle').innerText(), /^Add to shortcuts/)
      await page.locator('.fs-shortcuttoggle').click()
      await page.evaluate(() => __fixes.goFiles(null, ''))
      await page.locator('.fs-shortcut').waitFor()
      assert.equal(await page.locator('.fs-shortcut').count(), 1)
      assert.equal(await page.locator('.fs-host-shortcut').count(), 0)
      report.repin = 'Dismissed suggestion re-added once from its folder menu.'
      await page.evaluate(() => __fixes.goSettings('files'))
      await page.getByRole('button', { name: 'Remove shortcut to 00_System', exact: true }).waitFor()
      await shot('settings-files')
      await page.getByRole('button', { name: 'Remove shortcut to 00_System', exact: true }).click()
      await page.evaluate(() => { __fixes.closeSheet(); __fixes.goFiles(null, '') })
      await page.locator('.fs-noshort').waitFor()
      report.settingsRemoval = 'Settings uses the same membership: removing the re-pinned suggestion leaves the Files home empty.'
      await page.evaluate(() => __fixes.goFiles('vault', 'Notes/plan.md', { isFile: true }))
      await page.locator('.fs-edtitle').waitFor()
      await shot('file-viewer')
      await page.evaluate(() => __fixes.goSettings('root'))
      await page.locator('.settings .sheet-title').waitFor()
      await shot('settings')
      await page.evaluate(() => { __fixes.closeSheet(); __fixes.goActivity() })
      await page.locator('.activity-head').waitFor()
      await shot('activity')
      report.errors = errors
      assert.deepEqual(errors, [])
      reports.push(report)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'measurements.json'), JSON.stringify({ baseline: baseline || null, reports }, null, 2) + '\n')
  console.log(JSON.stringify({ result: 'passed', variants: reports.length, reports }))
} finally { await browser.close() }
