import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createThreadFixture } from '../public/threads-fixture.js'
import { fakeBroker, appAgainst, closing } from './harness.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.THREAD_ROUND_THREE_EVIDENCE
if (!out) throw new Error('Set THREAD_ROUND_THREE_EVIDENCE')
await mkdir(out, { recursive: true })
const f = createThreadFixture(), created = new Set(), results = []
const json = (res, value, status=200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
const classic = { session_id:'classic', agent:'scout', provider:'claude-code', title:'Classic keyboard check', state:'running', status:'done', live:true, record_seq:0, seen_seq:0, turns:[], pinned:false, archived:false, pending_permissions:[], background_tasks:0 }
const routes = {
  'GET /v1/agents': (req,res) => json(res,{ agents:f.agents }),
  'GET /v1/providers': (req,res) => json(res,{ providers:f.providers }),
  'GET /v1/sessions': (req,res) => json(res,{ sessions:[classic], live_count:1, cap:5 }),
  'GET /v1/sessions/events': (req,res) => {res.writeHead(200,{'content-type':'text/event-stream'});res.write(`: open ping=15000\n\ndata: ${JSON.stringify({type:'broker.session',...classic})}\n\n`)},
  'GET /v1/sessions/classic/events': (req,res) => {res.writeHead(200,{'content-type':'text/event-stream'});res.write(`: open ping=15000\n\ndata: ${JSON.stringify({type:'broker.session',...classic,window_first_seq:0,turns_before:0})}\n\n`)},
  'GET /v1/threads': async(req,res,url) => json(res,await f.api('GET','/threads'+url.search)),
  'GET /v1/threads/events': (req,res) => {res.writeHead(200,{'content-type':'text/event-stream'});res.write(': open ping=15000\n\n');const stop=f.subscribeList((e)=>res.write(`data: ${JSON.stringify(e)}\n\n`));res.on('close',stop)},
  'POST /v1/threads': async(req,res,url,raw) => {const row=await f.api('POST','/threads',JSON.parse(raw));created.add(row.id);addRoutes(row.id);json(res,row,201)},
}
function addRoutes(id) {
  for(const [method,suffix] of [['GET',''],['PATCH',''],['DELETE',''],['POST','/messages']]) routes[`${method} /v1/threads/${id}${suffix}`]=async(req,res,url,raw)=>{
    const answer=await f.api(method,`/threads/${id}${suffix}${url.search}`,raw?JSON.parse(raw):undefined)
    if(method==='DELETE')created.delete(id)
    json(res,answer)
  }
  routes[`GET /v1/threads/${id}/events`]=(req,res)=>{res.writeHead(200,{'content-type':'text/event-stream'});res.write(': open ping=15000\n\n');const stop=f.subscribe(id,(e)=>res.write(`data: ${JSON.stringify(e)}\n\n`));res.on('close',stop)}
}
for(const row of (await f.api('GET','/threads')).threads)addRoutes(row.id)
const broker=await fakeBroker(routes),app=await appAgainst(broker)
const browser=await chromium.launch({executablePath:process.env.LOAM_CHROME,headless:true,args:['--no-sandbox','--disable-gpu']})
try {
  for(const width of [390,1440])for(const theme of ['light','dark']) {
    const context=await browser.newContext({viewport:{width,height:width===390?844:1000},colorScheme:theme,isMobile:width===390,hasTouch:width===390})
    const page=await context.newPage(),errors=[]
    page.on('pageerror',(e)=>errors.push(e.message))
    const shot=async(name)=>{await page.evaluate(async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame)});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.screenshot({path:path.join(out,`${name}-${width}-${theme}.png`),animations:'disabled'})}
    await page.goto(app.url)
    const creation=page.waitForResponse((r)=>r.request().method()==='POST'&&r.url().endsWith('/api/threads'))
    await page.getByRole('button',{name:'New thread',exact:true}).click()
    await creation
    await page.locator('.tt-empty-icon').waitFor()
    const id=[...created].at(-1),draft=page.locator('#draft[contenteditable=true]')
    assert.equal(await page.locator('.tt-empty-icon svg').count(),1)
    assert.equal(await page.locator('.tt-empty .blob').count(),0)
    await shot('empty-people')
    await page.getByTitle('Thread details',{exact:true}).click()
    await page.getByRole('button',{name:'Use Conversation icon',exact:true}).click()
    await page.waitForFunction(()=>!document.querySelector('[aria-label="Use Conversation icon"]').disabled)
    const helpGap=await page.locator('.tt-nobody-note').evaluate((p)=>p.getBoundingClientRect().top-p.previousElementSibling.getBoundingClientRect().bottom)
    assert.ok(helpGap>=20)
    await page.getByRole('button',{name:'Chat',exact:true}).click()
    assert.equal(await page.locator('.tt-empty-icon svg').count(),1)
    await shot('empty-picked-glyph')
    await draft.fill('First line');await draft.press('Enter');await draft.type('Second line')
    const threadText=await draft.evaluate(async(el)=>(await import('/compose.js')).readField(el).md)
    assert.equal(threadText,'First line\nSecond line')
    assert.equal((await f.api('GET',`/threads/${id}`)).messages.length,0)
    await draft.fill('@quill @pip   A little space for the week')
    await page.locator('.roundsend').click()
    await page.waitForFunction(()=>document.querySelector('.tt-title .display')?.textContent==='A little space for the week')
    await page.waitForFunction(()=>document.querySelectorAll('.tt-avatar-stack > span:not(.tt-member-more)').length===0)
    assert.equal(await page.locator('.tt-member-more').count(),1)
    assert.equal(await page.locator('.tt-member-more').innerText(),'+2')
    for(let n=0;n<50;n++){const r=await f.api('GET',`/threads/${id}`);if(['quill','pip'].every((author)=>r.messages.some((m)=>m.author===author)))break;await page.waitForTimeout(100)}
    await page.locator('.tt-stream .tt-typing').waitFor({state:'detached'})
    await shot('no-default-replies')
    const record=await f.api('GET',`/threads/${id}`)
    assert.equal(record.thread.title,'A little space for the week')
    assert.equal(record.messages[0].text,'@quill @pip   A little space for the week')
    await page.reload()
    await page.locator('.tt-row').filter({hasText:'A little space for the week'}).click()
    await page.waitForFunction(()=>document.querySelectorAll('.tt-avatar-stack > span:not(.tt-member-more)').length===0)
    await f.api('PATCH',`/threads/${id}`,{add:['sprout','scout','ledger']})
    await page.waitForFunction(()=>document.querySelector('.tt-member-more')?.textContent==='+5')
    assert.equal(await page.locator('.tt-avatar-stack > span:not(.tt-member-more)').count(),0)
    await shot('overflow')
    await page.getByTitle('Thread details',{exact:true}).click()
    const config=page.locator('.tt-member-config').first()
    const alignment=await config.evaluate((el)=>{const engine=el.querySelector('.tt-engine-select').getBoundingClientRect(),label=el.querySelector('.tt-model-select').getBoundingClientRect(),select=el.querySelector('.tt-model-select').getBoundingClientRect();return {engine:engine.x,label:label.x,select:select.x,tidy:label.y>=engine.bottom?Math.abs(label.x-engine.x)<1:label.x>=engine.right+7}})
    assert.ok(Math.abs(alignment.label-alignment.select)<1&&alignment.tidy)
    await page.locator('.tt-member').first().scrollIntoViewIfNeeded()
    await shot('details-column')
    await page.locator('.tt-details-scroll').evaluate((el)=>el.scrollTop=0)
    await shot('details-spacing')
    await page.getByTitle('Rename thread',{exact:true}).click()
    await page.getByRole('textbox',{name:'Thread title',exact:true}).fill('@quill A chosen title')
    await page.getByRole('textbox',{name:'Thread title',exact:true}).press('Enter')
    await page.getByRole('button',{name:'Chat',exact:true}).click()
    await draft.fill('Another quiet thought');await page.locator('.roundsend').click()
    await page.waitForFunction(()=>document.querySelector('#draft')?.textContent==='')
    assert.equal((await f.api('GET',`/threads/${id}`)).thread.title,'@quill A chosen title')
    if(width<1100)await page.getByRole('button',{name:'Back to threads',exact:true}).click()
    await page.getByRole('button',{name:'Settings',exact:true}).first().click()
    assert.deepEqual(errors,[])
    results.push({width,theme,helpGap,aligned:true,memberControlMatchesLayout:true,overflowCountsHiddenMembers:true,titlePersisted:true,chosenTitlePreserved:true,errors})
    await context.close()
    await f.api('DELETE',`/threads/${id}`);created.delete(id)
  }
  assert.equal(created.size,0)
  await writeFile(path.join(out,'browser.json'),JSON.stringify(results,null,2)+'\n')
} finally {await browser.close();await closing(app,broker)()}
console.log(JSON.stringify(results,null,2))
