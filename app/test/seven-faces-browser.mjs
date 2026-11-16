// Seven installation-independent stand-ins exercise the supplied fixed slots.
// Actual installation IDs are runtime configuration and stay out of evidence.
import assert from 'node:assert/strict'
import {readFile,writeFile,mkdir} from 'node:fs/promises'
import path from 'node:path'
import {oneProductFixture} from './one-product-fixture.mjs'
import {shippedPlugins} from '../src/shipped-plugins.js'
import {inspectColourFamilies} from '../tools/colour-families.mjs'
const inspect=Function(`return (${inspectColourFamilies.toString().replace("if (document.documentElement.dataset.look !== 'latte') return []",'')})`)()
const {chromium}=await import(process.env.LOAM_PLAYWRIGHT||'playwright')
const fixed=Date.parse('2026-11-13T22:15:00Z'),Original=Date
globalThis.Date=class extends Original {constructor(...args){super(...(args.length?args:[fixed]))}static now(){return fixed}}
const out=process.env.FACE_GRID_EVIDENCE||path.join(process.env.TMPDIR,'seven-faces')
await mkdir(out,{recursive:true})
const app=await oneProductFixture(),rows=(await shippedPlugins('public')).filter(r=>r.kind.includes('theme'))
app.f.agents.push({id:'brook',name:'Brook',emoji:null},{id:'fern',name:'Fern',emoji:null})
const slots=[1,0,7,3,6,4,5],mapping=Object.fromEntries(app.f.agents.map((a,i)=>[a.id,slots[i]]))
app.config.avatarVariants=mapping
const values={phone:{},desktop:{}},json=(res,body)=>{res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(body))}
app.routes['GET /v1/settings/appearance']=(req,res)=>json(res,values)
app.routes['GET /v1/plugins']=(req,res)=>json(res,{plugins:rows})
const browser=await chromium.launch({executablePath:process.env.LOAM_CHROME,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']})
const source=await readFile('public/app.js','utf8'),reports=[]
try{
 for(const width of [390,1440])for(const mode of ['light','dark'])for(const look of (process.env.THEME_LOOKS||'mist-trail,deep-forest,yosemite,autumn,foundry').split(',')){
  const kind=width===390?'phone':'desktop';values[kind]={theme:look,mode,size:100}
  const context=await browser.newContext({viewport:{width,height:width===390?844:1000},isMobile:width===390,hasTouch:width===390,colorScheme:mode,reducedMotion:'reduce'})
  try{
   await context.addInitScript(({fixed})=>{const Old=Date;globalThis.Date=class extends Old{constructor(...args){super(...(args.length?args:[fixed]))}static now(){return fixed}}},{fixed})
   await context.route('**/app.js',r=>r.fulfill({contentType:'text/javascript',body:source+'\nglobalThis.__grid={threads,goSettings};'}))
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message))
   const capture=async label=>{
    await page.mouse.move(width-1,1);await page.evaluate(async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)})
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
    assert.deepEqual((await page.evaluate(inspect)).filter(v=>v.classes!=='theme-choice-name'),[],`${look}/${mode}/${label}`)
    await page.screenshot({path:path.join(out,`${look}-${width}-${mode}-${label}.png`),animations:'disabled'})
   }
   await page.goto(app.url);await page.locator('.strip .persona').nth(6).waitFor()
   const faces=await page.locator('.strip .persona .blob').evaluateAll((faces,look)=>faces.map(f=>({
    slot:Number(f.dataset.faceVariant),tone:getComputedStyle(f).getPropertyValue('--face-body').trim(),
    silhouette:f.querySelector(`[data-material="${look}"] [data-silhouette]`)?.getAttribute('d'),
    pour:f.querySelector('.latte-art').dataset.pour,
   })),look)
   assert.deepEqual(faces.map(f=>f.slot),slots)
   assert.equal(new Set(faces.map(f=>f.slot)).size,7)
   assert.equal(new Set(faces.map(f=>f.pour)).size,7)
   if(['mist-trail','deep-forest','yosemite','autumn'].includes(look)){
    assert.equal(new Set(faces.map(f=>f.tone)).size,7)
    assert.equal(new Set(faces.map(f=>f.silhouette)).size,7)
   }
   await capture('grid')
   await page.evaluate(()=>__grid.threads.open('a-quieter-week'))
   await page.waitForFunction(()=>!__grid.threads.st.open.loading&&!__grid.threads.st.open.refreshing)
   await page.locator('.tt-message').first().waitFor();await capture('thread')
   const panes=await page.locator('.pane').evaluateAll(nodes=>nodes.map(n=>getComputedStyle(n).borderRadius))
   if(look==='foundry'&&width===1440)assert.ok(panes.length&&panes.every(r=>r==='14px'),panes.join(','))
   await page.getByRole('button',{name:'Board',exact:true}).click();await page.locator('.tt-board-item').first().waitFor();await capture('board')
   if(look==='foundry')assert.equal(await page.locator('.tt-board').evaluate(n=>getComputedStyle(n).borderRadius),'14px')
   if(look==='foundry')assert.equal(await page.locator('.tt-board-item').first().evaluate(n=>getComputedStyle(n).borderRadius),'8px')
   assert.deepEqual(errors,[]);reports.push({look,width,mode,faces,panes,errors})
   console.log(`PASS ${look} ${width} ${mode}: seven unique fixed slots, grid/thread/board, contrast and pane corners`)
  }finally{await context.close()}
 }
 await writeFile(path.join(out,'checks.json'),JSON.stringify({browser:browser.version(),reports},null,2)+'\n')
}finally{await browser.close();await app.close()}
