// Actual application and Chromium, synthetic records through the real relay.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default
const out = process.env.THREAD_MENTION_EVIDENCE
if (!out) throw new Error('Set THREAD_MENTION_EVIDENCE')
await mkdir(out, { recursive: true })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const browser = await chromium.launch({ headless: true, executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const results = []
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture()
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__mention = { threads, goChats };' }))
    const page = await context.newPage(), errors = [], captures = [], geometry = {}
    page.on('pageerror', (error) => errors.push(error.message))
    const draft = page.locator('#draft'), toggle = page.locator('.tt-mention-button'), list = page.locator('.tt-mentions')
    const text = () => draft.evaluate((el) => el.value)
    const hit = (el, options) => width === 390 ? el.tap(options) : el.click(options)
    const measure = () => page.locator('.composer').evaluate((el) => ({ height: el.getBoundingClientRect().height, bottom: innerHeight - el.getBoundingClientRect().bottom }))
    const closed = async () => { await list.waitFor({ state: 'detached' }); assert.equal(await toggle.getAttribute('aria-expanded'), 'false'); assert.equal(await toggle.getAttribute('aria-label'), 'Mention an agent'); assert.equal(await toggle.evaluate((el) => el.classList.contains('on')), false) }
    const opened = async () => { await list.waitFor(); assert.equal(await toggle.getAttribute('aria-expanded'), 'true'); assert.equal(await toggle.getAttribute('aria-label'), 'Remove mention'); assert.equal(await toggle.evaluate((el) => el.classList.contains('on')), true) }
    const thread = async () => { await page.evaluate(() => __mention.goChats()); await page.locator('.tt-row').filter({ hasText: 'A little room for the week' }).click(); await draft.waitFor(); await page.waitForFunction(() => !__mention.threads.st.open.loading) }
      const raw = async (crop = false) => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      return crop ? page.locator('.thread-dock').screenshot({ animations: 'disabled' }) : page.screenshot({ animations: 'disabled' })
    }
    const pair = async (name, left) => { await left(); const file = name + '-' + width + '-' + theme + '.png'; const dest = path.join(out, file); await page.screenshot({ path: dest, animations: 'disabled' }); ; console.log(file) }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await pair('inserted', async () => { await thread(); await draft.fill(''); await hit(toggle); await opened(); assert.equal(await text(), '@'); assert.equal(await draft.evaluate((el) => document.activeElement === el), true) })
      await pair('removed', async () => { await thread(); await draft.fill(''); await hit(toggle); await opened(); await draft.pressSequentially('qui'); assert.equal(await text(), '@qui'); await hit(toggle); await closed(); assert.equal(await text(), '') })
      await pair('chosen', async () => { await thread(); await draft.fill(''); await hit(toggle); await hit(list.getByRole('option').filter({ hasText: 'Quill' })); await closed(); assert.equal(await text(), '@quill '); await draft.pressSequentially('A thought to share.'); assert.equal(await text(), '@quill A thought to share.') })
      await pair('hint', async () => { await thread(); await draft.fill(''); assert.equal(await page.locator('.thread-dock .hint').count(), width === 390 ? 0 : 1); if (width === 1440) assert.doesNotMatch(await page.locator('.thread-dock .hint').innerText(), /@| · /) })
      assert.match(await page.locator('.thread-dock .hint').innerText(), /Sprout asks before anything irreversible/)
      await thread(); await draft.fill('before after')
      await draft.evaluate(async (el) => { const { setMarkdown } = await import('/compose.js'); el.focus(); setMarkdown(el, el.value, 6) })
      await hit(toggle); await opened(); assert.equal(await text(), 'before @ after')
      await draft.pressSequentially('qui'); assert.equal(await text(), 'before @qui after')
      await hit(toggle); await closed(); assert.equal(await text(), 'before  after')
      // Existing whitespace needs no added prefix; toggling removes only its active range.
      await draft.fill('before after'); await draft.evaluate(async (el) => { const { setMarkdown } = await import('/compose.js'); el.focus(); setMarkdown(el, el.value, 7) })
      await hit(toggle); await opened(); assert.equal(await text(), 'before @after'); await hit(toggle); await closed(); assert.equal(await text(), 'before after')
      await draft.fill(''); await hit(toggle); await draft.press('Backspace'); await closed(); assert.equal(await text(), '')
      await draft.fill(''); await draft.pressSequentially('@qui'); await opened(); await hit(toggle); await closed(); assert.equal(await text(), '')
      await hit(toggle); await draft.press('Escape'); await closed(); assert.equal(await text(), '@')
      await draft.fill(''); await hit(toggle); await hit(page.locator('.tt-stream'), { position: { x: 10, y: 10 } }); await closed(); assert.equal(await text(), '@')
      await draft.fill(''); await hit(toggle); await page.getByRole('button', { name: 'Thread actions', exact: true }).focus(); await closed(); assert.equal(await text(), '@')
      await draft.fill(''); await hit(toggle); await toggle.focus(); await opened(); await toggle.press('Enter'); await closed(); assert.equal(await text(), '')
      await hit(toggle); await list.getByRole('option').filter({ hasText: 'Quill' }).focus(); await opened(); await page.keyboard.press('Enter'); await closed(); assert.equal(await text(), '@quill ')
      await draft.fill(''); await draft.pressSequentially('@qui'); await draft.press('Enter'); await closed(); assert.equal(await text(), '@quill ')
      assert.deepEqual(errors, [])
      results.push({ width, theme, insertAtCursor: true, prefixSpacing: true, toggleRemovesQuery: true, backspaceCloses: true, typedMentionToggles: true, escapePreservesText: true, outsideTapPreservesText: true, blurPreservesText: true, buttonFocusAllowed: true, optionFocusAllowed: true, pointerAndKeyboardChoice: true, hintRows: true, realTouch: width === 390, geometry, errors, captures })
      console.log(`PASS ${width} ${theme}`)
    } finally { await context.close(); await app.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
} finally { await browser.close() }
