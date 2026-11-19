import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'
import http from 'node:http'
import { once } from 'node:events'
import { row, readAnswer } from './fixtures/files-contract.js'
import { shut, until } from './harness.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
let app
const content = http.createServer((req, res) => {
  const upstream = http.request(`${app.url}${req.url}`, { method: req.method, headers: req.headers }, answer => { res.writeHead(answer.statusCode, answer.headers); answer.pipe(res) })
  upstream.on('error', () => res.destroy()); req.pipe(upstream)
})
content.listen(0, '127.0.0.1'); await once(content, 'listening')
const out = process.env.PANELS_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const reports = [], errors = [], posts = []
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const appearance = { phone: { theme: 'latte', mode: 'light', size: 100 }, desktop: { theme: 'latte', mode: 'light', size: 100 } }
const html = '<!doctype html><html><body><h1>A living plan</h1><button id="grow">Plant a thought</button><script>document.body.dataset.live="yes";document.querySelector("#grow").onclick=()=>document.querySelector("#grow").textContent="Growing"</script></body></html>'
function resetFiles() {
  const files = filesHost({ texts: { 'vault:Notes/plan.md': '# A little room for the week\n\nA good plan leaves room for the unexpected. Keep the work in sight while the conversation finds its next step.\n\n## Next steps\n\n- [ ] Choose the one thing worth your best attention.\n- [ ] Read the short first draft.\n- [ ] Leave Friday afternoon open.\n\nThe [[plan#Next steps]] stay close while you think.' } })
  for (const [key, handler] of Object.entries(files.routes)) if (key.includes('/v1/files/')) app.routes[key] = handler
  const read = app.routes['GET /v1/files/read'], download = app.routes['GET /v1/files/download']
  app.routes['GET /v1/files/read'] = (req, res, url) => url.searchParams.get('path') === 'Notes/page.html' ? json(res, readAnswer(row('vault', 'Notes/page.html'), html, 'sha256:html')) : read(req, res, url)
  app.routes['GET /v1/files/download'] = (req, res, url) => {
    if (url.searchParams.get('path') !== 'Notes/page.html') return download(req, res, url)
    res.writeHead(200, { 'content-type': 'application/octet-stream' }); res.end(html)
  }
}
const catalog = [{ name: 'Garden', url: 'https://garden.example.com/', owner: 'sprout', frame: true, source: 'yours', access: 'private', also_at: [] }]
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const a = 'a-quieter-week', b = 'small-details'
async function resetApp() {
  await app?.close()
  app = await oneProductFixture({ FILES_CONTENT_ORIGIN: `http://localhost:${content.address().port}` })
  resetFiles()
  app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, appearance)
  app.routes['GET /v1/apps'] = (req, res) => json(res, { apps: catalog, sources: ['yours'] })
  for (const id of [a, b]) {
    const previous = app.routes[`POST /v1/threads/${id}/messages`]
    app.routes[`POST /v1/threads/${id}/messages`] = (...args) => { posts.push({ id, body: JSON.parse(args[3]) }); return previous(...args) }
  }
  app.routes[`POST /v1/threads/${b}/attachments`] = (req, res) => {
    res.writeHead(201, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ attachments: [{ path: 'attachments/side-note.txt', name: 'side-note.txt', type: 'text/plain', size: 11 }] }))
  }
}
try {
  for (const width of (process.env.PANELS_REVIEW ? [1180, 1440, 1920] : process.env.PANELS_TOUCH ? [1180] : process.env.PANELS_SMOKE ? [1440] : [1180, 1440, 1920, 390])) for (const theme of (process.env.PANELS_SMOKE || process.env.PANELS_TOUCH || process.env.PANELS_REVIEW ? ['latte'] : ['latte', 'plain'])) for (const mode of (process.env.PANELS_SMOKE || process.env.PANELS_TOUCH ? ['light'] : ['light', 'dark'])) {
    if (process.env.PANELS_WIDTH && width !== Number(process.env.PANELS_WIDTH)) continue
    await resetApp()
    appearance.phone = appearance.desktop = { theme, mode, size: 100 }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, reducedMotion: 'reduce', hasTouch: width === 390 || Boolean(process.env.PANELS_TOUCH), isMobile: width === 390 })
    await context.addInitScript(() => {
      localStorage.setItem('loam-ui.speech', JSON.stringify({ listenFrom: 1, briefFrom: 10000 }))
      localStorage.setItem('loam-ui.dictation', JSON.stringify({ mode: 'browser' }))
      window.__recognisers = []
      window.SpeechRecognition = class extends EventTarget {
        constructor() { super(); __recognisers.push(this) }
        start() {} stop() {}
      }
      window.__audio = []
      window.Audio = class extends EventTarget {
        constructor() { super(); Object.assign(this, { paused: true, ended: false, duration: 90, currentTime: 0, buffered: { length: 0 } }); __audio.push(this) }
        play() { this.paused = false; this.dispatchEvent(new Event('play')); return Promise.resolve() }
        pause() { this.paused = true; this.dispatchEvent(new Event('pause')) }
        load() {} removeAttribute() {}
      }
    })
    // Audio plugins are off on a fresh install; this workflow opts them in.
    await context.route('**/shipped-plugins.json', async route => {
      const response = await route.fetch(), body = await response.json()
      await route.fulfill({ json: { ...body, plugins: body.plugins.map(plugin => plugin.switch === 'plugin' ? { ...plugin, on: true } : plugin) } })
    })
    await context.route('**/api/health', route => route.fulfill({ json: { configured: true, ok: true, speech: true } }))
    await context.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
    let audioSerial = 0
    await context.route(/\/x\/read-aloud\/speech(?:\/.*)?$/, route => route.fulfill({ json: route.request().url().endsWith('/lookup') ? { status: 'none' }
      : { id: String(++audioSerial).padStart(32, '0'), status: 'ready', duration_s: 90, url: '/x/read-aloud/speech/fixture/audio.mp3' } }))
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__panels = { panels, threads, sidePanel, goFiles, goApps, schedule };' }))
    await context.route('https://garden.example.com/**', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><style>:root{color-scheme:light dark}body{font:18px system-ui;padding:32px;background:#faf8f4;color:#35352b}button{padding:12px}@media(prefers-color-scheme:dark){body{background:#242722;color:#e4e7dc}}</style><h1>Garden</h1><p>A little room for ideas to grow.</p><button onclick="this.textContent=\'Growing\'">Plant an idea</button>' }))
    const page = await context.newPage(); page.setDefaultTimeout(10000)
    page.on('pageerror', error => { errors.push(error.message); console.error(error.stack) })
    const capture = async name => {
      await page.waitForFunction(() => [__panels.threads, __panels.sidePanel.threads].every(view => !view.st.open || (!view.st.open.loading && !view.st.open.refreshing)))
      // Keep captures comparable across the matrix; interaction assertions use
      // the live fixture events below, then the next capture returns to prose.
      await page.evaluate(() => {
        for (const view of [__panels.threads, __panels.sidePanel.threads]) if (view.st.open) view.st.open.messages = view.st.open.messages.filter(message => ['m1', 'm2', 'm2-scout', 'm3', 'm4'].includes(message.id))
        __panels.schedule()
      })
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      await page.waitForFunction(() => [...document.querySelectorAll('[data-prose-pending]')].every(node => { const r = node.getBoundingClientRect(), p = node.closest('.tt-stream')?.getBoundingClientRect(); return !p || r.bottom < p.top || r.top > p.bottom }))
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      if (out) await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}-${mode}.png`), animations: 'disabled' })
    }
    const main = page.locator('[data-panel=main]'), side = page.locator('[data-panel=side]')
    try {
      await page.goto(`${app.url}/threads/${a}`)
      await page.waitForFunction(() => globalThis.__panels)
      await page.locator('.tt-thread .composer [role=textbox]').waitFor()
      if (width === 390) {
        await page.evaluate(() => { __panels.threads.st.open.messages = __panels.threads.st.open.messages.filter(message => ['m1', 'm2', 'm2-scout', 'm3', 'm4'].includes(message.id)); __panels.schedule() })
        assert.equal(await page.locator('.row-side-action,.filelink-side').count(), 0)
        assert.equal(await side.count(), 0)
        await capture('phone-thread'); reports.push({ width, theme, mode, checks: ['Phone has one conversation and no side affordance'] }); continue
      }
      // A row's explicit side gesture opens a complete live thread.
      const offeredRow = page.locator('.tt-row[title]').filter({ hasText: (await app.f.api('GET', `/threads/${b}`)).thread.title })
      if (process.env.PANELS_TOUCH) {
        const hit = await offeredRow.locator('.row-side-action').boundingBox(), time = await offeredRow.locator('.srow-time').boundingBox()
        assert.ok(hit.width >= 40 && hit.height >= 40 && time.x + time.width <= hit.x, `touch side action has its own hit area beside the timestamp: ${JSON.stringify({ hit, time })}`)
        await offeredRow.locator('.row-side-action').click()
      } else await offeredRow.click({ modifiers: ['Alt'] })
      await side.locator('.tt-thread .composer [role=textbox]').waitFor()
      await page.waitForFunction(() => !__panels.sidePanel.threads.st.open.loading && !__panels.sidePanel.threads.st.open.refreshing)
      assert.equal(await page.evaluate(() => __panels.threads.st.open.thread.id), a)
      assert.equal(await page.evaluate(() => __panels.panels.st.side.id), b)
      assert.ok(new URL(page.url()).pathname.endsWith(`/threads/${a}`), 'opening side never changes the main address')
      assert.equal(await page.locator('.desk').evaluate(node => node.classList.contains('collapsed')), width < 1700)
      if (process.env.PANELS_REVIEW) {
        // A rail choice must expose its list in the same click. At 1920 the
        // automatic tuck does not apply, so also exercise a manually hidden list.
        if (width >= 1700) await page.locator('.listcol').getByTitle('Hide sidebar for a focused view').click()
        await page.locator('.rail-tabs [title="Files"]').click()
        await page.waitForFunction(() => __panels.sidePanel.host.state.screen === 'threads' && !document.querySelector('.desk').classList.contains('collapsed'))
        await page.locator('.listcol .fs-rootcard').first().waitFor()
        assert.equal(await page.locator('.listcol').getAttribute('inert'), null)
        assert.ok((await page.locator('.listcol').boundingBox()).width > 250)
        assert.equal(await page.evaluate(() => __panels.panels.st.side.id), b)
        await capture('review-rail-files')
        await page.locator('.listcol').getByTitle('Hide sidebar for a focused view').click()
        await page.locator('.rail-tabs [title="Chats"]').click()
        await page.waitForFunction(() => !document.querySelector('.desk').classList.contains('collapsed'))
        await page.locator('.listcol .tt-row').first().waitFor()
        assert.equal(await page.locator('.listcol').getAttribute('inert'), null)
        assert.equal(await page.evaluate(() => __panels.panels.st.side.id), b)
        await page.evaluate(a => __panels.threads.open(a), a)
        await main.locator('.tt-thread .composer [role=textbox]').waitFor()
        await page.waitForFunction(() => !__panels.threads.st.open.loading && !__panels.threads.st.open.refreshing)
        // Long answers let the author pass through the top of each scroller.
        const text = Array.from({ length: 16 }, (_, n) => `Paragraph ${n + 1}. Leave room for the reader to follow a thought. An author label should introduce these words without sitting on top of them.`).join('\n\n')
        for (const id of [a, b]) {
          const record = await app.f.api('GET', `/threads/${id}`)
          app.emit(id, { type: 'message.done', message: { id: 'm3', kind: 'message', author: record.thread.members[0].persona, text, at: new Date().toISOString() } })
        }
        await main.getByText('Paragraph 16.', { exact: false }).waitFor()
        await side.getByText('Paragraph 16.', { exact: false }).waitFor()
        await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
        const geometry = [], starts = []
        for (const pane of [main, side]) {
          const stream = pane.locator('.tt-stream')
          const start = await stream.evaluate(node => {
            const run = node.querySelector('[data-message-id="m3"]').closest('.tt-author-run')
            return node.scrollTop + run.getBoundingClientRect().top - node.getBoundingClientRect().top - 22
          })
          starts.push([stream, start])
          for (const offset of [0, 64, 140]) {
            await stream.evaluate((node, top) => { node.scrollTop = top }, start + offset)
            await page.evaluate(() => new Promise(requestAnimationFrame))
            const checked = await stream.evaluate(node => {
              const run = node.querySelector('[data-message-id="m3"]').closest('.tt-author-run'), speaker = run.querySelector('.tt-speaker'), pill = speaker.querySelector('.tt-author-pill').getBoundingClientRect()
              const surface = node.getBoundingClientRect(), paneWidth = node.closest('[data-panel]').getBoundingClientRect().width
              const overlap = [...node.querySelectorAll('.md p')].some(prose => {
                const range = document.createRange(); range.selectNodeContents(prose)
                return [...range.getClientRects()].some(r => r.bottom > surface.top && r.top < surface.bottom && pill.bottom > Math.max(r.top, surface.top) && pill.top < Math.min(r.bottom, surface.bottom) && pill.left < r.right && pill.right > r.left)
              })
              return { paneWidth, position: getComputedStyle(speaker).position, overlap }
            })
            if (checked.paneWidth <= 700) { assert.equal(checked.position, 'static'); assert.equal(checked.overlap, false, 'a narrow author must not cover visible prose') }
            geometry.push(checked)
          }
        }
        await capture('review-scrolled-authors')
        for (const [stream, start] of starts) await stream.evaluate((node, top) => { node.scrollTop = top }, start)
        await capture('review-authors')
        await page.evaluate(() => __panels.panels.openSide({ kind: 'file', root: 'vault', path: 'Projects/App/engineer/role.md' }))
        await side.locator('.fs-preview').waitFor()
        assert.equal(await side.locator('.side-heading strong').innerText(), 'role.md')
        assert.equal(await side.locator('.side-heading > span').innerText(), 'Vault / Projects / App / engineer')
        const promotion = side.getByRole('button', { name: 'Open as the main panel', exact: true })
        const mark = await promotion.locator('svg').innerHTML()
        assert.notEqual(mark, await side.getByRole('button', { name: 'Swap the two panels', exact: true }).locator('svg').innerHTML())
        assert.notEqual(mark, await page.locator('.listcol').getByTitle('Hide sidebar for a focused view').locator('svg').innerHTML())
        await capture('review-file')
        await promotion.click()
        await side.waitFor({ state: 'detached' }); await main.locator('.fs-preview').waitFor()
        reports.push({ width, theme, mode, geometry, checks: ['Rail Files and Chats reveal their list in one click, retaining side', 'Promotion has its own arrow and moves the file to main', 'File location is folder breadcrumbs without its filename', 'Narrow authors stay in flow and do not overlap prose at three scroll positions'] })
        continue
      }
      await capture('thread-thread')
      await side.locator('.speakbtn').last().click()
      await side.locator('.speakmenu [role=menuitem]').first().click()
      await page.waitForFunction(() => __audio.some(audio => !audio.paused))
      await main.locator('.speakbtn').last().click()
      await main.locator('.speakmenu [role=menuitem]').first().click()
      await page.waitForFunction(() => __audio.length === 2 && __audio.filter(audio => !audio.paused).length === 1)
      assert.equal(await page.locator('.playerbar-slot').count() <= 1, true)
      await page.evaluate(() => __audio.forEach(audio => audio.pause()))
      // Mention keyboard handling, dictation, model selection and uploads are
      // all exercised through the second mount's actual composer.
      await side.getByRole('button', { name: 'Mention an agent', exact: true }).click()
      await side.locator('.tt-mentions').waitFor()
      await side.locator('.composer [role=textbox]').press('Enter')
      assert.match(await side.locator('.composer [role=textbox]').innerText(), /@/)
      assert.equal(await main.locator('.composer [role=textbox]').innerText(), '')
      await side.locator('.composer [role=textbox]').fill('')
      await side.getByRole('button', { name: 'Dictate', exact: true }).click()
      await side.locator('.take').waitFor()
      await main.getByRole('button', { name: 'Dictate', exact: true }).click()
      await main.locator('.take').waitFor()
      await side.getByRole('button', { name: 'Keep', exact: true }).press('Escape')
      await side.locator('.take').waitFor({ state: 'detached' })
      assert.equal(await main.locator('.take').count(), 1, 'side Escape leaves the main capture active')
      await side.getByRole('button', { name: 'Dictate', exact: true }).click()
      await side.locator('.take').waitFor()
      await page.evaluate(() => {
        const result = Object.assign([{ transcript: 'Words dictated beside the thread', confidence: 1 }], { isFinal: true })
        __recognisers.at(-1).dispatchEvent(Object.assign(new Event('result'), { resultIndex: 0, results: [result] }))
      })
      await side.getByRole('button', { name: 'Keep', exact: true }).click()
      assert.equal(await side.locator('.composer [role=textbox]').innerText(), 'Words dictated beside the thread')
      assert.equal(await main.locator('.composer [role=textbox]').innerText(), '')
      assert.equal(await main.locator('.take').count(), 1)
      await main.getByRole('button', { name: 'Keep', exact: true }).press('Escape')
      await main.locator('.take').waitFor({ state: 'detached' })
      assert.equal(await page.evaluate(() => ['rec', 'dictation', 'speech', 'termsNote'].some(key => key in __panels.sidePanel.host.state)), false, 'side audio state stays in its plugins')
      await side.locator('.composer [role=textbox]').fill('')
      await side.getByRole('button', { name: 'Providers & models', exact: true }).click()
      await side.locator('.tt-configuration').getByRole('button', { name: 'Model for Quill', exact: true }).click()
      const model = await page.evaluate(() => __panels.sidePanel.threads.st.open.thread.members.find(m => m.persona === 'quill').model === 'deep' ? 'Swift' : 'Deep')
      await side.locator('.tt-configuration').getByRole('option', { name: model, exact: true }).click()
      await page.waitForFunction(() => !__panels.sidePanel.threads.st.open.patching)
      await page.keyboard.press('Escape'); await side.locator('.tt-configuration').waitFor({ state: 'detached' })
      await side.getByRole('button', { name: 'Thread actions', exact: true }).click()
      await side.locator('.tt-header-menu').getByRole('button', { name: 'Board', exact: true }).click()
      await side.locator('.tt-board').waitFor(); await side.getByRole('button', { name: 'Close board', exact: true }).click()
      await side.locator('.tt-head .agentbtn').click(); await side.locator('.tt-details').waitFor()
      const closeBox = await side.getByRole('button', { name: 'Close side panel', exact: true }).boundingBox(), sideBox = await side.boundingBox()
      assert.ok(closeBox.x >= sideBox.x && closeBox.x + closeBox.width <= sideBox.x + sideBox.width, 'side details keep Close within the pane')
      await side.locator('.tt-details-head .backbtn').click()
      await side.locator('.composer input[type=file]').first().setInputFiles({ name: 'side-note.txt', mimeType: 'text/plain', buffer: Buffer.from('Side notes.') })
      await page.waitForFunction(() => __panels.sidePanel.host.state.attach.items[0]?.path === 'attachments/side-note.txt')
      // Both inputs send to their own endpoint and their own stream only.
      await main.locator('.composer [role=textbox]').fill(`Main panel ${theme} ${mode}`)
      await side.locator('.composer [role=textbox]').fill(`Side panel ${theme} ${mode}`)
      await side.getByRole('button', { name: 'Swap the two panels', exact: true }).click()
      await page.waitForFunction(({ a, b }) => __panels.threads.st.open.thread.id === b && __panels.sidePanel.threads.st.open.thread.id === a, { a, b })
      assert.equal(await main.locator('.composer [role=textbox]').innerText(), `Side panel ${theme} ${mode}`)
      assert.equal(await side.locator('.composer [role=textbox]').innerText(), `Main panel ${theme} ${mode}`)
      await side.getByRole('button', { name: 'Swap the two panels', exact: true }).click()
      await page.waitForFunction(({ a, b }) => __panels.threads.st.open.thread.id === a && __panels.sidePanel.threads.st.open.thread.id === b && !__panels.threads.st.open.refreshing && !__panels.sidePanel.threads.st.open.refreshing, { a, b })
      await main.locator('.composer .roundsend').click()
      await side.locator('.composer .roundsend').click()
      await page.waitForFunction(() => !__panels.threads.st.open.sending && !__panels.sidePanel.threads.st.open.sending)
      assert.equal(posts.at(-2).id, a); assert.equal(posts.at(-1).id, b)
      assert.deepEqual(posts.at(-1).body.attachments, ['attachments/side-note.txt'])
      app.emit(a, { type: 'message.done', message: { id: `only-main-${theme}-${mode}`, author: 'sprout', kind: 'message', at: new Date().toISOString(), text: 'Only the main stream sees this sentence.' } })
      app.emit(b, { type: 'message.done', message: { id: `only-side-${theme}-${mode}`, author: 'quill', kind: 'message', at: new Date().toISOString(), text: 'Only the side stream sees this sentence.' } })
      await main.getByText('Only the main stream sees this sentence.', { exact: true }).waitFor()
      await side.getByText('Only the side stream sees this sentence.', { exact: true }).waitFor()
      await until(() => app.broker.seen.some(request => request.method === 'PATCH' && request.path === `/v1/threads/${b}` && JSON.parse(request.body).seen))
      assert.equal(await main.getByText('Only the side stream sees this sentence.', { exact: true }).count(), 0)
      assert.equal(await side.getByText('Only the main stream sees this sentence.', { exact: true }).count(), 0)
      await page.waitForFunction(() => !__panels.threads.st.open.typing.size && !__panels.sidePanel.threads.st.open.typing.size && !__panels.sidePanel.threads.st.open.marking)
      // The width transition suspends the side surface, retaining its item.
      await page.setViewportSize({ width: 1179, height: 1000 }); await side.waitFor({ state: 'detached' })
      assert.equal(await page.locator('.row-side-action,.filelink-side').count(), 0)
      assert.equal(await page.evaluate(() => __panels.threads.st.open.thread.id), a)
      const readCount = () => app.broker.seen.filter(request => request.method === 'PATCH' && request.path === `/v1/threads/${b}` && JSON.parse(request.body).seen).length
      const beforeHidden = readCount()
      app.emit(b, { type: 'message.done', message: { id: 'while-hidden', author: 'quill', kind: 'message', text: 'This arrived while the side was hidden.', at: new Date().toISOString() } })
      await page.waitForFunction(() => __panels.sidePanel.threads.st.open.messages.some(message => message.id === 'while-hidden'))
      assert.equal(readCount(), beforeHidden, 'a hidden side must not mark new messages read')
      await page.locator('.tt-row').filter({ hasText: 'General' }).click({ modifiers: ['Alt'] })
      await page.waitForFunction(() => __panels.threads.st.open.thread.id === 'general')
      await page.evaluate(a => __panels.threads.open(a), a)
      await page.setViewportSize({ width, height: 1000 }); await side.waitFor()
      await until(() => readCount() > beforeHidden)
      if (width < 1700) await main.getByRole('button', { name: 'Show sidebar', exact: true }).click()
      await page.waitForFunction(() => !document.querySelector('.desk').classList.contains('collapsed'))
      assert.equal(await page.locator('.desk').evaluate(node => node.classList.contains('collapsed')), false)
      // A plain row click keeps the side, including after explicitly showing the list.
      await page.locator('.tt-row').filter({ hasText: 'General' }).click()
      await page.waitForFunction(() => __panels.threads.st.open.thread.id === 'general')
      assert.equal(await page.evaluate(() => __panels.panels.st.side.id), b)
      await page.goBack(); await page.waitForFunction(a => __panels.threads.st.open.thread.id === a, a)
      assert.equal(await page.evaluate(() => __panels.panels.st.side.id), b)
      await page.goForward(); await page.waitForFunction(() => __panels.threads.st.open.thread.id === 'general')
      // Selecting the side item in the main slot moves it and closes side.
      await page.evaluate(b => __panels.threads.open(b), b)
      await side.waitFor({ state: 'detached' })
      assert.equal(await page.evaluate(() => __panels.threads.st.open.thread.id), b)
      await page.evaluate(a => __panels.threads.open(a), a)
      await page.evaluate(b => __panels.panels.openSide({ kind: 'thread', id: b }), b)
      await side.locator('.tt-thread').waitFor()
      await page.reload(); await side.locator('.tt-thread').waitFor()
      await page.waitForFunction(() => globalThis.__panels)
      await main.locator('.tt-thread').waitFor()
      assert.equal(await page.evaluate(() => __panels.threads.st.open.thread.id), a)
      assert.equal(await page.evaluate(() => __panels.sidePanel.threads.st.open.thread.id), b)
      await side.getByRole('button', { name: 'Open as the main panel', exact: true }).click()
      await side.waitFor({ state: 'detached' })
      assert.equal(await page.evaluate(() => __panels.threads.st.open.thread.id), b)
      await page.evaluate(a => __panels.threads.open(a), a)
      // Files use the real viewer and editor mount, with in-panel navigation.
      await page.evaluate(() => __panels.panels.openSide({ kind: 'file', root: 'vault', path: 'Notes/plan.md' }))
      await side.locator('.fs-preview').waitFor(); await capture('thread-file')
      await side.getByRole('button', { name: 'Read aloud', exact: true }).click()
      await page.locator('.sheet.file-speech').waitFor()
      assert.match(await page.locator('.sheet.file-speech').innerText(), /plan\.md/)
      assert.equal(await page.evaluate(() => __panels.sidePanel.host.state.sheet?.kind ?? 'main owns the shared speech sheet'), 'main owns the shared speech sheet')
      await page.keyboard.press('Escape'); await page.locator('.sheet.file-speech').waitFor({ state: 'detached' })
      const headingGeometry = await side.locator('.side-head').evaluate(node => ({ box: node.getBoundingClientRect().toJSON(), children: [...node.children].map(n => ({ class: n.className, box: n.getBoundingClientRect().toJSON(), position: getComputedStyle(n).position })) }))
      assert.ok(headingGeometry.children.every(child => child.box.top >= headingGeometry.box.top && child.box.bottom <= headingGeometry.box.bottom), JSON.stringify(headingGeometry))
      await side.locator('.filelink').filter({ hasText: 'plan#Next steps' }).click()
      await page.waitForFunction(() => __panels.sidePanel.host.files.st.file?.landed === 'next-steps')
      assert.equal(await page.evaluate(() => __panels.threads.st.open.thread.id), a)
      await side.getByRole('button', { name: 'Edit', exact: true }).first().click()
      await side.locator('.fs-field').fill('# A plan beside the conversation\n\nAn edit made in the side panel.')
      await side.locator('.fs-field').press('Control+s')
      await page.waitForFunction(() => !__panels.sidePanel.host.files.dirty())
      await page.evaluate(() => __panels.panels.openSide({ kind: 'file', root: 'vault', path: 'Notes/page.html' }))
      await side.locator('.fs-iframe').contentFrame().locator('body[data-live=yes]').waitFor()
      await side.locator('.fs-iframe').contentFrame().getByRole('button', { name: 'Plant a thought' }).click()
      await side.locator('.fs-iframe').contentFrame().getByRole('button', { name: 'Growing' }).waitFor()
      const frameGeometry = await side.locator('.fs-iframe').evaluate(node => ({ frame: node.getBoundingClientRect().toJSON(), pane: node.closest('.pane').getBoundingClientRect().toJSON() }))
      assert.ok(frameGeometry.frame.height > 700); assert.equal(frameGeometry.frame.bottom, frameGeometry.pane.bottom)
      // Split is keyboard and pointer accessible and restored per device.
      const settledSplit = value => page.waitForFunction(value => {
        const pair = document.querySelector('.panel-pair').getBoundingClientRect(), bar = document.querySelector('.panel-divider').getBoundingClientRect()
        return Math.abs(__panels.panels.st.split - value) < 1 && Math.abs(bar.left + bar.width / 2 - pair.left - pair.width * value / 100) < 1
      }, value)
      if (width === 1440 && theme === 'latte' && mode === 'light') await page.emulateMedia({ reducedMotion: 'no-preference' })
      await page.getByRole('separator', { name: 'Resize panels' }).focus(); await page.keyboard.press('ArrowLeft')
      assert.equal(await page.evaluate(() => __panels.panels.st.split), 54)
      await settledSplit(54)
      await page.getByRole('separator', { name: 'Resize panels' }).dblclick()
      await settledSplit(56)
      const dividerBox = await page.getByRole('separator', { name: 'Resize panels' }).boundingBox(), pairBox = await page.locator('.panel-pair').boundingBox()
      await page.mouse.move(dividerBox.x + dividerBox.width / 2, dividerBox.y + dividerBox.height / 2); await page.mouse.down()
      await page.mouse.move(pairBox.x + pairBox.width * .48, dividerBox.y + dividerBox.height / 2, { steps: 5 })
      await page.waitForFunction(() => Math.abs(__panels.panels.st.split - 48) < 1)
      await settledSplit(48)
      assert.ok(await main.evaluate(node => parseFloat(getComputedStyle(node).transitionDuration) < .001), 'pointer dragging must follow without a motion delay')
      await page.mouse.move(pairBox.x + pairBox.width * .60, dividerBox.y + dividerBox.height / 2, { steps: 5 })
      await page.waitForFunction(() => Math.abs(__panels.panels.st.split - 60) < 1)
      await settledSplit(60)
      await page.mouse.up(); await page.getByRole('separator', { name: 'Resize panels' }).dblclick()
      await settledSplit(56)
      await page.emulateMedia({ reducedMotion: 'reduce' })
      // Files rows and their existing menu use exactly the same destination
      // rule as a thread row, including the duplicate-item swap.
      await page.evaluate(() => __panels.goFiles('vault', 'Notes', { isFile: false }))
      if (width < 1700) await main.getByRole('button', { name: 'Show sidebar', exact: true }).click()
      await page.locator('.listcol .fs-entry').filter({ hasText: 'plan.md' }).getByRole('button', { name: 'Open to the side', exact: true }).click()
      await side.locator('.fs-preview').waitFor()
      assert.equal(await main.locator('.fs-file').count(), 0)
      await page.locator('.listcol .fs-entry').filter({ hasText: 'page.html' }).locator('.setrow').click()
      await main.locator('.fs-iframe').waitFor()
      assert.equal(await page.evaluate(() => __panels.sidePanel.host.files.st.file.path), 'Notes/plan.md')
      await main.getByRole('button', { name: 'This file', exact: true }).click()
      await page.locator('.fs-sheet').getByRole('button', { name: 'Open to the side', exact: true }).click()
      await page.waitForFunction(() => __panels.sidePanel.host.files.st.file?.path === 'Notes/page.html')
      await main.locator('.fs-preview').waitFor()
      assert.equal(await page.evaluate(() => __panels.sidePanel.host.panels.st.side.path), 'Notes/page.html')
      assert.equal(await main.locator('.fs-preview').count(), 1)
      await page.goto(`${app.url}/files/vault/Notes/page.html`); await page.waitForFunction(() => globalThis.__panels)
      await main.locator('.fs-iframe').waitFor(); assert.equal(await side.count(), 0, 'a cold link to the held side file moves it into main')
      await main.locator('.fs-iframe').contentFrame().getByRole('button', { name: 'Plant a thought' }).click()
      await page.evaluate(b => __panels.panels.openSide({ kind: 'thread', id: b }), b)
      await side.locator('.tt-thread').waitFor()
      await main.locator('.fs-iframe').contentFrame().getByRole('button', { name: 'Growing', exact: true }).waitFor()
      await side.getByRole('button', { name: 'Close side panel', exact: true }).click(); await side.waitFor({ state: 'detached' })
      await main.locator('.fs-iframe').contentFrame().getByRole('button', { name: 'Growing', exact: true }).waitFor()
      await page.evaluate(() => __panels.goApps())
      await page.locator('.apps-row [aria-label="Open Garden to the side"]').click()
      assert.equal(await page.evaluate(() => __panels.panels.st.side.kind), 'app')
      await page.evaluate(a => __panels.threads.open(a), a)
      await side.locator('.apps-embed').waitFor()
      await side.locator('.apps-embed').contentFrame().getByRole('button', { name: 'Plant an idea' }).click()
      await side.locator('.apps-embed').contentFrame().getByRole('button', { name: 'Growing' }).waitFor()
      await capture('thread-app')
      await page.evaluate(() => __panels.goFiles('vault', 'Notes', { isFile: false }))
      await main.locator('.tt-thread').waitFor()
      assert.equal(await page.evaluate(() => __panels.threads.st.open.thread.id), a, 'browsing a folder only changes the navigator')
      await side.locator('.apps-embed').contentFrame().getByRole('button', { name: 'Growing', exact: true }).waitFor()
      await page.evaluate(a => __panels.threads.open(a), a)
      await side.locator('.apps-embed').contentFrame().getByRole('button', { name: 'Growing', exact: true }).waitFor()
      await page.evaluate(() => __panels.panels.split(61))
      await page.reload(); await page.waitForFunction(() => globalThis.__panels)
      await side.locator('.apps-embed').waitFor()
      assert.equal(await page.evaluate(() => __panels.panels.st.split), 61)
      assert.equal(await page.evaluate(() => __panels.threads.st.open.thread.id), a)
      if (width === 1440) {
        await page.evaluate(() => { document.documentElement.style.setProperty('--ui-scale', '1.25'); __panels.schedule() })
        await side.waitFor({ state: 'detached' }); assert.equal(await page.locator('.row-side-action,.row-side-mark,.filelink-side').count(), 0)
        await page.evaluate(() => { document.documentElement.style.setProperty('--ui-scale', '1'); __panels.schedule() })
        await side.waitFor()
      }
      await side.getByRole('button', { name: 'Open as the main panel', exact: true }).click()
      await main.locator('.apps-embed').waitFor()
      await page.evaluate(b => __panels.panels.openSide({ kind: 'thread', id: b }), b)
      await side.locator('.tt-thread').waitFor()
      await page.reload(); await page.waitForFunction(() => globalThis.__panels)
      await main.locator('.apps-embed').waitFor(); await side.locator('.tt-thread').waitFor()
      assert.equal(await page.evaluate(() => __panels.sidePanel.threads.st.open.thread.id), b)
      if (width === 1440 && theme === 'latte' && mode === 'light') await page.emulateMedia({ reducedMotion: 'no-preference' })
      await side.getByRole('button', { name: 'Close side panel', exact: true }).click()
      await side.waitFor({ state: 'detached' })
      assert.equal(await page.locator('.desk').evaluate(node => node.classList.contains('collapsed')), false)
      reports.push({ width, theme, mode, checks: ['Side gesture and app row icon', 'Files row side icon, plain main click and menu swap', 'Independent stream routing', 'Independent composer routing', 'Independent plugin-owned dictation and scoped Escape', 'Side mentions, dictation, models, Board and attachment', 'Shared audio playback and side file reading', 'Swap transfers both drafts and attachments', 'Side thread read mark', 'Narrow and widen retains side', 'No Alt-side behaviour below 1180', 'Sidebar tuck and explicit show', 'Plain rows drive main', 'Back and Forward change only main', 'No duplicate item and Promote', 'Reload restores thread, app and split in either slot', 'File edit and save; side links and fragments stay in side', 'Live HTML fills pane and runs scripts', 'Iframe script state survives main navigation and opening/closing side', 'Pointer and keyboard resizing, reset', 'Live app scripts', 'UI scale applied to threshold', 'Sidebar restores on close; reduced and ordinary motion'] })
    } finally { await context.close() }
  }
  assert.deepEqual(errors, [])
  if (out) await writeFile(path.join(out, 'browser.json'), JSON.stringify({ reports, errors }, null, 2) + '\n')
  console.log(JSON.stringify({ result: 'passed', reports, errors }, null, 2))
} finally { await browser.close(); await app?.close(); await shut(content) }
