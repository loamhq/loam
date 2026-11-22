import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import {readFaceVariants,readFaceSlots,setFaceVariants,setFaceSlots,recordFaceSlots,variantFor} from '../public/face-identity.js'
import {pourFor,POURS} from '../public/latte-avatar.js'
import {signatureArt} from '../public/signature-avatar.js'
import {blob} from '../public/ui.js'
import {createAgents,agentLook} from '../public/sessions.js'
import {installDom} from './fakedom.js'
import {readConfig} from '../src/config.js'
import {fakeBroker,appAgainst,closing} from './harness.js'

test('fixed slots distinguish seven identities, survive appending/reordering, and share Latte identity',t=>{
  t.after(()=>{setFaceVariants();setFaceSlots()})
  const ids=Array.from({length:7},(_,i)=>`persona-${i}`), slots=Object.fromEntries(ids.map((id,i)=>[id,i]))
  setFaceVariants(slots)
  assert.deepEqual(ids.map(variantFor),[0,1,2,3,4,5,6])
  assert.deepEqual(ids.map(id=>pourFor(id)[0]),POURS.slice(0,7).map(p=>p[0]))
  setFaceVariants({...Object.fromEntries(Object.entries(slots).reverse()),extra:7})
  assert.deepEqual(ids.map(variantFor),[0,1,2,3,4,5,6])
  setFaceVariants(); const before=variantFor('unknown')
  setFaceVariants(slots);assert.equal(variantFor('unknown'),before)
})
test('mapping rejects reused, fractional, missing and out-of-range slots; object-like IDs are safe',()=>{
  for(const input of [[],null,1,{a:0,b:0},{a:8},{a:-1},{a:1.5},{a:true},{a:'1'}])assert.throws(()=>readFaceVariants(input),/AVATAR_VARIANTS/)
  const map=readFaceVariants('{"__proto__":0,"constructor":1}')
  assert.equal(Object.getPrototypeOf(map),Object.prototype)
  assert.equal(map.__proto__,0);assert.equal(map.constructor,1)
  assert.deepEqual(readConfig({AVATAR_VARIANTS:'{"sample":3}'}).avatarVariants,{sample:3})
})
test('fixed identities and broker face slots are supplied in the parser-blocking boot before any faces render',async t=>{
  const json=(res,body)=>{res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(body))}
  const broker=await fakeBroker({
    'GET /v1/agents':(req,res)=>json(res,{agents:[{id:'iris',name:'Iris',face_slot:6},{id:'juniper',name:'Juniper',face_slot:0}]}),
  }),app=await appAgainst(broker,{AVATAR_VARIANTS:'{"sample":3,"other":5}'})
  t.after(closing(app,broker))
  const response=await fetch(app.url+'/appearance-state.js'),source=await response.text()
  assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store')
  const context=vm.createContext({});vm.runInContext(source,context)
  assert.equal(context.LOAM_AVATAR_VARIANTS.sample,3)
  assert.equal(context.LOAM_AVATAR_VARIANTS.other,5)
  assert.equal(context.LOAM_FACE_SLOTS.iris,6)
  assert.equal(context.LOAM_FACE_SLOTS.juniper,0)
  const html=await(await fetch(app.url)).text()
  assert.ok(html.indexOf('/appearance-state.js')<html.indexOf('/app.js'))
})
test('stored face_slots prevent collisions across Latte and all signature themes, honour AVATAR_VARIANTS, support arbitrary face counts, and yield to uploaded images',t=>{
  t.after(()=>{setFaceVariants();setFaceSlots()})
  installDom()
  // sprout and juniper collide under FNV-1a mod 8 (both hash to 0),
  // yet distinct broker face_slots separate them everywhere.
  assert.equal(variantFor('sprout'),variantFor('juniper'))
  const nine=Array.from({length:9},(_,i)=>({id:`a-${i}`,name:`Agent ${i}`,face_slot:i}))
  const agents=createAgents(nine)
  recordFaceSlots(nine)

  // Four agents never collide; eight fill all eight; ninth repeats evenly.
  assert.equal(new Set(nine.slice(0,4).map(a=>variantFor(a.id))).size,4)
  assert.deepEqual(nine.slice(0,8).map(a=>variantFor(a.id)),[0,1,2,3,4,5,6,7])
  assert.equal(variantFor(nine[8].id),0)

  // Different theme face counts work from their own count:
  // 5-face theme: first 5 distinct, 6th..9th repeat evenly (0,1,2,3).
  assert.deepEqual(nine.map(a=>variantFor(a.id,5)),[0,1,2,3,4,0,1,2,3])
  // 10-face theme: all 9 distinct (including the 9th agent with slot 8).
  assert.deepEqual(nine.map(a=>variantFor(a.id,10)),[0,1,2,3,4,5,6,7,8])

  // Deleting/reordering/renaming another agent changes nobody else.
  const withoutOne=createAgents(nine.filter((_,i)=>i!==2).reverse().map(a=>({...a,name:`Renamed ${a.id}`})))
  for(const a of withoutOne.list){
    assert.equal(variantFor(a.id,{slot:agentLook(a.id,withoutOne).faceSlot}),a.faceSlot%8)
  }

  // Latte and all 5 signature themes read the stored slot through blob().
  const look=agentLook('a-5',agents)
  const el=blob(look,{size:44,font:19})
  assert.equal(el.getAttribute('data-face-variant'),'5')
  assert.equal(pourFor('a-5',look.faceSlot)[0],POURS[5][0])
  assert.equal(signatureArt('a-5',look.faceSlot).getAttribute('data-variant'),'5')

  // AVATAR_VARIANTS still wins over stored face_slot.
  setFaceVariants({'a-5':2})
  assert.equal(variantFor('a-5',{slot:5}),2)
  assert.equal(blob(look,{size:44,font:19}).getAttribute('data-face-variant'),'2')
  setFaceVariants()

  // Uploaded image (agent.face) still wins over all of this.
  const withImage=createAgents([{id:'a-5',name:'Agent 5',face_slot:5,face:{url:'/v1/agents/a-5/face?v=123'}}])
  const imgBlob=blob(agentLook('a-5',withImage),{size:44,font:19})
  assert.equal(imgBlob.firstChild.nodeName.toLowerCase(),'img')
  assert.equal(imgBlob.hasAttribute('data-face-variant'),false)
  assert.equal(readFaceSlots([{id:'x',face_slot:4},{id:'bad',face_slot:-1}]).x,4)
})
