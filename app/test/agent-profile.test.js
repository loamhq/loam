import test from 'node:test'
import assert from 'node:assert/strict'
import { once } from 'node:events'
import { createProfiles, profileStatus } from '../public/agent-profile.js'
import { createApp } from '../src/server.js'
import { readConfig } from '../src/config.js'
import { fakeBroker, appAgainst, closing, TOKEN } from './harness.js'
import { listedManifest } from './audio-plugins.mjs'
import { readAloudApp } from './audio-servers.mjs'
import { readAloudConfig } from './key-fixture.js'
import { agentVoiceFrom } from '../../plugins/_listed/read-aloud/server/app.js'
import { PREBUILT_VOICES } from '../../plugins/_listed/read-aloud/page/document-speech.js'
import { createActivity } from '../public/activity.js'
import { installDom } from './fakedom.js'
import { h } from '../public/ui.js'
import { text, all } from './browser.js'

test('all profile relays preserve bearer, by, encoded ids, error sentences and no-store', async t => {
  const paths=['/agents/test agent','/engineers','/engineers/Project name','/system/reviewer','/plugins/test-plugin/agents','/plugins/test-plugin/agents/test-agent']
  const broker=await fakeBroker(new Proxy({}, {get:()=> (req,res,url,raw)=>{res.writeHead(req.method==='PATCH'?409:200,{'content-type':'application/json'});res.end(raw||'{"ok":true}')}}))
  const app=await appAgainst(broker);t.after(closing(app,broker))
  for (const path of paths) for (const method of ['GET','PATCH']) {
    const body={display:'Taken',by:'test-agent'}
    const response=await fetch(app.url+'/api'+path,{method,headers:{'content-type':'application/json'},...(method==='PATCH'?{body:JSON.stringify(body)}:{})})
    assert.equal(response.status,method==='PATCH'?409:200)
    assert.equal(response.headers.get('cache-control'),'no-store')
    const seen=broker.seen.at(-1);assert.equal(seen.path,'/v1'+path);assert.equal(seen.auth,`Bearer ${TOKEN}`)
    if(method==='PATCH') assert.deepEqual(JSON.parse(seen.body),body)
  }
})

test('reviewer instructions open in Settings even though the System folder is hidden', async t => {
  let present = true
  const broker = await fakeBroker({ 'GET /v1/files/read': (req, res, url) => {
    assert.equal(url.searchParams.get('root'), 'system')
    assert.equal(url.searchParams.get('path'), 'Reviewer/CLAUDE.md')
    res.writeHead(present ? 200 : 404, { 'content-type': 'application/json' })
    res.end(JSON.stringify(present ? { text: 'Read one workspace.' } : { error: 'missing' }))
  } })
  const app = await appAgainst(broker)
  t.after(closing(app, broker))
  const read = () => fetch(app.url + '/api/system/reviewer/instructions?path=elsewhere')
  assert.equal((await fetch(app.url + '/api/files/stat?root=system&path=Reviewer%2FCLAUDE.md')).status, 404)
  assert.deepEqual(await (await read()).json(), { text: 'Read one workspace.' })
  present = false
  const gone = await read()
  assert.equal(gone.status, 200)
  assert.deepEqual(await gone.json(), { text: null })
})

test('late profile loads and writes never replace a newly opened profile; conflicts preserve edit', async () => {
  let answerLoad,answerWrite,fail=false
  const changes=[]
  const api=async(method,path)=>{
    if(method==='GET'&&path==='/agents/first') return new Promise(resolve=>answerLoad=resolve)
    if(method==='PATCH') {
      if(fail) throw Object.assign(new Error('That name belongs to another agent.'),{status:409})
      return new Promise(resolve=>answerWrite=resolve)
    }
    return {agent:{id:'second'},identity:{display:'Second'}}
  }
  const c=createProfiles({api,redraw(){},changedAgent:a=>changes.push(a)})
  const first=c.load('agent','first');await c.load('agent','second');answerLoad({agent:{id:'first'}});await first
  assert.equal(c.st.data.agent.id,'second')
  c.st.edit={field:'display',value:'Taken'};fail=true
  assert.equal(await c.patch({display:'Taken'}),false)
  assert.equal(c.st.edit.value,'Taken');assert.equal(c.st.error,'That name belongs to another agent.')
  fail=false;const write=c.patch({display:'Renamed'});await c.load('agent','third');answerWrite({agent:{id:'second',name:'Renamed'}});await write
  assert.equal(c.st.target.id,'third');assert.equal(c.st.data.identity.display,'Second');assert.equal(changes[0].name,'Renamed')
})

test('a schedule refresh that settles after navigation cannot overwrite the new profile', async () => {
  let finishRefresh, reads = 0
  const c = createProfiles({ redraw() {}, api: async (method, path) => {
    if (method === 'PATCH') return { schedule: {} }
    if (path === '/agents/first' && ++reads === 2) return new Promise(resolve => { finishRefresh = resolve })
    return { agent: { id: path.split('/').at(-1) } }
  } })
  await c.load('agent', 'first')
  const pending = c.patch({ enabled: false }, '/schedules/first/reflection')
  await new Promise(resolve => setImmediate(resolve))
  await c.load('agent', 'second')
  finishRefresh({ agent: { id: 'first' } }); await pending
  assert.equal(c.st.data.agent.id, 'second')
  assert.equal(c.st.busy, false)
})

test('plugin fields and engineer patch responses update from their authoritative answer', async () => {
  const c=createProfiles({redraw(){},api:async(method,path,body)=>{
    if(method==='GET') return {plugins:[{id:'voice-plugin',values:{voice:'Kore'}}]}
    if(path.startsWith('/plugins')) return {plugin:'voice-plugin',values:{voice:'Puck'}}
    return {engineers:[{project:'Notebook',effective:{provider:'codex'}}],default:{},applies:'From the next job.'}
  }})
  await c.load('agent','test');await c.patch({values:{voice:'Puck'}},'/plugins/voice-plugin/agents/test')
  assert.equal(c.st.data.plugins[0].values.voice,'Puck')
  await c.load('engineer','Notebook');await c.patch({provider:'codex'})
  assert.equal(c.st.data.effective.provider,'codex');assert.equal(c.st.data.applies,'From the next job.')
})

test('speech requests and lookups use stored plugin voice, with legacy and document fallbacks', async t => {
  let stored={voice:'Puck'},on=true
  const broker=await fakeBroker({'GET /v1/plugins/read-aloud/agents/test-agent':(req,res)=>{res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify({on,stored,values:{voice:'Charon'}}))}})
  const fs=await import('node:fs/promises'),os=await import('node:os'),path=await import('node:path')
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'voice-token-')),tokenFile=path.join(dir,'token');await fs.writeFile(tokenFile,TOKEN+'\n')
  t.after(()=>fs.rm(dir,{recursive:true,force:true}))
  // Read aloud's own server asks the broker, as the page reads it (1.1.0).
  const calls=[],speech={request:async body=>(calls.push(body),{status:200,body}),peek:async body=>(calls.push(body),{status:200,body}),stop(){}}
  const app=await readAloudApp({config:readAloudConfig({}),speech,agentVoice:agentVoiceFrom({brokerUrl:broker.url,brokerTokenFile:tokenFile})})
  t.after(closing(app,broker))
  const call=async(route,body)=>{const r=await fetch(`${app.url}${route}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});assert.equal(r.status,200);return r.json()}
  for(const route of ['/speech','/speech/lookup']) assert.equal((await call(route,{agent:'test-agent',voice:'Kore'})).voice,'Puck')
  stored={};assert.equal((await call('/speech',{agent:'test-agent'})).voice,undefined)
  on=false;stored={voice:'Puck'};assert.equal((await call('/speech',{agent:'test-agent',voice:'Kore'})).voice,'Kore')
  assert.equal((await call('/speech',{source:'document',agent:'test-agent',voice:'Aoede'})).voice,'Aoede')
  assert.equal(calls.length,5)
  assert.ok(broker.seen.every(r=>r.auth===`Bearer ${TOKEN}`))
})

test('read-aloud manifest declares precisely the supported voices through the ordinary profile place', async () => {
  const p=await listedManifest('read-aloud')
  assert.ok(p.places.includes('agent.profile'))
  assert.deepEqual(p.agent_settings[0].choices.map(c=>c.id),PREBUILT_VOICES)
  assert.equal(p.agent_settings[0].default,'Charon')
})

test('Recent limits the combined feed and engineer jobs remain filtered by project', async () => {
  installDom()
  const c=createActivity({redraw(){},agents:()=>[],avatar:()=>h('span'),api:async()=>({items:[{id:'a',kind:'job',project:'A',title:'First job',ts:'2026-11-13T00:00:00Z'},{id:'b',kind:'job',project:'B',title:'Other job',ts:'2026-11-12T00:00:00Z'}],jobs:{slots:[],queue:[],landing:[]},next_offset:null})})
  await c.load()
  assert.equal(all(c.view({embedded:true,limit:1}),'activity-item').length,1)
  const view=c.view({embedded:true,project:'B'});assert.match(text(view),/Other job/);assert.doesNotMatch(text(view),/First job/)
  assert.match(profileStatus({threads:[{},{}],box_read:false}),/Working in 2 threads · Jobs unavailable/)
})

test('compact Recent caps a mixed feed at four consistent rows and preserves chat, receipt and detail actions', async () => {
  installDom()
  const opened = [], receipt = { root: 'vault', path: 'Receipts/compact-job.md' }
  const jobs = Array.from({ length: 5 }, (_, i) => ({ id: `job-${i}`, kind: 'job', project: 'Notebook', title: `A useful job ${i}`, outcome: 'completed', landing: 'landed',
    ts: `2026-11-13T12:0${5 - i}:00Z`, ...(i === 0 ? { receipt } : {}) }))
  const c = createActivity({ redraw() {}, agents: () => [], avatar: () => h('span'), openRef: ref => opened.push(ref), openChat: chat => opened.push(chat.id),
    chats: () => ({ pinned: [], recent: [{ id: 'thread-a', title: 'The next step', last: { at: '2026-11-13T12:10:00Z' } }] }),
    chatFace: () => h('span'), chatInfo: () => ({ status: 'Working', secondary: 'Full activity preview' }),
    api: async () => ({ items: jobs, jobs: { slots: [], queue: [], landing: [] }, next_offset: null }) })
  await c.load()
  const compact = c.view({ compact: true, limit: 20 }), rows = all(compact, 'profile-recent-row')
  assert.equal(rows.length, 4)
  assert.equal(all(compact, 'activity-day-title').length, 0)
  assert.equal(all(compact, 'activity-job-context').length, 0)
  assert.equal(all(compact, 'profile-recent-time').length, 4)
  assert.match(text(rows[0]), /The next stepWorking/)
  assert.match(text(rows[1]), /Engineer job · landed/)
  rows[0].fire('click', { currentTarget: rows[0] })
  rows[1].fire('click', { currentTarget: rows[1] })
  rows[2].fire('click', { currentTarget: rows[2] })
  assert.deepEqual(opened, ['thread-a', receipt])
  assert.equal(c.st.detail.item.id, 'job-1')
  const full = c.view({ embedded: true })
  assert.equal(all(full, 'activity-item').length, 6)
  assert.ok(all(full, 'activity-day-title').length > 0)
  assert.ok(all(full, 'activity-job-context').length > 0)
})
