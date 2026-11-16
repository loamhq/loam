import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { installDom, settle, clearWrites, sinceSettled } from './fakedom.js'
import { blob, morph } from '../public/ui.js'
import { agentLook } from '../public/sessions.js'
import { faceSuppliers, variantFor } from '../public/signature-avatar.js'
import { loadThemes } from '../src/themes.js'
import { appearanceValues } from '../src/appearance.js'
import { optionValues, mergeAppearance } from '../public/theme-options.js'
import { createTheme } from '../public/theme.js'
import { contrast } from '../tools/theme-contrast.mjs'

const ids = ['deep-forest','autumn','yosemite','mist-trail','foundry']
test('material suppliers have eight permanent distinct variants without shared SVG references', () => {
  installDom()
  assert.deepEqual(Array.from({length:8},(_,i)=>variantFor(`sample-${i}`)), [2,5,4,7,6,1,0,3])
  const serialize = el => [el.nodeName, [...el.attributes], el.childNodes.map(serialize)]
  for (const id of ids) {
    const variants = Array.from({length:8},(_,n)=>faceSuppliers[id](n).map(serialize))
    assert.equal(new Set(variants.map(v=>JSON.stringify(v))).size,8,id)
    assert.doesNotMatch(JSON.stringify(variants), /url\(|\b(?:href|id|filter|mask|clip-path)\b/,id)
  }
})
test('shared faces preserve selection offsets, unchanged redraws, and image priority', () => {
  const doc = installDom(), root = doc.createElement('div'), look = agentLook('sample')
  morph(root,[blob(look,{size:22})]); settle(root); clearWrites()
  morph(root,[blob(look,{size:22})]); assert.deepEqual(sinceSettled(),[])
  assert.equal(root.textContent,look.initial)
  assert.equal(root.firstChild.hasAttribute('data-face-small'),true)
  assert.equal(blob(look,{inline:true,decorative:true}).textContent,'')
  const image = blob({...look,imageSrc:'/portrait.png'},{size:44})
  assert.equal(image.firstChild.nodeName.toLowerCase(),'img')
  assert.equal(image.childNodes.length,1)
  assert.equal(image.hasAttribute('data-face-initial'),false)
})
test('saved faces survive simple defaults and signature content uses the existing settings route', async () => {
  const doc = installDom(); doc.documentElement = doc.createElement('html'); doc.querySelector = () => null
  const themes = (await loadThemes()).themes, host = appearanceValues(null,themes), writes=[]
  const request = async (method,patch) => { if(patch){writes.push(patch);for(const kind of Object.keys(patch)) host[kind]=mergeAppearance(host[kind],patch[kind])}return structuredClone(host) }
  for (const id of ids) {
    for (const shape of ['signature','distinct','circle','rounded-square']) assert.equal(optionValues({[id]:{'agent-shapes':shape}},themes)[id]['agent-shapes'],shape)
    const controller = () => createTheme(null,{kind:'phone',boot:{themes,values:structuredClone(host),available:true},request})
    const theme=controller();theme.set({theme:id,options:{[id]:{'agent-shapes':'signature','face-content':'initials'}}});await theme.save()
    assert.equal(host.phone.options[id]['agent-shapes'],'signature')
    assert.equal(writes.at(-1).phone.options[id]['face-content'],'initials')
    assert.equal(controller().value.options[id]['face-content'],'initials')
    assert.equal(host.desktop.options[id]['face-content'],'mark')
  }
})
test('initials have AA against their material and geometry never imports agent pigments', async () => {
  const css=await readFile('public/themes/materials.css','utf8')
  for(const block of css.matchAll(/(:root\[data-look="[^"]+"\](?:\[data-theme="dark"\])?) \{([^}]+)\}/g)) {
    const t=Object.fromEntries([...block[2].matchAll(/--(face-[\w-]+):\s*(#[\dA-Fa-f]+)/g)].map(m=>[m[1],m[2]]))
    if(!t['face-ink']) continue
    for(const [key,value]of Object.entries(t).filter(([k])=>k==='face-body'||k.startsWith('face-leaf'))) assert.ok(contrast(t['face-ink'],value)>=4.5,block[1]+'/'+key)
  }
  assert.doesNotMatch(await readFile('public/signature-avatar.js','utf8'), /currentColor|persona-|look\.color/)
})

test('every material variant differs in tone and keeps AA initials, including granite shadow facets',async()=>{
  const source=await readFile('public/themes/face-paints.css','utf8'),groups=new Map()
  for(const m of source.matchAll(/:root\[data-look="([^"]+)"\]\[data-theme="([^"]+)"\] \.blob\[data-face-variant="(\d)"\] \{([^}]+)\}/g)){
    const values=Object.fromEntries([...m[4].matchAll(/--([\w-]+):\s*(#[\dA-F]+)/g)].map(v=>[v[1],v[2]])),key=m[1]+'/'+m[2]
    const colours=groups.get(key)||new Set();colours.add(values['face-body']);groups.set(key,colours)
    assert.ok(contrast(values['face-ink'],values['face-body'])>=4.5,key+'/'+m[3])
    if(m[1]==='yosemite')assert.ok(contrast(values['face-ink'],values['face-edge'])>=4.5,key+'/'+m[3]+'/facet')
  }
  assert.equal(groups.size,8);for(const colours of groups.values())assert.equal(colours.size,8)
})
