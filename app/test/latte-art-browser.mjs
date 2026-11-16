// Local real app/relay; agents, conversations and shared settings are fixtures.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { tmpdir } from 'node:os'
import { oneProductFixture } from './one-product-fixture.mjs'
import { appearanceValues } from '../src/appearance.js'
import { loadThemes } from '../src/themes.js'
import { mergeAppearance } from '../public/theme-options.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.LATTE_EVIDENCE || path.join(tmpdir(), 'latte-art-evidence')
await mkdir(out, { recursive: true })
const app = await oneProductFixture(), themes = (await loadThemes()).themes
const values = appearanceValues(null, themes), writes = [], results = []
const json = (res, value) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => {
  const patch = JSON.parse(raw); writes.push(patch)
  for (const kind of Object.keys(patch)) values[kind] = mergeAppearance(values[kind], patch[kind])
  json(res, values)
}
const source = await readFile('public/app.js', 'utf8')
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const settings = async (page, width) => {
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  if (width === 390) await page.getByRole('button', { name: /Appearance/ }).click()
}
const saved = page => page.waitForFunction(() => !__latteTest.theme.status.saving && !__latteTest.theme.status.error)
try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const kind = width === 390 ? 'phone' : 'desktop'
    values[kind] = { mode, size: 100, theme: 'latte', options: appearanceValues(null, themes)[kind].options }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, isMobile: width === 390, hasTouch: width === 390, colorScheme: mode, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__latteTest={theme,threads,closeSheet,blob,agentLook};' }))
    const page = await context.newPage(), errors = [], csp = []
    page.on('pageerror', e => errors.push(e.message))
    page.on('console', msg => { if (/Content Security Policy|Refused to/.test(msg.text())) csp.push(msg.text()) })
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor(); await page.evaluate(() => document.fonts.ready)
      await settings(page, width)
      assert.equal(await page.getByRole('switch', { name: 'In a mug', exact: true }).count(), 0)
      await page.getByRole('group', { name: 'Agent shapes', exact: true }).getByRole('button', { name: 'Latte art', exact: true }).click()
      await page.getByRole('switch', { name: 'In a mug', exact: true }).waitFor()
      assert.equal(await page.getByRole('switch', { name: 'In a mug', exact: true }).getAttribute('aria-checked'), 'true')
      assert.equal(await page.getByRole('group', { name: 'Agent colours', exact: true }).count(), 0)
      await saved(page)
      await page.screenshot({ path: path.join(out, `appearance-${width}-${mode}.png`), animations: 'disabled' })
      const controls = await page.locator('.theme-option-choices button').evaluateAll(nodes => nodes.map(n => ({ height: n.getBoundingClientRect().height, overflow: n.scrollWidth > n.clientWidth })))
      assert.ok(controls.every(n => n.height >= 44 && !n.overflow), JSON.stringify(controls))
      for (const mug of ['on', 'off']) for (const inside of ['pour', 'initials']) {
        if (await page.getByRole('switch', { name: 'In a mug', exact: true }).getAttribute('aria-checked') !== String(mug === 'on')) await page.getByRole('switch', { name: 'In a mug', exact: true }).click()
        await page.getByRole('group', { name: 'Inside', exact: true }).getByRole('button', { name: inside === 'pour' ? 'Pour' : 'Initials', exact: true }).click()
        await saved(page)
        assert.equal(values[kind].options.latte['latte-mug'], mug)
        assert.equal(values[kind].options.latte['latte-inside'], inside)
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
        await page.evaluate(() => __latteTest.closeSheet())
        const label = `${width}-${mode}-${mug}-${inside}`
        await page.screenshot({ path: path.join(out, `chats-${label}.png`), animations: 'disabled' })
        await page.evaluate(() => __latteTest.threads.open('small-details'))
        await page.locator('.tt-message .mention .blob .latte-art').first().waitFor()
        const mention = page.locator('.tt-message .mention .blob').first(), text = await mention.locator('..').textContent()
        assert.equal(await mention.textContent(), '')
        const before = await mention.evaluate(n => { globalThis.__keptMention = n; return getComputedStyle(n.querySelector('.latte-pour')).display })
        assert.equal(before === 'none', inside === 'initials')
        const mugDisplay = await mention.locator('.latte-mug').evaluate(n => getComputedStyle(n).display)
        assert.equal(mugDisplay === 'none', mug === 'off')
        const paint = await mention.evaluate(n => ({ position: getComputedStyle(n).position, background: getComputedStyle(n).backgroundColor, initial: getComputedStyle(n, '::after').content, coffee: getComputedStyle(n.querySelector('circle[r="37.5"]')).fill }))
        assert.equal(paint.position, 'absolute', 'the decorative mention face must not enter text flow')
        assert.equal(paint.background, 'rgba(0, 0, 0, 0)')
        assert.equal(paint.coffee, 'rgb(126, 82, 48)')
        if (inside === 'initials') assert.match(paint.initial, /[A-Za-z]/)
        await page.screenshot({ path: path.join(out, `thread-${label}.png`), animations: 'disabled' })
        // CSS must update the actual retained mention, without modifying text.
        await page.evaluate(inside => __latteTest.theme.set({ options: { latte: { 'latte-inside': inside === 'pour' ? 'initials' : 'pour' } } }), inside)
        assert.equal(await mention.evaluate(n => n === __keptMention), true)
        assert.equal(await mention.locator('..').textContent(), text)
        await page.evaluate(inside => __latteTest.theme.set({ options: { latte: { 'latte-inside': inside } } }), inside)
        await saved(page)
        await page.evaluate(() => __latteTest.threads.close())
        await settings(page, width)
        results.push({ width, mode, mug, inside, controls, paint, retainedMention: true, fixturePersistence: true })
      }
      // Preserve a representative reload and theme-switch check per device/mode.
      await page.getByRole('group', { name: 'Theme', exact: true }).getByRole('button', { name: 'Plain', exact: true }).click()
      assert.equal(await page.getByRole('switch', { name: 'In a mug', exact: true }).count(), 0)
      await page.getByRole('group', { name: 'Theme', exact: true }).getByRole('button', { name: 'Latte & Newspaper', exact: true }).click()
      assert.equal(await page.getByRole('switch', { name: 'In a mug', exact: true }).getAttribute('aria-checked'), 'false')
      await page.getByRole('group', { name: 'Agent shapes', exact: true }).getByRole('button', { name: 'Circle', exact: true }).click()
      await page.getByRole('group', { name: 'Agent colours', exact: true }).waitFor()
      assert.equal(await page.getByRole('switch', { name: 'In a mug', exact: true }).count(), 0)
      await page.getByRole('group', { name: 'Agent shapes', exact: true }).getByRole('button', { name: 'Latte art', exact: true }).click()
      await saved(page); await page.reload(); await page.locator('.tt-row').first().waitFor()
      assert.equal(await page.evaluate(() => document.documentElement.getAttribute('data-option-latte-inside')), 'initials')
      assert.equal(await page.evaluate(() => document.documentElement.getAttribute('data-option-latte-mug')), 'off')
      await page.locator('.tt-personas .persona').first().click()
      await page.locator('.tt-persona-card .latte-art').waitFor()
      await page.screenshot({ path: path.join(out, `agent-card-${width}-${mode}.png`), animations: 'disabled' })
      assert.deepEqual(errors, []); assert.deepEqual(csp, [])
      // Separate review plate: all eight vector pours at 44px and 22px. This
      // is explicitly a component gallery, not an additional product screen.
      const gallery = await context.newPage()
      await gallery.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: `
        import { h, blob } from '/ui.js';
        import { agentLook } from '/sessions.js';
        import { POURS, pourFor } from '/latte-avatar.js';
        const root = document.documentElement;
        root.dataset.optionAgentShapes = 'latte-art';
        const ids = POURS.map(([pour]) => Array.from({length:200},(_,i)=>'sample-'+i).find(id=>pourFor(id)[0]===pour));
          const plate = h('section', {style:{padding:'20px',margin:'16px',borderRadius:'24px',background:'var(--paper-pane)'}},
            h('h2',{text:'Eight pours · 44 / 22px',style:{fontFamily:'var(--font-display)',fontWeight:'400',fontSize:'18px',margin:'0 0 20px'}}));
          const grid = h('div',{style:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(70px,1fr))',gap:'20px'}});
          ids.forEach((id,i)=>{
            const cell=h('div',{style:{display:'flex',flexDirection:'column',alignItems:'center',gap:'10px'}},
              blob({...agentLook(id),initial:i%2?'Sa':'A'}, {size:44,font:19}),
              blob({...agentLook(id),initial:i%2?'Sa':'A'}, {size:22,font:10}),
              h('span',{text:POURS[i][0],style:{fontSize:'11px',color:'var(--paper-ink-3)'}}));
            grid.append(cell);
          });
          plate.append(grid);
        const gallery=h('main',{style:{width:'100%',maxWidth:'1000px',flexShrink:'0',margin:'auto'}},plate);
        document.querySelector('#app').replaceChildren(gallery);
        globalThis.__galleryReady=true;
      ` }))
      await gallery.goto(app.url); await gallery.waitForFunction(() => globalThis.__galleryReady); await gallery.evaluate(() => document.fonts.ready)
      for (const mug of ['on', 'off']) for (const inside of ['pour', 'initials']) {
        await gallery.evaluate(({ mug, inside }) => {
          document.documentElement.dataset.optionLatteMug = mug
          document.documentElement.dataset.optionLatteInside = inside
        }, { mug, inside })
        await gallery.locator('main').screenshot({ path: path.join(out, `pours-${width}-${mode}-${mug}-${inside}.png`), animations: 'disabled' })
      }
      await gallery.close()
    } finally { await context.close() }
  }
  await writeFile(path.join(out, 'checks.json'), JSON.stringify({ source: 'local real app/relay, fixture broker; no live deployment claim', browser: browser.version(), results, writes }, null, 2) + '\n')
  console.log(`PASS: ${results.length} Latte art variants at 390/1440, light/dark, mug on/off, pour/initials; real controls, fixture persistence/reload, retained mentions, no overflow, page errors or CSP violations.`)
} finally { await browser.close(); await app.close() }
