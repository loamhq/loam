// All integration operations are loopback fixtures. External networking is
// aborted; this runner cannot initiate a real device-code login.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { acmePluginFixture } from './acme-plugin-fixture.mjs'
import { SHAPES, EXPIRED, PENDING } from './fixtures/acme.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const look = process.env.ACME_LOOK || 'latte'
const out = process.env.ACME_EVIDENCE || path.join(process.env.TMPDIR, 'acme-plugin')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const reference = process.env.ACME_REFERENCE
const coreFiles = ['app.js', 'integration-catalog.js', 'integrations.js', 'marks.js', 'page/accounts.js', 'page/settings.js', 'page/sheets.js', 'style.css']
const old = reference ? Object.fromEntries(coreFiles.map(file => [file, execFileSync('git', ['show', `${reference}:public/${file}`], { encoding: 'utf8' })])) : {}
const results = [], screenshots = new Map()
try {
  for (const width of process.env.ACME_WIDTH ? [Number(process.env.ACME_WIDTH)] : [390, 1440]) for (const mode of process.env.ACME_MODE ? [process.env.ACME_MODE] : ['light', 'dark']) {
    for (const phase of reference ? ['before', 'after'] : ['after']) {
      const app = await acmePluginFixture({ mode, look }), errors = [], consoleErrors = [], networkErrors = [], external = [], photos = [], comparisons = []
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 960 }, reducedMotion: 'reduce', colorScheme: mode,
        isMobile: width === 390, hasTouch: width === 390, serviceWorkers: 'block', permissions: ['clipboard-read', 'clipboard-write'] })
      const page = await context.newPage()
      page.on('pageerror', error => errors.push(error.message))
      page.on('console', message => { if (message.type() === 'error') (message.text().startsWith('Failed to load resource:') ? networkErrors : consoleErrors).push({ text: message.text(), url: message.location().url }) })
      await context.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url())
        if (url.origin !== app.url) { external.push(request.url()); return route.abort() }
        const file = url.pathname.slice(1)
        if (phase === 'before' && url.pathname.startsWith('/api/integrations/acme')) {
          const data = app.st.data
          if (request.method() === 'GET') return route.fulfill({ json: data })
          const routePath = url.pathname.replace('/api/integrations/acme', '/x/acme').replace(/acme$/, 'acme/')
          const answer = await fetch(app.url + routePath, { method: request.method(), headers: { 'Content-Type': 'application/json' }, body: request.postData() ?? undefined })
          return route.fulfill({ status: answer.status, json: await answer.json() })
        }
        if (file === 'app.js') {
          const source = phase === 'before' ? old[file] : await readFile('public/app.js', 'utf8')
          return route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__u2 = {state,page,goSettings,openSheet,closeSheet,pluginLoader,schedule,theme};' })
        }
        if (phase === 'before' && old[file]) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: old[file] })
        if (phase === 'before' && file === 'api/plugins') return route.fulfill({ json: { plugins: [] } })
        // Ancillary surfaces are not under test; supply their empty host shapes
        // so console diagnostics are reserved for real page faults.
        const empty = { '/api/apps': { apps: [] }, '/api/schedules': { schedules: [] }, '/api/running': { live: [], queue: [] }, '/api/files/roots': { roots: [] } }
        if (empty[url.pathname]) return route.fulfill({ json: empty[url.pathname] })
        return route.continue()
      })
      const shot = async label => {
        await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(100)
        const name = `${label}-${width}-${mode}.png`, target = page.locator('[data-menu-surface]').last()
        const surface = await target.count() ? target : page.locator('.sheet').last()
        const grip = await surface.evaluate(node => {
          const element = node.querySelector('.grip')
          if (!element || getComputedStyle(element).visibility === 'hidden') return null
          const rect = element.getBoundingClientRect(), box = node.getBoundingClientRect(), css = getComputedStyle(element)
          return { x: rect.x - box.x, y: rect.y - box.y, width: rect.width, height: rect.height, color: css.backgroundColor, radius: css.borderRadius }
        })
        const buffer = await surface.screenshot({ path: path.join(out, `${phase}-${name}`), animations: 'disabled' })
        photos.push(name)
        if (process.env.ACME_TRACE) console.log('CAPTURE', phase, name)
        if (phase === 'before') screenshots.set(name, { buffer, grip })
        else if (reference) {
          const before = screenshots.get(name)
          if (buffer.equals(before.buffer)) comparisons.push({ name, exact: true })
          else {
            // Chromium can rasterize the unchanged 40px rounded grip at a
            // different subpixel phase. Require identical geometry/paint and
            // byte-identical pixels everywhere outside that grip alone.
            assert.deepEqual(grip, before.grip, `Grip geometry: ${name}`)
            assert.ok(grip, `Visual parity: ${name}`)
            const difference = await page.evaluate(async ({ first, second, grip }) => {
              const decode = async value => { const img = new Image(); img.src = 'data:image/png;base64,' + value; await img.decode(); return img }
              const a = await decode(first), b = await decode(second)
              if (a.width !== b.width || a.height !== b.height) return { dimensions: false }
              const canvas = document.createElement('canvas'); canvas.width = a.width; canvas.height = a.height
              const ctx = canvas.getContext('2d', { willReadFrequently: true })
              ctx.drawImage(a, 0, 0); const pixels = ctx.getImageData(0, 0, a.width, a.height).data
              ctx.clearRect(0, 0, a.width, a.height); ctx.drawImage(b, 0, 0); const other = ctx.getImageData(0, 0, b.width, b.height).data
              let inside = 0, outside = 0
              for (let i = 0; i < pixels.length; i += 4) if (pixels.slice(i, i + 4).some((v, k) => v !== other[i + k])) {
                const x = i / 4 % a.width, y = Math.floor(i / 4 / a.width)
                if (x >= grip.x - 1 && x <= grip.x + grip.width + 1 && y >= grip.y - 1 && y <= grip.y + grip.height + 1) inside++
                else outside++
              }
              return { dimensions: true, inside, outside }
            }, { first: before.buffer.toString('base64'), second: buffer.toString('base64'), grip })
            assert.ok(difference.dimensions && difference.outside === 0, `Visual parity: ${name}: ${JSON.stringify(difference)}`)
            comparisons.push({ name, exact: false, grip, difference })
          }
        }
      }
      async function open(data) {
        app.st.data = data
        await page.evaluate(() => __u2.closeSheet())
        await page.locator('[data-menu-surface]').waitFor({ state: 'detached' })
        await page.getByRole('button', { name: 'Settings', exact: true }).click()
        await page.evaluate(() => __u2.goSettings('integrations'))
        await page.locator('.setrow').filter({ has: page.getByText('Acme', { exact: true }) }).click()
        if (data.account) await page.locator('.setrow').filter({ has: page.getByText(data.account.name, { exact: true }) }).click()
        await page.locator('.card.acme').waitFor()
        await page.waitForFunction(() => !document.querySelector('.card.acme')?.textContent.includes('Asking'))
      }
      try {
        await page.goto(app.url); await page.waitForFunction(() => globalThis.__u2?.pluginLoader.st.loaded)
        if (phase === 'after') await page.waitForFunction(() => document.querySelector('link[href*="acme"][href*="style.css"]')?.sheet)
        await page.getByRole('button', { name: 'Settings', exact: true }).click()
        await page.evaluate(() => __u2.goSettings('integrations'))
        await page.getByText('Acme', { exact: true }).waitFor(); await shot('integrations')
        await open(SHAPES.unconfigured); await shot('unconfigured')
        await page.getByText('Paste a token instead', { exact: true }).click()
        await page.locator('#bc-token').waitFor(); await shot('token')
        await page.locator('#bc-token').fill('fixture-sentinel-token')
        await page.getByRole('button', { name: 'Connect', exact: true }).click()
        await page.locator('.bcpick').first().waitFor(); await shot('token-accounts')
        await page.locator('.bcpick').last().click(); await page.locator('.card.acme').waitFor()
        assert.equal(await page.locator('#bc-token').count(), 0)
        await shot('connected')
        await page.getByRole('button', { name: 'Refresh', exact: true }).click()
        await page.getByText('Checked with Acme just now.', { exact: true }).waitFor()
        await page.getByRole('button', { name: 'Disconnect', exact: true }).click(); await shot('disconnect')
        await page.locator('.ghconfirm').getByRole('button', { name: 'Disconnect', exact: true }).click()
        await page.getByRole('link', { name: 'Revoke the token in Acme', exact: true }).waitFor(); await shot('revoke')
        await open(SHAPES.unconfigured)
        await page.getByRole('button', { name: 'Sign in to Acme', exact: true }).click()
        await page.locator('.devcode').waitFor(); await shot('login')
        await page.getByRole('button', { name: 'Copy the code', exact: true }).click()
        assert.equal(await page.evaluate(() => navigator.clipboard.readText()), PENDING.user_code)
        app.st.data = SHAPES.accounts
        await page.locator('.bcpick').first().waitFor(); await shot('login-accounts')
        await page.locator('.bcpick').last().click(); await page.getByText('signed in · renews itself', { exact: true }).waitFor(); await shot('login-connected')
        await open(SHAPES.loginUnhealthy); await shot('unhealthy')
        await page.getByRole('button', { name: 'Sign in to Acme', exact: true }).click()
        await page.locator('.devcode').waitFor()
        app.st.data = SHAPES.expired
        await page.getByText(EXPIRED, { exact: true }).waitFor(); await shot('expired')
        await page.getByRole('button', { name: 'Try again', exact: true }).click()
        await page.locator('.devcode').waitFor()
        await page.getByRole('button', { name: 'Cancel', exact: true }).click()
        if (phase === 'after') {
          const readCount = () => app.seen.filter(r => r.method === 'GET').length
          const before = readCount(); await page.waitForTimeout(3200); assert.equal(readCount(), before, 'Cancel stops polling')
          // Enabled state refresh must remove the UI, open sheet and CSS, and
          // cancel a slow state request before a later enable starts fresh.
          await open(SHAPES.unconfigured)
          await page.getByText('Paste a token instead', { exact: true }).click()
          await page.locator('#bc-token').fill('fixture-disable-sentinel')
          app.st.catalog = 'off'; await page.evaluate(() => __u2.pluginLoader.refresh())
          await page.locator('#bc-token').waitFor({ state: 'detached' })
          assert.equal(await page.locator('#bc-token').count(), 0)
          assert.equal(await page.locator('link[href*="acme"]').count(), 0)
          await page.evaluate(() => __u2.goSettings('integrations'))
          await page.getByText('Acme', { exact: true }).waitFor({ state: 'detached' })
          assert.equal(await page.getByText('Acme', { exact: true }).count(), 0)
          const offCount = app.seen.length
          await page.reload(); await page.waitForFunction(() => globalThis.__u2?.pluginLoader.st.loaded)
          await page.evaluate(() => __u2.openSheet({ kind: 'settings', page: 'integrations' }))
          assert.equal(await page.getByText('Acme', { exact: true }).count(), 0)
          assert.equal(app.seen.length, offCount)
          app.st.catalog = 'absent'; await page.evaluate(() => __u2.pluginLoader.refresh())
          assert.equal(await page.getByText('Acme', { exact: true }).count(), 0)
          assert.equal(app.seen.length, offCount)
          app.st.catalog = 'on'; await page.evaluate(() => __u2.pluginLoader.refresh())
          await page.getByText('Acme', { exact: true }).waitFor()
          let release; app.st.held = new Promise(resolve => { release = resolve })
          await page.evaluate(() => __u2.openSheet({ kind: 'settings', page: 'integrations' }))
          await page.waitForTimeout(50)
          app.st.catalog = 'off'; await page.evaluate(() => __u2.pluginLoader.refresh())
          app.st.held = null; release(); await page.waitForTimeout(150)
          assert.equal(await page.getByText('Acme', { exact: true }).count(), 0)
          app.st.catalog = 'on'; await page.evaluate(() => __u2.pluginLoader.refresh())
          await open(SHAPES.unconfigured)
          assert.ok(app.seen.every(r => r.auth === undefined))
          assert.equal(app.broker.seen.some(r => r.path.includes('/integrations/acme')), false)
        }
        if (process.env.ACME_TRACE) console.log('NETWORK', networkErrors)
        assert.deepEqual(errors, []); assert.deepEqual(consoleErrors, []); assert.deepEqual(external, [])
        assert.ok(networkErrors.every(error => error.text.includes('400 (Bad Request)') && ['/x/acme/', '/api/integrations/acme'].includes(new URL(error.url).pathname)), 'Only the deliberate multiple-account 400 may produce a resource diagnostic')
        results.push({ width, look, mode, phase, errors, consoleErrors, networkErrors, external, photos, comparisons, requests: app.seen.map(r => ({ method: r.method, path: r.path, auth: r.auth ?? null })) })
        console.log(`PASS ${phase} ${width} ${mode}: ${photos.length} matching surfaces, flows${phase === 'after' ? ', on/off/absent and cancellation' : ''}; zero page or application console errors`)
      } catch (error) {
        await page.screenshot({ path: path.join(out, `failure-${phase}-${width}-${mode}.png`) })
        console.log('FAILURE STATE', await page.evaluate(() => ({ sheet: __u2?.state.sheet, active: document.activeElement?.tagName, dialogs: [...document.querySelectorAll('[data-menu-surface]')].map(n => n.outerHTML.slice(0, 500)) })))
        throw error
      } finally { await context.close(); await app.close() }
    }
  }
  await writeFile(path.join(out, 'results.json'), JSON.stringify({ chromium: browser.version(), fixturesOnly: true, reference, results }, null, 2) + '\n')
} finally { await browser.close() }
