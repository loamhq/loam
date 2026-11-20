// Exercise the real page, Files editor, and Comments service over disposable data.
// Set LOAM_PLAYWRIGHT and, optionally, LOAM_CHROME.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { spawn } from 'node:child_process'
import { readdir, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { oneProductFixture } from './one-product-fixture.mjs'
import { row } from './fixtures/files-contract.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const plugin = new URL('../../plugins/doc-comments/', import.meta.url)
const server = spawn('python3', [fileURLToPath(new URL('test/dev_server.py', plugin)), '0'], { stdio: ['ignore', 'pipe', 'inherit'] })
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const app = await oneProductFixture()
try {
  const base = await new Promise((resolve, reject) => {
    let output = ''
    server.stdout.on('data', chunk => {
      output += chunk
      const match = /doc-comments dev: (http:\/\/127\.0\.0\.1:\d+)/.exec(output)
      if (match) resolve(match[1])
    })
    server.once('exit', code => reject(new Error('Comments fixture exited ' + code)))
  })
  const boardPath = '00_INBOX/from-agents/comms.md'
  const source = '---\r\nlast_modified_by: "[[Sam]]"\r\n---\r\n# Board\r\n\r\n## Ideas\n- 2026-11-14 · Read **the draft** <!-- Sam · until 2027-01-15 -->\r\n- [X] 2026-11-19 · **quill** · Send the notes <!-- new · custom -->\r\n'
  const seed = async text => {
    const result = await fetch(base + '/dev/outside', { method: 'POST', body: JSON.stringify({ path: boardPath, text }) })
    assert.equal(result.status, 204)
  }
  const disk = async () => (await (await fetch(base + '/dev/file?' + new URLSearchParams({ path: boardPath }))).json()).text
  const revision = text => 'sha256:' + createHash('sha256').update(text).digest('hex')
  const boardRow = row('vault', boardPath, { capabilities: { read: true, write: true, download: true } })
  const json = (res, body) => { res.setHeader('content-type', 'application/json'); res.end(JSON.stringify(body)) }
  app.routes['GET /v1/plugins'] = (_, res) => json(res, { plugins: [{
    id: 'doc-comments', name: 'Comments', version: '1.0.0', layer: 'shipped', api: 0,
    kind: ['extension'], places: ['files.viewer', 'files.root'], overrides: [],
    effective: true, on: true, switch: 'plugin', has: { page_main: true },
  }] })
  for (const rel of await readdir(plugin, { recursive: true })) {
    if (!/^(page|assets)\/.*\.(js|css)$/.test(rel)) continue
    app.routes['GET /v1/plugins/doc-comments/files/' + rel] = async (_, res) => {
      res.setHeader('content-type', rel.endsWith('.css') ? 'text/css' : 'text/javascript')
      res.end(await readFile(new URL(rel, plugin)))
    }
  }
  app.routes['GET /v1/files/stat'] = (_, res) => json(res, boardRow)
  app.routes['GET /v1/files/read'] = async (_, res) => {
    const text = await disk()
    json(res, { ...boardRow, text, revision: revision(text) })
  }
  const open = async page => {
    await page.goto(app.url + '/threads/general')
    await page.getByRole('button', { name: 'Board', exact: true }).click()
    const board = page.getByRole('complementary', { name: 'Thread board' })
    await board.getByRole('button', { name: 'Add a note', exact: true }).first().waitFor()
    assert.equal(await board.getByRole('button', { name: 'Doc', exact: true }).count(), 0)
    await board.getByRole('button', { name: 'Open in Files', exact: true }).click()
  }
  for (const width of [390, 1440]) {
    await app.f.api('PUT', '/threads/general/board', { markdown: source })
    await seed(source)
    const phone = width === 390
    const context = await browser.newContext({ viewport: { width, height: 844 }, isMobile: phone, hasTouch: phone })
    try {
      let beforeSave
      await context.route('**/x/doc-comments/**', async route => {
        const req = route.request(), url = new URL(req.url())
        if (url.pathname.endsWith('/config')) return route.fulfill({ json: { reach: 'everywhere' } })
        if (url.pathname.endsWith('/doc/save') && beforeSave) {
          const text = beforeSave
          beforeSave = null
          await seed(text)
        }
        const result = await fetch(base + url.pathname + url.search, {
          method: req.method(), headers: { 'content-type': 'application/json' },
          body: req.method() === 'GET' ? undefined : req.postData(),
        })
        await route.fulfill({ status: result.status, contentType: result.headers.get('content-type') || 'application/json', body: Buffer.from(await result.arrayBuffer()) })
      })
      const page = await context.newPage()
      const errors = []
      page.on('pageerror', e => errors.push(e.message))
      page.setDefaultTimeout(10000)
      await open(page)
      const inPanel = page.locator(phone ? '.files.screen' : '[data-panel="side"]')
      const editor = inPanel.locator('.cm-content')
      await editor.waitFor().catch(async error => {
        console.error('Files did not mount the Comments editor:', page.url(), errors, (await page.locator('body').innerText()).slice(0, 1500))
        throw error
      })
      assert.doesNotMatch(await editor.innerText(), /2026-11|<!--|last_modified_by|until 2027/)
      assert.match(await editor.innerText(), /Read the draft/)
      if (phone) {
        assert.match(page.url(), /\/files\/vault\//)
      } else {
        assert.match(page.url(), /\/threads\/general\?side=/)
        await page.locator('[data-panel="main"] .tt-composer').waitFor()
        assert.equal(await page.locator('[data-panel="main"] .tt-conversation[inert]').count(), 0)
      }
      const edit = async (line, words) => {
        await editor.locator('.cm-line').filter({ hasText: line }).click()
        await page.keyboard.press('End')
        const reply = page.waitForResponse(r => r.url().endsWith('/doc/save') && r.ok())
        await page.keyboard.insertText(words)
        return (await reply).json()
      }
      await edit('Ideas', ' revised')
      const saved = source.replace('## Ideas', '## Ideas revised')
      assert.equal(await disk(), saved, 'existing board metadata is byte-intact after a Files save')
      const agentLine = '- 2026-11-20 · Agent contribution <!-- Quill · until 2027-03-01 -->\r\n'
      beforeSave = saved + agentLine
      const result = await edit('Send the notes', ' tomorrow')
      assert.equal(result.merged_with_others, true)
      assert.equal(await disk(), saved.replace('Send the notes', 'Send the notes tomorrow') + agentLine)
      if (phone) {
        const back = inPanel.getByRole('button', { name: 'Back to board', exact: true })
        assert.equal(await inPanel.locator('.dc-back,.panel-history-back').count(), 1)
        assert.match(await back.getAttribute('aria-keyshortcuts'), /^(Control|Meta)\+\[$/)
        await back.click()
        await page.getByRole('complementary', { name: 'Thread board' }).getByRole('button', { name: 'Open in Files' }).waitFor()
        await page.goForward()
        await editor.waitFor()
        await back.waitFor()
        await page.reload()
        await back.waitFor()
        assert.equal(await inPanel.locator('.dc-back,.panel-history-back').count(), 1)
        await page.goBack()
        await page.getByRole('complementary', { name: 'Thread board' }).getByRole('button', { name: 'Open in Files' }).waitFor()
      } else {
        await editor.evaluate(node => { window.boardEditor = node })
        await page.goBack()
        await page.locator('[data-panel="main"] .tt-board').waitFor()
        assert.equal(await editor.evaluate(node => node === window.boardEditor), true)
        await page.goForward()
        await page.locator('[data-panel="main"] .tt-board').waitFor({ state: 'detached' })
        assert.equal(await editor.evaluate(node => node === window.boardEditor), true)
        await inPanel.getByRole('button', { name: 'Open as the main panel' }).click()
        await page.locator('[data-panel="main"] .cm-content').waitFor()
        assert.match(page.url(), /\/files\/vault\//)
      }
      assert.deepEqual(errors, [])
      console.log(`${width}px: Files navigation, hidden metadata, byte-exact save, concurrent agent edit and ${phone ? 'both Back controls' : 'main panel promotion'} passed.`)
    } finally { await context.close() }
  }
} finally {
  await browser.close()
  await app.close()
  server.kill()
}
