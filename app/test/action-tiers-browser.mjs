// Real page and authenticated relay, synthetic host records and image bytes.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { oneProductFixture } from './one-product-fixture.mjs'
import { contrast, resolvedTheme } from '../tools/theme-contrast.mjs'

const out = process.env.ACTION_TIERS_EVIDENCE
if (!out) throw new Error('Set ACTION_TIERS_EVIDENCE')
await mkdir(out, { recursive: true })
const require = createRequire(path.join(process.env.TMPDIR, 'deps/package.json'))
const sharp = require('sharp')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })
const source = await readFile('public/app.js', 'utf8'), results = [], id = 'a-quieter-week'
const at = '2026-11-12T20:45:00Z'
const paragraph = 'Give the conversation room to breathe. Keep the useful details together, leave a little space for questions, and choose one clear next step. '
const files = [
  { name: 'Garden plan.png', size: 1200, type: 'image/png', path: '98_ATTACHMENTS/preview/Garden-plan.png' },
  { name: 'Room sketch.png', size: 1200, type: 'image/png', path: '98_ATTACHMENTS/preview/Room-sketch.png' },
  { name: 'Colour notes.png', size: 1200, type: 'image/png', path: '98_ATTACHMENTS/preview/Colour-notes.png' },
]
const images = await Promise.all([[600,400,'#b4b99b'],[300,600,'#c5b4a6'],[600,300,'#a9b6c4']].map(async ([w,h,c], i) => sharp(Buffer.from(`<svg width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg"><rect width="100%" height="100%" fill="${c}"/><rect x="20" y="20" width="${w-40}" height="${h-40}" rx="16" fill="none" stroke="#514c45" stroke-width="5"/><circle cx="${w/2}" cy="${h/2}" r="${Math.min(w,h)/4}" fill="#e8e2d8"/><text x="50%" y="52%" text-anchor="middle" font-size="32" fill="#514c45">${i+1}</text></svg>`)).png().toBuffer()))
const messages = [
  { id: 'answer', author: 'sprout', text: paragraph.repeat(3) + '\n\nThere is a [reference](https://example.com/reference) if you want the details.', did: [{ label: 'Read the notes' }, { label: 'Gathered the next steps' }] },
  { id: 'long', author: 'you', text: paragraph.repeat(18), did: [] },
  { id: 'images', author: 'you', text: 'Three views of the same idea.', attachments: files, did: [] },
  { id: 'ready', author: 'sprout', text: 'The notes are ready when you are.', did: [] },
].map(m => ({ ...m, kind: 'message', at }))
const json = (res, body, status=200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
try {
  for (const width of [320,390,1440]) for (const mode of ['light','dark']) {
    const app = await oneProductFixture(), record = await app.f.api('GET', `/threads/${id}`), errors=[]
    app.routes[`GET /v1/threads/${id}`] = (req,res) => json(res, { ...record, messages, has_more:false, first_unseen_message_id:'answer', turn:null, turns:[] })
    app.routes[`GET /v1/threads/${id}/events`] = (req,res) => { res.writeHead(200, { 'content-type':'text/event-stream' }); res.write(': open ping=15000\n\n') }
    app.routes[`POST /v1/threads/${id}/attachments`] = (req,res) => json(res, { attachments:[{ path:'98_ATTACHMENTS/preview/Reading-notes.txt', name:'Reading-notes.txt', size:32, type:'text/plain' }] }, 201)
    app.routes['GET /v1/files/download'] = (req,res,url) => {
      const index = files.findIndex(f => f.path === url.searchParams.get('path'))
      if (index < 0) return json(res, {error:'File not found'},404)
      res.writeHead(200, { 'content-type':'image/png', 'content-disposition':'attachment; filename="preview.png"' }); res.end(images[index])
    }
    const settings = {mode,theme:'latte',size:100}
    app.routes['GET /v1/settings/appearance'] = (req,res) => json(res, {phone:settings,desktop:settings})
    const context = await browser.newContext({viewport:{width,height:width<1100?844:1000},colorScheme:mode,isMobile:width<1100,hasTouch:width<1100,reducedMotion:'reduce'})
    await context.route('**/app.js', route => route.fulfill({contentType:'text/javascript',body:source+'\nglobalThis.__tiers={threads,state,draft,growDraft,schedule};'}))
    await context.route('**/api/health', route => route.fulfill({json:{configured:true,ok:true,speech:true}}))
    await context.route('**/x/read-aloud/status', r => r.fulfill({ json: { speech: true } }))
    await context.route('**/x/read-aloud/speech/lookup', route => route.fulfill({json:{status:'none'}}))
    const page=await context.newPage(); page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message))
    const settle=async()=>{await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(100)}
    const bring=async locator=>{await locator.evaluate(n=>{const pane=n.closest('.tt-stream');pane.scrollTop+=n.getBoundingClientRect().top-pane.getBoundingClientRect().top-80;pane.dispatchEvent(new Event('scroll'))});await settle()}
    const capture=async name=>{if(width!==320)await page.screenshot({path:path.join(out,`${name}-${width}-${mode}.png`),animations:'disabled'})}
    try {
      await page.goto(app.url);await page.locator('.tt-row').first().waitFor()
      await page.evaluate(id=>{__tiers.threads.st.threads.find(t=>t.id===id).unread=2;__tiers.threads.open(id)},id)
      await page.locator('[data-message-id="images"]').waitFor();await settle()
      await page.evaluate(()=>{__tiers.draft.value='Keep these notes with the plan.';__tiers.growDraft();__tiers.schedule()})
      await page.locator('#pick-file').setInputFiles({name:'Reading notes.txt',mimeType:'text/plain',buffer:Buffer.from('Notes for the next conversation.')})
      await page.locator('.attachment-chip-name').filter({hasText:'Reading-notes.txt'}).waitFor();await settle()
      const marks=await page.locator('[data-message-id="answer"] .tt-footer-action,[data-message-id="answer"] .speakbtn,.fold-head').evaluateAll(nodes=>nodes.map(n=>{
        const c=getComputedStyle(n),r=n.getBoundingClientRect();return {label:n.getAttribute('aria-label')||n.textContent,width:r.width,height:r.height,background:c.backgroundColor,border:c.borderWidth,color:c.color}
      }))
      assert.ok(marks.length>=5, 'four message actions and a step summary')
      for(const m of marks){assert.equal(m.background,'rgba(0, 0, 0, 0)');assert.equal(m.border,'0px');assert.ok(m.width>=44&&m.height>=44,JSON.stringify(m))}
      const tokens=await resolvedTheme('latte',mode),ratios={}
      for(const ground of ['paper-page','paper-pane','paper-card']){ratios[ground]=contrast(tokens['paper-mark-ink'],tokens[ground]);assert.ok(ratios[ground]>=4.5)}
      await bring(page.locator('.tt-unread-strip'));await capture('actions-and-divider')
      const fold=page.locator('[data-message-id="answer"] .fold-head');await fold.click();await page.locator('.fold-body').waitFor();await capture('steps-open');await fold.click()
      const action=page.locator('[data-message-id="answer"] .tt-footer-reply')
      await page.keyboard.press('Tab');await action.focus();assert.equal(await action.evaluate(n=>getComputedStyle(n).outlineStyle),'solid')
      await action.press('Enter');await page.locator('.tt-reply-chip').waitFor();await page.locator('.tt-reply-chip .iconbtn').click()
      if(width===1440){await action.hover();assert.equal(await action.evaluate(n=>getComputedStyle(n).backgroundColor),'rgba(0, 0, 0, 0)');await page.mouse.move(0,0)}
      const more=page.getByRole('button',{name:'Show more',exact:true});await bring(more)
      const collapse=await more.evaluate(n=>{const b=n.parentElement,c=getComputedStyle(b,'::after');return {fade:c.backgroundImage,fadeHeight:parseFloat(c.height),lineHeight:parseFloat(getComputedStyle(b).lineHeight),bodyBottom:b.querySelector('.user-message-body').getBoundingClientRect().bottom,buttonBottom:n.getBoundingClientRect().bottom}})
      assert.ok(collapse.fadeHeight>=collapse.lineHeight*2.9);assert.ok(collapse.bodyBottom>=collapse.buttonBottom-10,'faint text continues behind label')
      await capture('collapsed')
      const bubble=page.locator('[data-message-id="long"] .say.user')
      // Compare paint in the label's lower line, away from the label itself.
      const b=await bubble.boundingBox(),clip={x:b.x+16,y:b.y+b.height-30,width:Math.floor(b.width/2),height:20}
      const painted=await page.screenshot({clip});await bubble.locator('.user-message-body').evaluate(n=>n.style.visibility='hidden')
      const hidden=await page.screenshot({clip});await bubble.locator('.user-message-body').evaluate(n=>n.style.visibility='')
      assert.ok(!painted.equals(hidden),'text is faintly painted behind the Show more line')
      await more.click();await page.getByRole('button',{name:'Show less',exact:true}).click();await more.waitFor()
      await bring(page.locator('[data-message-id="images"]'))
      await page.waitForFunction(()=>[...document.querySelectorAll('.message-image img')].every(n=>n.complete&&n.naturalWidth>0))
      const gallery=await page.locator('.message-image').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect(),i=n.querySelector('img');return{x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height,fit:getComputedStyle(i).objectFit,natural:[i.naturalWidth,i.naturalHeight]}}))
      if(width>=390){assert.equal(gallery[0].y,gallery[1].y,'two images share a row');assert.ok(gallery[1].x>=gallery[0].right+7.5)}
      else assert.ok(gallery[1].y>=gallery[0].bottom+7.5,'narrow gallery wraps without overlap')
      assert.ok(gallery.every(i=>i.fit==='contain'));assert.equal(await page.locator('[data-message-id="images"] .morebtn').count(),0,'gallery never triggers text collapse')
      await capture('images-and-composer')
      const layouts=[]
      for(const text of ['', 'A short draft.', paragraph.repeat(5)]){
        await page.evaluate(text=>{__tiers.draft.value=text;__tiers.growDraft();__tiers.schedule()},text);await page.locator('#draft').focus();await settle()
        const layout=await page.locator('.composer-box').evaluate(n=>{
          const box=n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height}}
          return {box:box(n),expand:box(n.querySelector('.expandbtn')),send:box(n.querySelector('.roundsend')),draft:box(n.querySelector('.draft')),overflow:document.documentElement.scrollWidth>innerWidth}
        })
        assert.ok(layout.expand.bottom<=layout.send.y,'expand has its own row');assert.ok(layout.draft.right<=layout.expand.x,'draft clears expand');assert.ok(layout.expand.width>=44&&layout.expand.height>=44);assert.equal(layout.overflow,false);layouts.push(layout)
      }
      await page.getByRole('button',{name:'Write this in a bigger window'}).click();await page.locator('#editor-draft').waitFor();await page.getByRole('button',{name:'Done',exact:true}).click()
      await page.locator('.attachment-chip-x').click();await page.locator('.attachstrip').waitFor({state:'hidden'});await settle()
      const noAttachment=[]
      for(const text of ['', 'A short draft.', paragraph.repeat(5)]){
        await page.evaluate(text=>{__tiers.draft.value=text;__tiers.growDraft();__tiers.schedule()},text);await page.locator('#draft').focus();await settle()
        const clear=await page.locator('.expandbtn').evaluate(n=>n.getBoundingClientRect().bottom<=document.querySelector('.roundsend').getBoundingClientRect().top)
        assert.equal(clear,true);noAttachment.push(clear)
      }
      const broken=page.locator('[data-message-id="images"] .bubble-files a').first()
      await broken.locator('img').evaluate(n=>n.src='/api/files/download?root=vault&path=missing.png')
      await page.waitForFunction(()=>document.querySelector('[data-message-id="images"] .bubble-files a img').hidden)
      assert.equal(await broken.locator('img').evaluate(n=>getComputedStyle(n).display),'none')
      assert.ok(await broken.innerText(),'a missing image retains its named download')
      assert.ok(await broken.evaluate(n=>n.classList.contains('attachment-chip')),'a missing image becomes a filename chip')
      assert.deepEqual(errors,[])
      assert.ok(app.broker.seen.filter(r=>r.path==='/v1/files/download').length>=3,'image bytes crossed authenticated relay')
      results.push({width,mode,marks,ratios,collapse,gallery,layouts,noAttachment,errors})
      console.log(`PASS ${width} ${mode}: bare 44px marks, keyboard reply/focus, steps, divider, gradual painted fade, wrapping image gallery, composer with/without attachment and fullscreen draft`)
    } finally {await context.close();await app.close()}
  }
  await writeFile(path.join(out,'browser.json'),JSON.stringify(results,null,2)+'\n')
} finally {await browser.close()}
