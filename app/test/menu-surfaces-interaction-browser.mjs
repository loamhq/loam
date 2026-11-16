import assert from 'node:assert/strict'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { oneProductFixture } from './one-product-fixture.mjs'
const {chromium}=await import(process.env.LOAM_PLAYWRIGHT||'playwright')
const browser=await chromium.launch({executablePath:process.env.LOAM_CHROME,args:['--no-sandbox']})
const app=await oneProductFixture(), checks=[]
const source=await readFile(new URL('../public/app.js',import.meta.url),'utf8')
const out=process.env.MENU_EVIDENCE
try {
 for(const width of (process.env.MENU_INTERACTION_WIDTH ? [Number(process.env.MENU_INTERACTION_WIDTH)] : [390,820,1440])) {
  const ctx=await browser.newContext({viewport:{width,height:1000},hasTouch:width<1000,isMobile:width===390})
  await ctx.route('**/app.js',r=>r.fulfill({contentType:'text/javascript',body:source+'\nglobalThis.__interaction={threads,state,schedule};'}))
  const page=await ctx.newPage();page.setDefaultTimeout(10000)
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  const settle=async()=>{await page.waitForTimeout(300);await page.evaluate(()=>new Promise(requestAnimationFrame))}
  const near=async(trigger,menu)=>{
   await settle();const a=await trigger.boundingBox(),b=await menu.boundingBox(),w=page.viewportSize().width
   assert.ok(b.x>=-1&&b.y>=-1&&b.x+b.width<=w+1&&b.y+b.height<=1001,JSON.stringify(b))
   if(w<700) assert.ok(Math.abs(b.y+b.height-1000)<1)
   else assert.ok(Math.hypot(Math.max(a.x-b.x-b.width,b.x-a.x-a.width,0),Math.max(a.y-b.y-b.height,b.y-a.y-a.height,0))<=13,JSON.stringify({a,b,title:await menu.getAttribute('data-menu-title')}))
  }
  try {
   await page.goto(app.url);await page.locator('.tt-row').first().waitFor()
   await page.evaluate(()=>__interaction.threads.open('a-quieter-week'));await settle()
   const trigger=page.getByRole('button',{name:'Thread actions',exact:true}), menu=page.locator('.tt-header-menu')
   await trigger.focus();await page.keyboard.press('Enter');await menu.waitFor();await near(trigger,menu)
   assert.equal(await menu.getByRole('button',{name:'Thread details',exact:true}).evaluate(n=>n===document.activeElement),true)
   await page.keyboard.press('ArrowDown');assert.equal(await menu.getByRole('button',{name:'Pin thread',exact:true}).evaluate(n=>n===document.activeElement),true)
   await page.keyboard.press('Escape');await menu.waitFor({state:'hidden'});assert.equal(await trigger.evaluate(n=>n===document.activeElement),true)
   await trigger.click();await menu.waitFor();await page.mouse.click(3,500);await menu.waitFor({state:'hidden'})
   // A live redraw preserves the actual surface and its animation/focus.
   await trigger.click();await menu.waitFor();await settle()
   await menu.evaluate(n=>window.__heldMenu=n)
   await page.evaluate(()=>__interaction.schedule());await settle()
   assert.equal(await menu.evaluate(n=>n===window.__heldMenu),true)
   assert.equal(await menu.evaluate(n=>n.getAnimations().every(a=>a.playState==='finished')),true)
   // Resize crosses both the shell and app layout breakpoints.
   for(const next of [1920,640,1440,width]) {console.log(`Resize ${width} → ${next}`);await page.setViewportSize({width:next,height:1000});await near(trigger,menu)}
   await page.keyboard.press('Escape')
   if(width===1440) {
    await trigger.click();await page.getByRole('button',{name:'Hide sidebar',exact:true}).click({force:true}).catch(async()=>{
      // The outside click correctly closes the menu before collapsing. Open
      // it again after the layout transition and check the same header anchor.
      await page.evaluate(()=>{__interaction.state.collapsed=true;__interaction.schedule()})
    });await settle()
    if(await menu.count()===0) await trigger.click()
    await near(trigger,menu);await page.keyboard.press('Escape')
   }
   // The provider control also opens with a keyboard, with no prior click.
   const model=page.getByRole('button',{name:'Providers & models',exact:true})
   await model.focus();await page.keyboard.press('Enter');await page.locator('.tt-configuration').waitFor();await near(model,page.locator('.tt-configuration'));await page.keyboard.press('Escape')
   await page.emulateMedia({reducedMotion:'reduce'})
   await trigger.click();await menu.waitFor();assert.equal(await menu.evaluate(n=>getComputedStyle(n).animationName),'menuFade');await page.keyboard.press('Escape')
   // A synthetic scroll host uses the same real component to exercise trigger
   // movement and edge flipping independent of sticky application headers.
   const measured=await page.evaluate(async()=>{
     const {h}=await import('/ui.js'),{menuSurface,createMenuSurfaces}=await import('/menu-surface.js')
     const root=h('div'), scroller=h('div'), inner=h('div'), button=h('button',{text:'Scroll anchor'}), panel=menuSurface(h('div',{},h('button',{text:'Choice'})),{title:'Scroll test',close:()=>panel.remove()})
     scroller.style.cssText='position:fixed;left:80px;top:60px;width:200px;height:300px;overflow:auto;z-index:100'
     inner.style.height='800px';button.style.marginTop='200px';inner.append(button);scroller.append(inner);root.append(scroller);document.body.append(root)
     const surfaces=createMenuSurfaces(root);button.click();root.append(panel);surfaces.sync()
     await new Promise(requestAnimationFrame);const before=panel.getBoundingClientRect().top
     scroller.scrollTop=80;await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);const after=panel.getBoundingClientRect().top
     const layout=panel.dataset.menuLayout;scroller.scrollTop=400;await new Promise(resolve=>setTimeout(resolve,300));const closed=!panel.isConnected
     root.remove();return {before,after,layout,closed}
   })
   if(width>=700){assert.ok(Math.abs(measured.before-measured.after-80)<2);assert.equal(measured.closed,true)}
   assert.deepEqual(errors,[]);checks.push({width,keyboard:true,outside:true,resize:true,redraw:true,reducedMotion:true,scroll:measured})
   console.log(`Interaction checks passed at ${width}`)
  } finally {await ctx.close()}
 }
 if(out){await mkdir(out,{recursive:true});await writeFile(`${out}/interactions.json`,JSON.stringify(checks,null,2)+'\n')}
} finally {await browser.close();await app.close()}
