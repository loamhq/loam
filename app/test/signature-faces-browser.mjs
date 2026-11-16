// Material specimen sheets use the actual shared blob, not a second renderer.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.FACE_EVIDENCE || path.join(process.env.TMPDIR,'material-faces')
await mkdir(out,{recursive:true})
const app=await oneProductFixture(), values={phone:{},desktop:{}}, rows=(await shippedPlugins('public')).filter(r=>r.kind.includes('theme'))
const json=(res,body)=>{res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(body))}
app.routes['GET /v1/settings/appearance']=(req,res)=>json(res,values)
app.routes['GET /v1/plugins']=(req,res)=>json(res,{plugins:rows})
const browser=await chromium.launch({executablePath:process.env.LOAM_CHROME,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']})
const reports=[]
try {
  for(const look of ['deep-forest','autumn','yosemite','mist-trail','foundry']) for(const mode of ['light','dark']) {
    values.desktop={theme:look,mode,size:100}
    const context=await browser.newContext({viewport:{width:1000,height:650},reducedMotion:'reduce',colorScheme:mode})
    try {
      const page=await context.newPage(), errors=[];page.on('pageerror',e=>errors.push(e.message))
      await page.goto(app.url);await page.locator('.tt-row').first().waitFor()
      for(const content of ['mark','initials']) {
        await page.evaluate(async({look,mode,content})=>{
          const {blob,h}=await import('/ui.js'), {agentLook}=await import('/sessions.js'), {variantFor}=await import('/signature-avatar.js')
          document.documentElement.setAttribute('data-option-agent-shapes','signature')
          document.documentElement.setAttribute('data-option-face-content',content)
          const samples=Array(8).fill(null);for(let i=0;samples.some(s=>!s);i++){const id=`sample-${i}`;samples[variantFor(id)]=id;if(i>1000)throw Error('Missing variants')}
          const box=h('section',{'data-specimens':'',style:{position:'fixed',inset:'0',zIndex:'99999',background:'var(--paper-page)',color:'var(--paper-ink)',padding:'24px',fontFamily:'var(--font-body)',display:'flex',flexDirection:'column',gap:'24px'}},
            h('div',{style:{display:'flex',alignItems:'center',gap:'24px'}},h('img',{src:`/themes/${look}/cover${look==='autumn'&&mode==='dark'?'-dark':''}.webp`,style:{width:'250px',height:'160px',objectFit:'cover',borderRadius:'8px'}}),
              h('div',{},h('h1',{class:'display',text:rowsName(look)}),h('p',{text:`${mode} · ${content} · 44 / 32 / 22 px`}),h('p',{text:'Eight permanent identity variants'}))),
            ...[44,32,22].map(size=>h('div',{style:{display:'grid',gridTemplateColumns:'repeat(8,1fr)',gap:'16px',alignItems:'center',minHeight:'90px'}},...samples.map(id=>h('div',{style:{display:'flex',flexDirection:'column',alignItems:'center',gap:'12px'}},blob({...agentLook(id),initial:'A'},{size,font:size*.43}),h('span',{text:String(variantFor(id)+1).padStart(2,'0'),style:{fontSize:'11px',color:'var(--paper-ink-2)'}}))))))
          function rowsName(id){return id.split('-').map(p=>p[0].toUpperCase()+p.slice(1)).join(' ')}
          document.querySelector('[data-specimens]')?.remove();document.body.append(box)
          await document.fonts.ready
          await Promise.all([...box.querySelectorAll('img')].map(n=>n.decode()))
        },{look,mode,content})
        const facts=await page.locator('[data-specimens]').evaluate((el,{look,content})=>{
          const faces=[...el.querySelectorAll('.blob')]
          return {count:faces.length,ids:el.querySelectorAll('svg [id],svg [href],svg [filter],svg [mask],svg [clip-path]').length,
            materials:faces.every(f=>getComputedStyle(f.querySelector(`[data-material="${look}"]`)).display!=='none'),
            detail:faces.filter(f=>f.hasAttribute('data-face-small')).every(f=>[...f.querySelectorAll('.face-detail')].every(n=>getComputedStyle(n).display==='none')),
            initials:faces.every(f=>content==='mark'?getComputedStyle(f,'::after').content==='none':getComputedStyle(f,'::after').content==='"A"')}
        },{look,content})
        assert.deepEqual(facts,{count:24,ids:0,materials:true,detail:true,initials:true})
        if(content==='initials'&&['mist-trail','deep-forest','yosemite','autumn'].includes(look)) assert.equal(await page.locator('[data-specimens]').evaluate((el,look)=>[...el.querySelectorAll(`[data-material="${look}"] .face-detail, [data-material="${look}"] .face-mark`)].every(n=>getComputedStyle(n).display==='none'),look),true)
        await page.screenshot({path:path.join(out,`${look}-${mode}-${content}.png`),animations:'disabled'})
        reports.push({look,mode,content,...facts})
      }
      assert.deepEqual(errors,[])
      console.log(`PASS ${look} ${mode}: eight variants at 44/32/22px, marks/initials, no SVG references, small detail removed`)
    } finally {await context.close()}
  }
  await writeFile(path.join(out,'checks.json'),JSON.stringify(reports,null,2)+'\n')
} finally {await browser.close();await app.close()}
