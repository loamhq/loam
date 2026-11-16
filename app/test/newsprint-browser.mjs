// Rendered family contract: real components, inherited ink, hover and focus.
import assert from 'node:assert/strict'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from '../docs/design-system/deps.mjs'
import { fixtureApp, NOW } from '../docs/design-system/fixture.mjs'
import { inspectColourFamilies } from '../tools/colour-families.mjs'
const out = process.env.NEWSPRINT_EVIDENCE || path.join(process.env.TMPDIR, 'newsprint-browser')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({headless:true,args:['--no-sandbox']})
const source = await readFile('public/app.js','utf8'), checks=[]
try {
  for (const width of [390,1440]) for (const mode of ['light','dark']) {
    const app=await fixtureApp(), context=await browser.newContext({viewport:{width,height:width===390?844:1000},colorScheme:mode,isMobile:width===390,hasTouch:width===390,reducedMotion:'reduce'})
    try {
      await context.route('**/app.js',route=>route.fulfill({contentType:'text/javascript',body:source+'\nglobalThis.__news={theme,threads,state,files,goSettings,goFiles,schedule};'}))
      const page=await context.newPage(), errors=[]
      page.on('pageerror',e=>errors.push(e.message));await page.clock.setFixedTime(new Date(NOW))
      await page.goto(app.url);await page.locator('.tt-row').first().waitFor()
      const check=async(surface)=>{
        await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(100)
        const violations=await page.evaluate(inspectColourFamilies)
        assert.deepEqual(violations,[],`${width}/${mode}/${surface}: ${JSON.stringify(violations)}`)
        checks.push({width,mode,surface,violations})
      }
      await check('chats')
      await page.getByRole('button',{name:'Settings',exact:true}).click()
      await page.evaluate(()=>__news.goSettings('appearance'));await page.locator('.appearance-card').first().waitFor()
      await check('appearance')
      if(width===1440){
        await page.locator('.segmented button:not(.on)').first().hover();await check('segment-hover')
        await page.locator('.settings .iconbtn:visible').first().hover();await check('icon-hover')
      }
      await page.keyboard.press('Tab')
      await page.locator('.segmented button:not(.on)').first().focus();await check('segment-focus')
      assert.equal(await page.locator('.segmented button:not(.on)').first().evaluate(n=>getComputedStyle(n).outlineColor),await page.evaluate(()=>{const n=document.createElement('span');n.style.color='var(--latte-track-ink)';document.body.append(n);const c=getComputedStyle(n).color;n.remove();return c}))
      // A regression must fail the invariant even when ordinary contrast passes.
      await page.evaluate(()=>{const target=[...document.querySelectorAll('.settings .iconbtn')].find(n=>n.getClientRects().length&&getComputedStyle(n).visibility!=='hidden');globalThis.__badColour={target,background:target.style.background,color:target.style.color};target.style.background='var(--latte-foam)';target.style.color='var(--paper-ink)'})
      assert.ok((await page.evaluate(inspectColourFamilies)).some(v=>v.issue==='family'))
      await page.evaluate(()=>{const {target,background,color}=globalThis.__badColour;target.style.background=background;target.style.color=color;delete globalThis.__badColour})
      await page.evaluate(()=>{__news.state.sheet=null;__news.state.pop=null;__news.threads.open('a-quieter-week');__news.schedule()})
      await page.locator('.tt-message').first().waitFor();await check('thread')
      if(width===1440){await page.locator('.tt-footer-action').first().hover();await check('reply-action-hover')}
      await page.evaluate(()=>{__news.goSettings('provider','claude-code')});await page.locator('.provider-sign-in').waitFor();await check('provider')
      await page.evaluate(()=>{__news.state.sheet=null;__news.state.pop=null;__news.goFiles('vault','Notes/plan.md',{isFile:true});__news.schedule()})
      await page.locator('.fs-preview').waitFor();await check('file-preview')
      assert.equal(await page.locator('.fs-preview a').first().evaluate(n=>getComputedStyle(n).textDecorationColor),await page.evaluate(()=>{const n=document.createElement('span');n.style.color='var(--paper-mark-rule)';document.body.append(n);const c=getComputedStyle(n).color;n.remove();return c}))
      await page.evaluate(()=>__news.files.setMode('edit'));await page.locator('.fs-editwrap').waitFor();await check('file-editor')
      assert.deepEqual(errors,[])
    } finally {await context.close();await app.close()}
  }
  await writeFile(path.join(out,'family-checks.json'),JSON.stringify(checks,null,2)+'\n')
  console.log(`PASS: ${checks.length} rendered family/contrast states at 390/1440 in light/dark; pointer hovers, keyboard focus and deliberate cross-family mutations checked; zero page errors.`)
} finally {await browser.close()}
