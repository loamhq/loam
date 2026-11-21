// Run the real page and relay against a sample broker, with no provider session.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const out = process.env.THREAD_BRANCH_EVIDENCE
if (!out) throw new Error('Set THREAD_BRANCH_EVIDENCE')
const baseline = process.env.THREAD_BRANCH_BASELINE === '1'
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
    const shot = async (name, animations = 'disabled') => {
      await page.evaluate(async () => {
        await document.fonts.ready
        await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame)
      })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      const file = `${name}-${width}-${theme}.png`
      await page.screenshot({ path: path.join(out, file), animations })
      shots.push(file)
    }
    const draft = page.locator('#draft')
    const send = page.locator('.tt-composer button[type=submit]')
    const dialog = page.getByRole('dialog', { name: 'New thread with', exact: true })
    const row = page.locator('.tt-stream [data-message-id="m3"]')
    const highlight = row.and(page.locator('.tt-reply-highlight'))
    const branch = row.getByRole('button', { name: 'Branch into a new thread' })
    const sendMessage = async () => {
      const [response] = await Promise.all([
        page.waitForResponse(res => /\/api\/threads\/[^/]+\/messages$/.test(new URL(res.url()).pathname) && res.ok()),
        send.click(),
      ])
      const { message } = await response.json()
      return page.locator(`[data-message-id=${JSON.stringify(message.id)}] .tt-bubble`)
    }
    const metrics = bubble => bubble.evaluate(node => {
      const body = node.querySelector('.user-message-body')
      const lines = [...body.querySelectorAll('p')].reduce((total, prose) =>
        total + Math.round(prose.getBoundingClientRect().height / parseFloat(getComputedStyle(prose).lineHeight)), 0)
      return { collapsed: node.classList.contains('clamped'), body: body.clientHeight, scrollHeight: body.scrollHeight, lines }
    })
    const pointer = control => width === 390 ? control.tap() : control.click()
    const jumpStyle = (target = row) => target.evaluate(node => {
      const css = getComputedStyle(node)
      return { focused: document.activeElement === node, focusVisible: node.matches(':focus-visible'),
        outline: css.outlineStyle, outlineColor: css.outlineColor, offset: css.outlineOffset, wash: css.backgroundColor, padding: css.boxShadow }
    })
    const captureJump = async name => {
      await highlight.waitFor()
      // Capture after native touch feedback, while the destination wash still holds its full tint.
      if (width === 390) await page.waitForTimeout(350)
      await shot(name, 'allow')
      const arrival = await jumpStyle()
      await highlight.waitFor({ state: 'detached' })
      const settled = await jumpStyle()
      if (!baseline) {
        assert.equal(arrival.focused, true)
        assert.equal(arrival.outline, 'none')
        assert.equal(settled.outline, 'none')
        assert.notEqual(arrival.padding, 'none', 'wash extends past the message')
        assert.notEqual(arrival.wash, settled.wash)
        assert.equal(settled.wash, 'rgba(0, 0, 0, 0)')
      }
      return { arrival, settled }
    }
    const checkKeyboardJump = async control => {
      await control.focus()
      await page.keyboard.press('Enter')
      await highlight.waitFor()
      const { focused, focusVisible, outline, offset } = await jumpStyle()
      assert.deepEqual({ focused, focusVisible, outline, offset }, { focused: true, focusVisible: true, outline: 'solid', offset: '8px' })
    }
    const readShort = async (bubble, name) => {
      await bubble.locator('.user-message-body p').first().waitFor()
      await bubble.scrollIntoViewIfNeeded()
      await shot(name)
      const layout = await metrics(bubble)
      assert.equal(layout.lines, 3)
      if (!baseline) {
        assert.equal(layout.collapsed, false)
        assert.equal(layout.body, layout.scrollHeight)
      }
      return layout
    }
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
      const opening = 'Let’s start with the short list.\nKeep Friday open.\n\nWe can review it on Thursday.'
      await draft.fill(opening)
      await page.locator('.tt-composer button[type=submit]:enabled').waitFor()
      await shot('typed')
      const firstBubble = await sendMessage()
      const quoted = page.locator('.tt-bubble .tt-quote')
      await quoted.waitFor()
      const answer = page.getByText(/Picking up from .* keep the rest of the plan flexible\./)
      await answer.waitFor()
      await page.locator('.tt-title').filter({ hasText: 'Let’s start with the short list.' }).waitFor()
      assert.match(await quoted.innerText(), /A little room for the week/)
      assert.match(await quoted.innerText(), /Let’s leave a little room for the unexpected/)
      assert.match(await answer.innerText(), /Let’s leave a little room for the unexpected/)
      assert.equal(await quoted.evaluate(node => getComputedStyle(node).textDecorationLine), 'none')
      const sent = app.broker.seen.find(({ method, path }) => method === 'PUT' && path.endsWith('/messages'))
      assert.deepEqual(Object.fromEntries(Object.entries(JSON.parse(sent.body)).filter(([key]) => key !== 'submission_id')),
        { text: opening, reply_to: 'm3', reply_thread: source })
      assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
      const branchReply = await readShort(firstBubble, 'new-thread')
      await page.reload()
      await quoted.waitFor()
      await pointer(quoted)
      await page.waitForURL(`**/threads/${source}?message=m3`)
      assert.equal(await draft.evaluate(node => node.value), '', 'reload clears page-local source text')
      const sourceJump = await captureJump('source-highlight')
      const direct = await context.newPage()
      let directJump
      try {
        await direct.goto(`${app.url}/threads/${source}?message=m3`)
        const target = direct.locator('[data-message-id="m3"]')
        const cue = target.and(direct.locator('.tt-reply-highlight'))
        await cue.waitFor()
        if (!baseline) assert.equal((await jumpStyle(target)).outline, 'none')
        await cue.waitFor({ state: 'detached' })
        directJump = await jumpStyle(target)
        if (!baseline) assert.equal(directJump.outline, 'none')
      } finally { await direct.close() }
      await page.goBack()
      assert.equal(page.url(), branchUrl)
      await quoted.waitFor()
      if (baseline) await quoted.click()
      else await checkKeyboardJump(quoted)
      const marker = row.getByRole('link', { name: /^branched → Let’s start with the short list\./ })
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
      const reply = row.getByRole('button', { name: 'Reply', exact: true })
      await reply.click()
      await chip.getByText('Replying to Sprout', { exact: true }).waitFor()
      await draft.fill(opening)
      const localBubble = await sendMessage()
      const local = localBubble.locator('button.tt-quote')
      await local.waitFor()
      const localReply = await readShort(localBubble, 'ordinary-reply')
      await pointer(local)
      const localJump = await captureJump('local-highlight')
      if (!baseline) {
        await checkKeyboardJump(local)
        await highlight.waitFor({ state: 'detached' })
        assert.equal((await jumpStyle()).outline, 'solid')

        const long = Array.from({ length: 8 }, (_, i) => `Planning note ${i + 1}.`).join('\n')
        const clampedHeights = []
        for (const withQuote of [true, false]) {
          if (withQuote) await reply.click()
          await draft.fill(long)
          const bubble = await sendMessage()
          const more = bubble.getByRole('button', { name: 'Show more', exact: true })
          await more.waitFor()
          assert.equal(await bubble.locator('.tt-quote').count(), Number(withQuote))
          clampedHeights.push((await metrics(bubble)).body)
          await more.click()
          const less = bubble.getByRole('button', { name: 'Show less', exact: true })
          await less.waitFor()
          assert.ok((await metrics(bubble)).body > clampedHeights.at(-1))
          await less.click()
          await more.waitFor()
        }
        assert.equal(clampedHeights[0], clampedHeights[1], 'quoted and plain text have the same clamp')
      }
      await page.goto(app.url)
      await page.getByRole('button', { name: 'New thread', exact: true }).click()
      await page.waitForURL(/\/threads\/new-\d+$/)
      assert.equal(await draft.evaluate(node => node.value), '')
      assert.equal(await send.isDisabled(), true)
      await shot('ordinary-new-thread')
      assert.deepEqual(errors, [])
      results.push({ width, height, theme, shots, emptySend: 'disabled', replyJump: 'highlighted',
        branchReply, localReply, sourceJump, localJump, directJump,
        back: 'returned', removeQuote: 'plain thread', markers: 'follow rename and deletion',
        keyboard: width === 390 ? 'adjusted to emulated visual viewport with no tab bar' : undefined })
      console.log(`${baseline ? 'BASELINE' : 'PASS'} ${width}×${height} ${theme} · branch/local short collapsed ${branchReply.collapsed}/${localReply.collapsed} · direct URL settled outline ${directJump.outline}`)
    } finally {
      await context.close()
      await app.close()
    }
  }
} finally {
  await browser.close()
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
}
