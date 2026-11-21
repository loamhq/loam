// Run the real page and relay against a sample broker, with no provider session.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_BRANCH_EVIDENCE
if (!out) throw new Error('Set THREAD_BRANCH_EVIDENCE')
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const results = []
try {
  for (const [width, height] of [[390, 844], [1440, 900]]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), source = 'a-quieter-week'
    const context = await browser.newContext({ viewport: { width, height }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    const page = await context.newPage(), errors = [], shots = []
    page.setDefaultTimeout(12000)
    page.on('pageerror', error => errors.push(error.message))
    const shot = async (name) => {
      await page.evaluate(async () => {
        await document.fonts.ready
        await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame)
      })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      const file = `${name}-${width}-${theme}.png`
      await page.screenshot({ path: path.join(out, file), animations: 'disabled' })
      shots.push(file)
    }
    const draft = page.locator('#draft')
    const send = page.locator('.tt-composer button[type=submit]')
    const dialog = page.getByRole('dialog', { name: 'New thread with', exact: true })
    const row = page.locator('.tt-stream [data-message-id="m3"]')
    const branch = row.getByRole('button', { name: 'Branch into a new thread' })
    const opened = () => app.broker.seen.filter(({ path, method }) => method === 'POST' && path.endsWith('/branch'))
    const choose = async (persona) => {
      await branch.click()
      await dialog.waitFor()
      assert.equal(await dialog.count(), 1)
      assert.equal(await dialog.getAttribute('data-menu-layout'), width === 390 ? 'sheet' : 'anchored')
      const person = dialog.getByRole('button', { name: persona, exact: true })
      await person.click()
      await page.waitForURL(/\/threads\/new-\d+$/)
      await dialog.waitFor({ state: 'hidden' })
      await draft.waitFor()
    }
    try {
      await page.goto(`${app.url}/threads/${source}`)
      await branch.waitFor()
      await draft.fill('A note to keep in the source.')
      await branch.click()
      await dialog.waitFor()
      await shot('picker')
      await dialog.locator(width === 390 ? '.sheet-x' : '.surface-close').click()
      assert.equal(opened().length, 0)
      await choose('Quill')
      const branchUrl = page.url()
      assert.equal(opened().length, 1)
      assert.deepEqual(JSON.parse(opened()[0].body), { message: 'm3', persona: 'quill' })
      const chip = page.locator('.tt-reply-chip')
      await chip.getByText('A little room for the week', { exact: false }).waitFor()
      assert.equal(await draft.evaluate(node => node.value), '')
      assert.equal(await send.isDisabled(), true)
      await draft.fill('   ')
      assert.equal(await send.isDisabled(), true)
      await draft.fill('')
      await shot('empty-composer')
      if (width === 390) {
        assert.equal(await page.locator('nav:visible').count(), 0)
        // Headless Chromium has no software keyboard. Drive its visual viewport
        // event with an on-screen keyboard's geometry and inspect the real dock.
        const dock = await page.evaluate(() => {
          const visual = window.visualViewport
          const real = visual.height
          Object.defineProperty(visual, 'height', { configurable: true, value: real - 280 })
          visual.dispatchEvent(new Event('resize'))
          const app = document.querySelector('#app')
          const box = document.querySelector('.tt-composer').getBoundingClientRect()
          const result = { keyboard: app.dataset.keyboard, composer: box.bottom, visible: visual.height }
          delete visual.height
          visual.dispatchEvent(new Event('resize'))
          return result
        })
        assert.equal(dock.keyboard, 'open')
        assert.ok(dock.composer <= dock.visible + 1, JSON.stringify(dock))
      }
      const opening = 'What should get our best attention?'
      await draft.fill(opening)
      await page.locator('.tt-composer button[type=submit]:enabled').waitFor()
      await shot('typed')
      await send.click()
      const quoted = page.locator('.tt-bubble .tt-quote')
      await quoted.waitFor()
      const answer = page.getByText(/Picking up from .* keep the rest of the plan flexible\./)
      await answer.waitFor()
      await page.locator('.tt-title').filter({ hasText: opening }).waitFor()
      assert.match(await quoted.innerText(), /A little room for the week/)
      assert.match(await quoted.innerText(), /Let’s leave a little room for the unexpected/)
      assert.match(await answer.innerText(), /Let’s leave a little room for the unexpected/)
      assert.equal(await quoted.evaluate(node => getComputedStyle(node).textDecorationLine), 'none')
      const sent = app.broker.seen.find(({ method, path }) => method === 'PUT' && path.endsWith('/messages'))
      assert.deepEqual(Object.fromEntries(Object.entries(JSON.parse(sent.body)).filter(([key]) => key !== 'submission_id')),
        { text: opening, reply_to: 'm3', reply_thread: source })
      await shot('new-thread')
      await page.reload()
      await quoted.waitFor()
      await quoted.click()
      await page.waitForURL(`**/threads/${source}?message=m3`)
      await page.locator('[data-message-id="m3"].tt-reply-highlight').waitFor()
      assert.equal(await draft.evaluate(node => node.value), '', 'reload clears page-local source text')
      await shot('source-highlight')
      await page.goBack()
      assert.equal(page.url(), branchUrl)
      await quoted.waitFor()
      await quoted.click()
      const marker = row.getByRole('link', { name: `branched → ${opening}`, exact: true })
      await marker.waitFor()
      await shot('source-marker')
      await marker.click()
      await page.waitForURL(branchUrl)
      await quoted.waitFor()

      const branchId = new URL(branchUrl).pathname.split('/').at(-1)
      const renamed = 'Give the week some room'
      await app.f.api('PATCH', `/threads/${branchId}`, { title: renamed })
      await quoted.click()
      await row.getByRole('link', { name: `branched → ${renamed}`, exact: true }).waitFor()
      await choose('Sprout')
      await page.getByRole('button', { name: 'Remove quote' }).click()
      await chip.waitFor({ state: 'hidden' })
      await shot('quote-removed')
      const plain = 'This is a separate thought.'
      await draft.fill(plain); await send.click()
      await page.getByText(plain, { exact: true }).first().waitFor()
      assert.equal(await page.locator('.tt-bubble .tt-quote').count(), 0)
      await shot('plain-thread')

      await app.f.api('DELETE', `/threads/${branchId}`)
      await page.goto(`${app.url}/threads/${source}`)
      await row.waitFor()
      assert.equal(await row.locator('.tt-branch-links').count(), 0)
      await row.getByRole('button', { name: 'Reply', exact: true }).click()
      await chip.getByText('Replying to Sprout', { exact: true }).waitFor()
      await draft.fill('Let’s start with the short list.')
      await send.click()
      const local = page.locator('.tt-bubble button.tt-quote')
      await local.waitFor()
      await shot('ordinary-reply')
      await local.click()
      await page.locator('[data-message-id="m3"].tt-reply-highlight').waitFor()
      await page.goto(app.url)
      await page.getByRole('button', { name: 'New thread', exact: true }).click()
      await page.waitForURL(/\/threads\/new-\d+$/)
      assert.equal(await draft.evaluate(node => node.value), '')
      assert.equal(await send.isDisabled(), true)
      await shot('ordinary-new-thread')
      assert.deepEqual(errors, [])
      results.push({ width, height, theme, shots, emptySend: 'disabled', replyJump: 'highlighted',
        back: 'returned', removeQuote: 'plain thread', markers: 'follow rename and deletion',
        keyboard: width === 390 ? 'adjusted to emulated visual viewport with no tab bar' : undefined })
      console.log(`PASS ${width}×${height} ${theme} (${shots.length} screenshots)`)
    } finally {
      await context.close()
      await app.close()
    }
  }
} finally {
  await browser.close()
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
}
