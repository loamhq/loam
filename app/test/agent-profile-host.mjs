import { oneProductFixture } from './one-product-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'
import { readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
export async function profileHost({ appOverrides = {} } = {}) {
  const app = await oneProductFixture({}, appOverrides), writes = [], voices = {}
  const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  const plugins = await shippedPlugins('public'), plugin = plugins.find(p => p.id === 'read-aloud')
  plugin.on = true
  app.routes['GET /v1/files/read'] = (req, res) => json(res, { text: '# Soul\n\nMake space for thoughtful work. Keep the next step clear.' })
  app.routes['GET /v1/plugins'] = (req,res) => json(res,{plugins})
  app.routes['PATCH /v1/plugins/read-aloud'] = (req,res,url,raw) => { plugin.on = JSON.parse(raw).on; json(res,{plugin}) }
  for (const p of plugins) for (const file of (p.has.page_main ? (await readdir(path.join('public',p.folder,'page'))).map(file => `page/${file}`) : ['theme.css'])) app.routes[`GET /v1/plugins/${p.id}/files/${file}`] = async (req,res) => {
    try { const body = await readFile(path.join('public',p.folder,file)); res.writeHead(200,{'content-type':file.endsWith('.js')?'text/javascript':'text/css'});res.end(body) }
    catch { json(res,{error:'Missing fixture file'},404) }
  }
  for (const p of app.f.providers) {
    p.connected = true
    p.models = p.id === 'codex' ? [{id:'code-fast',name:'Code Fast',efforts:['low','high']},{id:'code-deep',name:'Code Deep',efforts:['high','max']}] : [{id:'fable',name:'Fable'},{id:'deep',name:'Deep'}]
    p.efforts = ['low','medium','high','xhigh','max']
  }
  const ref = file => ({root:'spaces',path:`sprout/${file}`,exists:true})
  const schedule = {agent:'sprout',name:'reflection',label:'Nightly wake',plain:'Every day at 3:20 AM',schedule:'daily at 03:20 PT',enabled:true,next_fire:'2026-11-14T11:20:00Z',last_fire:{ts:'2026-11-13T11:20:00Z',outcome:'ran'},file:ref('crons/reflection.md')}
  const profiles = new Map(app.f.agents.map(agent => [agent.id,{
    agent, identity:{id:agent.id,display:agent.name,emoji:agent.emoji},
    defaults:{provider:null,model:null,effort:null,effective:{provider:'claude-code',model:'fable',effort:null},applies:'New threads and members brought in start on this provider. Model and thinking apply from the next turn unless the thread chose its own.',not_honoured:[]},
    files:{workspace:ref(''),soul:ref('soul.md'),config:ref('config.md'),memory:{always:ref('_always.md'),active:ref('_active.md'),reflection:ref('_reflection.md'),folder:ref('memory')},skills:{...ref('skills'),count:4}},
    schedule:[structuredClone(schedule)],status:{threads:[{thread:'a-quieter-week'}],jobs:[{job:'build-notes'}],queued:[],run:null,next_wake:schedule.next_fire,box_read:true},
    plugins:[{id:'read-aloud',name:'Read aloud',on:true,fields:plugin.agent_settings,values:{voice:'Charon'}},{id:'off',name:'Hidden plugin',on:false,fields:plugin.agent_settings,values:{voice:'Kore'}}],
  }]))
  const engineers = {engineers:[{project:'Notebook',platform:false,own:null,effective:{provider:'codex',model:'code-fast',effort:'high'},providers:['claude-code','codex'],deploy_on_land:false,instructions:{root:null,note:'In the project repository; not reachable from Files.'}},
    {project:'Loam',platform:true,own:null,effective:{provider:'claude-code',model:'fable',effort:null},providers:['claude-code'],deploy_on_land:null,instructions:{root:'system',path:'Engineer/Platform project.md'}}],default:{own:null,effective:{provider:'codex',model:'code-fast',effort:'high'},providers:['claude-code','codex'],note:'Every project engineer without its own setting.'},applies:'From the next job; a running job is unchanged.'}
  const reviewer = {id:'reviewer',name:'Nightly reviewer',schedule:'daily at 04:20 PT',plain:'Every day at 4:20 AM',next_fire:'2026-11-14T12:20:00Z',last_fire:'2026-11-13T12:20:00Z',provider:'claude-code',providers:['claude-code'],model:null,effort:null,effective:{provider:'claude-code',model:'fable',effort:null},instructions:{root:'system',path:'Reviewer/CLAUDE.md'},applies:'From the next run.'}
  const tiers = ['quick', 'standard', 'hard']
  const resolveTiers = row => { row.effective.tiers = Object.fromEntries(tiers.map(tier => [tier, { model: row.own?.tiers?.[tier] || row.effective.model, from: row.own?.tiers?.[tier] ? 'tier' : 'model' }])) }
  for (const row of [...engineers.engineers, engineers.default]) resolveTiers(row)
  app.routes['GET /v1/engineers'] = (req,res) => json(res,engineers)
  for (const id of ['Loam','Notebook','default']) {
    const get = () => id === 'default' ? {project:id,...engineers.default,applies:engineers.applies} : {...engineers.engineers.find(r=>r.project===id),applies:engineers.applies}
    app.routes[`GET /v1/engineers/${id}`] = (req,res) => json(res,get())
    app.routes[`PATCH /v1/engineers/${id}`] = (req,res,url,raw) => {
      const b = JSON.parse(raw); writes.push({path:url.pathname,body:b})
      const row = id === 'default' ? engineers.default : engineers.engineers.find(r=>r.project===id)
      if (['provider','model','effort'].some(key => key in b)) { row.own={...row.own,...b}; Object.assign(row.effective,b) }
      if (b.provider) {
        const provider = app.f.providers.find(p => p.id === b.provider)
        Object.assign(row.own, { model:null, effort:null, tiers:Object.fromEntries(tiers.map(tier => [tier,null])) })
        Object.assign(row.effective, { model:provider.models[0].id, effort:null })
      }
      if (b.tiers) row.own = { ...row.own, tiers:{...row.own?.tiers,...b.tiers} }
      resolveTiers(row)
      if ('deploy_on_land' in b) row.deploy_on_land=b.deploy_on_land
      json(res,engineers)
    }
  }
  app.routes['GET /v1/system/reviewer'] = (req,res) => json(res,reviewer)
  app.routes['PATCH /v1/system/reviewer'] = (req,res,url,raw) => {const b=JSON.parse(raw);writes.push({path:url.pathname,body:b});Object.assign(reviewer,b);if(b.schedule) reviewer.plain='Every day at 5:40 AM'; Object.assign(reviewer.effective,b);json(res,reviewer)}
  app.routes['GET /v1/activity'] = (req,res,url) => json(res,{items:[{id:'job-a',kind:'job',agent:'sprout',briefed_by:'sprout',project:'Notebook',title:'A calmer project page',outcome:'completed',landing:'landed',ts:'2026-11-13T20:00:00Z',engine:'codex',model:'code-fast'},{id:'job-other',kind:'job',project:'Other',title:'Another project job',ts:'2026-11-13T19:00:00Z'}].filter(i=>!url.searchParams.get('agent')||i.agent===url.searchParams.get('agent')),jobs:{slots:[],queue:[],landing:[]},total:2,next_offset:null})
  app.routes['GET /v1/schedules'] = (req,res) => json(res,{schedules:[schedule]})
  for (const [id,p] of profiles) {
    app.routes[`GET /v1/agents/${id}`] = (req,res) => json(res,p)
    app.routes[`PATCH /v1/agents/${id}`] = (req,res,url,raw) => {
      const b=JSON.parse(raw);writes.push({path:url.pathname,body:b})
      if(b.display==='Scout') return json(res,{error:'That name belongs to another agent.'},409)
      if(b.display) {p.identity.display=b.display;p.agent.name=b.display}
      if ('emoji' in b) {p.identity.emoji=b.emoji;p.agent.emoji=b.emoji}
      for(const key of ['provider','model','effort']) if(key in b) {p.defaults[key]=b[key];p.defaults.effective[key]=b[key]}
      if(b.provider) {p.defaults.model=null;p.defaults.effort=null;p.defaults.effective.model=null;p.defaults.effective.effort=null;p.defaults.not_honoured=b.provider==='claude-code'?[]:['Wakes and schedules continue on Claude Code.']}
      json(res,p)
    }
    app.routes[`PATCH /v1/schedules/${id}/reflection`] = (req,res,url,raw) => {const b=JSON.parse(raw);writes.push({path:url.pathname,body:b});Object.assign(p.schedule[0],b);if(b.schedule) p.schedule[0].plain='Weekdays at 6:10 AM';json(res,{schedule:p.schedule[0]})}
    app.routes[`GET /v1/plugins/read-aloud/agents/${id}`] = (req,res) => json(res,{plugin:'read-aloud',on:true,fields:plugin.agent_settings,values:{voice:voices[id]||'Charon'},stored:voices[id]?{voice:voices[id]}:{}})
    app.routes[`PATCH /v1/plugins/read-aloud/agents/${id}`] = (req,res,url,raw) => {const b=JSON.parse(raw);writes.push({path:url.pathname,body:b});voices[id]=b.values.voice;p.plugins[0].values={voice:b.values.voice};json(res,{plugin:'read-aloud',values:p.plugins[0].values})}
  }
  return {...app,writes,voices,profiles,engineers,reviewer}
}
