import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import {readFaceVariants,setFaceVariants,variantFor} from '../public/face-identity.js'
import {pourFor,POURS} from '../public/latte-avatar.js'
import {readConfig} from '../src/config.js'
import {fakeBroker,appAgainst,closing} from './harness.js'

test('fixed slots distinguish seven identities, survive appending/reordering, and share Latte identity',t=>{
  t.after(()=>setFaceVariants())
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
test('fixed identities are supplied in the parser-blocking boot before any faces render',async t=>{
  const broker=await fakeBroker({}),app=await appAgainst(broker,{AVATAR_VARIANTS:'{"sample":3,"other":5}'})
  t.after(closing(app,broker))
  const response=await fetch(app.url+'/appearance-state.js'),source=await response.text()
  assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store')
  const context=vm.createContext({});vm.runInContext(source,context)
  assert.equal(context.LOAM_AVATAR_VARIANTS.sample,3)
  assert.equal(context.LOAM_AVATAR_VARIANTS.other,5)
  const html=await(await fetch(app.url)).text()
  assert.ok(html.indexOf('/appearance-state.js')<html.indexOf('/app.js'))
})
