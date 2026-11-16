// Real page/relay with synthetic records only. Optional baseline uses this repo's git objects.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile, readdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.MENU_EVIDENCE
if (!out) throw new Error('Set MENU_EVIDENCE')
await mkdir(out, { recursive: true })
const baseline = process.env.MENU_BASELINE, prefix = baseline ? 'before' : 'after'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const source = async (name) => baseline ? execFileSync('git', ['show', `${baseline}:public/${name}`], { encoding: 'utf8' }) : readFile(new URL(`../public/${name}`, import.meta.url), 'utf8')
const results = [], widths = process.env.MENU_QUICK ? [Number(process.env.MENU_QUICK_WIDTH)||1440] : [1440, 1920, 390]
try {
 for (const width of widths) for (const collapsed of (width === 390 ? [false] : [false, true])) for (const theme of (process.env.MENU_QUICK ? ['light'] : ['light', 'dark'])) {
  const app = await oneProductFixture(), errors = []
  const id = 'a-quieter-week'
  const record = await app.f.api('GET', `/threads/${id}`)
  // Named background work and an explicit model are fixture facts.
  record.messages.push({id:'long-reply',author:'sprout',kind:'message',text:'There is room for a calmer week. Keep a clear next step, leave space between commitments and gather the loose ends. '.repeat(12)})
  record.thread.members[0].model = 'opus'
  record.thread.members[0].background = 1
  record.thread.members[0].background_tasks = [{ id: 'layout', description: 'Check the menus across phone and desktop layouts', started_at: '2026-11-11T18:00:00Z' }]
  app.routes[`GET /v1/threads/${id}`] = (req,res) => { res.writeHead(200,{'content-type':'application/json'}); res.end(JSON.stringify(record)) }
  app.routes['GET /v1/settings/appearance'] = (req,res) => { res.writeHead(200,{'content-type':'application/json'}); res.end(JSON.stringify({ phone:{mode:theme,size:100},desktop:{mode:theme,size:100} })) }
  app.records.forEach(row=>{row.turns=[{model:'claude-opus',context:{used:123000,window:1000000},cost_delta_usd:0.12}];row.background_tasks=1;row.record_seq=8;row.status='waiting'})
  app.events.push({type:'broker.background_tasks',count:1,tasks:[{task_id:'classic-task',description:'Check the reading layout'}],seq:8})
  app.routes['GET /v1/speech/health'] = (req,res) => { res.writeHead(200,{'content-type':'application/json'}); res.end('{"available":true}') }
  const ctx = await browser.newContext({ viewport:{width,height:1000}, colorScheme:theme, isMobile:width===390, hasTouch:width===390 })
  for (const name of ['app.js','threads.js','style.css','threads.css']) {
    let body = await source(name)
    if (name === 'app.js') body += '\nglobalThis.__menu = { threads, state, picker, schedule, openSheet, goSettings, menuSurfaces: typeof menuSurfaces === "undefined" ? null : menuSurfaces };'
    await ctx.route(`**/${name}`,route=>route.fulfill({contentType:name.endsWith('.css')?'text/css':'text/javascript',body}))
  }
  await ctx.route('**/api/files/roots', route=>route.fulfill({json:{roots:[{root:'vault',name:'Vault',description:'Shared files',writable:true}]}}))
  await ctx.route('**/api/health', route=>route.fulfill({json:{configured:true,ok:true,speech:true}}))
  const page = await ctx.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', e => errors.push(e.message))
  const stamp = `${width}-${collapsed?'collapsed':'open'}-${theme}`
  const settle = async () => { await page.evaluate(async()=>{ await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) }); await page.waitForTimeout(200) }
  const reset = async () => { await page.evaluate(()=>{ const {state,threads,schedule}=__menu;__menu.picker.close(); state.sheet=null;state.pop=null;state.speakMenu=null;state.tasksOpen=false;threads.st.listMenu=false;if(threads.st.open){ threads.st.open.headerMenu=false;threads.st.open.tasksOpen=false;threads.st.open.configuration=null;threads.st.open.confirmDelete=false;threads.st.open.boardOpen=false;threads.st.open.boardMenu=null }; schedule() }); await settle() }
  const thread = async () => { await reset(); await page.evaluate(async(id)=>{__menu.state.screen='threads';await __menu.threads.open(id);__menu.schedule()},id); await page.locator('.tt-title').waitFor(); await settle() }
  const check = async (name, trigger, surface, {exception=false, close=true}={}) => {
    await surface.waitFor();await settle()
    const a=await trigger.boundingBox(), b=await surface.boundingBox()
    assert.ok(b, `${name} visible`)
    const gap = a ? Math.hypot(Math.max(a.x-b.x-b.width,b.x-a.x-a.width,0),Math.max(a.y-b.y-b.height,b.y-a.y-a.height,0)) : null
    if (!baseline && !exception) {
      assert.ok(b.x>=-.5 && b.y>=-.5 && b.x+b.width<=width+.5 && b.y+b.height<=1000+.5,`${name} in viewport ${JSON.stringify(b)}`)
      const mode=await surface.getAttribute('data-menu-layout')
      assert.equal(mode,width<700?'sheet':'anchored',name)
      if(width<700) { assert.ok(Math.abs(b.x)<1 && Math.abs(b.width-width)<1 && Math.abs(b.y+b.height-1000)<1,`${name} bottom sheet`) }
      else assert.ok(gap<=13,`${name} trigger distance ${gap}`)
      assert.equal(await surface.evaluate(n=>n.contains(document.activeElement)),true,`${name} focused`)
      for(const zoom of (name==='thread-actions' && width===1440 && !collapsed && theme==='light' ? Array.from({length:16},(_,i)=>75+i*5) : [75,150])) {
        await trigger.evaluate((node,zoom)=>{document.documentElement.style.setProperty('--ui-scale',zoom/100);node.scrollIntoView({block:'nearest',inline:'nearest'})},zoom);await settle()
        const x=await trigger.boundingBox(),y=await surface.boundingBox()
        assert.ok(y && y.x>=-1 && y.y>=-1 && y.x+y.width<=width+1 && y.y+y.height<=1001,`${name} zoom ${zoom} viewport: ${JSON.stringify(y)}`)
        if(width<700) assert.ok(Math.abs(y.y+y.height-1000)<1 && Math.abs(y.width-width)<1,`${name} zoom ${zoom} sheet`)
        else {const distance=Math.hypot(Math.max(x.x-y.x-y.width,y.x-x.x-x.width,0),Math.max(x.y-y.y-y.height,y.y-x.y-x.height,0));assert.ok(distance<=13,`${name} zoom ${zoom} gap ${distance}`)}
      }
      await trigger.evaluate(node=>{document.documentElement.style.setProperty('--ui-scale',1);node.scrollIntoView({block:'nearest',inline:'nearest'})});await settle()
      if(name==='read-aloud') assert.equal(await surface.locator('.setrow-sub').evaluateAll(nodes=>nodes.some(n=>n.scrollWidth>n.clientWidth+1)),false,'read-aloud labels wrap')
    }
    if(!baseline && !exception) {
      const editing=await surface.evaluate(n=>document.activeElement?.matches('input,textarea,select,[contenteditable="true"]'))
      if(!editing) {
        await surface.evaluate(n=>{window.__menuPreviousFocus=document.activeElement});await page.keyboard.press('ArrowDown')
        const count=await surface.locator('button:visible:not(:disabled),a[href]:visible,input:visible:not(:disabled)').count()
        if(count>1) assert.equal(await surface.evaluate(n=>document.activeElement!==window.__menuPreviousFocus),true,`${name} arrow moves`)
      }
      await surface.locator('button:visible:not(:disabled),a[href]:visible,input:visible:not(:disabled),textarea:visible:not(:disabled),[tabindex="0"]:visible').last().focus()
      await page.keyboard.press('Tab')
      assert.equal(await surface.evaluate(n=>n.contains(document.activeElement)),true,`${name} Tab stays inside`)
    }
    await page.screenshot({path:path.join(out,`${prefix}-${name}-${stamp}.png`),animations:'disabled'})
    results.push({name,width,collapsed,theme,anchor:a,box:b,gap,exception})
    if(close) { if(baseline || exception) await reset();else {await page.keyboard.press('Escape');await surface.waitFor({state:'hidden'}); await settle();assert.equal(await trigger.evaluate(n=>n===document.activeElement),true,`${name} focus returned`)} }
  }
  try {
    await page.goto(app.url); await page.locator('.tt-row').first().waitFor();await settle()
    if(collapsed) { await page.evaluate(()=>{__menu.state.collapsed=true;__menu.schedule()});await settle() }
    // List actions exist on the list; a collapsed sidebar is temporarily shown to open it.
    if(collapsed) await page.evaluate(()=>{__menu.state.collapsed=false;__menu.schedule()})
    let trigger=page.getByRole('button',{name:'Thread list actions',exact:true});await trigger.click()
    await check('thread-list',trigger,page.locator('.tt-list-menu'))
    if(collapsed) await page.evaluate(()=>{__menu.state.collapsed=true;__menu.schedule()})
    await thread();trigger=page.getByRole('button',{name:'Thread actions',exact:true});await trigger.click()
    await check('thread-actions',trigger,page.locator('.tt-header-menu'))
    await trigger.click();await page.getByRole('button',{name:'Delete thread',exact:true}).click()
    await check('delete-confirm',trigger,page.locator('.tt-header-confirm'))
    trigger=page.getByRole('button',{name:'Providers & models',exact:true});await trigger.click()
    await check('providers-models',trigger,page.locator('.tt-configuration'),{close:false})
    let choice=page.locator('.tt-configuration .tt-model-select').first();await choice.click()
    await check('model-options',trigger,page.locator('.tt-configuration'),{close:false})
    choice=page.locator('.tt-configuration .tt-engine-select').first();await choice.click()
    await check('provider-options',trigger,page.locator('.tt-configuration'))
    trigger=page.locator('.tt-tasks-button');await trigger.click()
    await check('background-tasks',trigger,page.locator('.tt-tasks-panel'),{close:false})
    await page.locator('.tt-task-row').first().click();await check('task-details',trigger,page.locator('.tt-tasks-panel'))
    trigger=page.locator('.tt-message .speakbtn').last();await trigger.click()
    await check('read-aloud',trigger,page.locator('.speakmenu'))
    // Typing and board workspaces are inventoried exceptions, not popup menus.
    trigger=page.locator('.tt-mention-button');await trigger.click();await check('mentions',trigger,page.locator('.tt-mentions'),{exception:true})
    trigger=page.locator('.tt-board-button');await trigger.click();await check('board-controls',trigger,page.locator('.tt-board'),{exception:true})
    await page.locator('.tt-board-button').click();await settle()
    trigger=page.locator('.tt-item-actions > button').first();await trigger.click();await check('board-item',trigger,page.locator('.tt-item-menu'),{close:false})
    await page.getByRole('button',{name:'Move to…',exact:true}).click();await check('board-move',trigger,page.locator('.tt-item-menu'))
    await reset();if(width===390){await page.evaluate(()=>__menu.threads.close());await settle()}
    if(collapsed) { trigger=page.getByRole('button',{name:'Settings',exact:true}).first();await trigger.click(); await page.getByRole('button',{name:'Running now',exact:true}).click() }
    else {trigger=page.locator('.run-indicator').first();await trigger.click()}
    await check('running',trigger,page.locator('.run-sheet'))
    trigger=page.getByRole('button',{name:'Settings',exact:true}).first();await trigger.click();await check('settings',trigger,page.locator('.sheet.settings'),{close:false})
    await page.evaluate(()=>__menu.goSettings('read-aloud'));await settle()
    const voice=page.locator('#files-voice');await voice.click()
    if(baseline) { await page.keyboard.press('Escape'); await check('voice-picker',voice,page.locator('.sheet.settings'),{exception:true,close:false}) }
    else await check('voice-picker',voice,page.locator('.voice-menu'))
    await thread()
    trigger=page.locator('.plusbtn').filter({hasNot:page.locator('.tt-mention-button')}).first();await trigger.click()
    await check('composer-add',trigger,page.locator('.popover:not(.speakmenu), .sheet').last())
    await trigger.click();await page.getByRole('button',{name:/Pick from Files/}).click();await check('files-picker',trigger,page.locator('.fs-picker'))
    assert.deepEqual(errors,[],`page errors ${stamp}`)
    console.log(`${prefix} ${stamp}: ${results.filter(r=>r.width===width&&r.collapsed===collapsed&&r.theme===theme).length} surfaces`)
  } catch(e) { console.error(errors);console.error(await page.locator('[data-menu-surface]').evaluateAll(nodes=>nodes.map(n=>({title:n.dataset.menuTitle,transform:getComputedStyle(n).transform,animations:n.getAnimations().map(a=>({time:a.currentTime,state:a.playState})),top:n.style.top,bottom:n.style.bottom,scale:getComputedStyle(document.documentElement).zoom})))); await page.screenshot({path:path.join(out,`failure-${prefix}-${stamp}.png`)});throw e }
  finally {await ctx.close();await app.close()}
 }
 await writeFile(path.join(out,`${prefix}-browser.json`),JSON.stringify(results,null,2)+'\n')
 const files=(await readdir(out)).filter(n=>n.startsWith(prefix+'-')&&n.endsWith('.png'))
 await writeFile(path.join(out,`${prefix}-captures.json`),JSON.stringify(await Promise.all(files.map(async name=>({name,sha256:createHash('sha256').update(await readFile(path.join(out,name))).digest('hex')}))),null,2)+'\n')
} finally {await browser.close()}
