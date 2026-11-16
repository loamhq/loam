// Rebuild/verify the retained review index using only Node and PNG headers.
import assert from 'node:assert/strict'
import { readFile, writeFile, readdir, rm } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'
const dir = 'docs/evidence/settings-activity-review'
const verify = process.argv.includes('--verify')
const json = file => readFile(path.join(dir, file), 'utf8').then(JSON.parse)
const before = await json('before/browser.json'), after = await json('after/browser.json')
const slots = ['390-latte-light', '390-latte-dark', '390-plain-light', '1440-latte-light', '1440-latte-dark', '1440-plain-light']
const key = row => `${row.width}-${row.theme}-${row.mode}`
const expected = new Set()
for (const [phase, data] of [['before', before], ['after', after]]) {
  assert.deepEqual(data.reports.map(key), slots)
  for (const row of data.reports) {
    assert.deepEqual(row.errors, [])
    for (const state of row.states) {
      assert.equal(state.overflow, false)
      if (phase === 'after') {
        assert.deepEqual(state.targets, [])
        assert.deepEqual(state.contrasts, [])
        assert.deepEqual(state.focus.missing, [])
      }
      expected.add(`${phase}/${state.name}-${key(row)}.png`)
    }
  }
}
// Remove only obsolete generated captures from these two owned output folders.
for (const phase of ['before', 'after']) for (const file of await readdir(path.join(dir, phase))) {
  if (!file.endsWith('.png') || expected.has(`${phase}/${file}`)) continue
  if (verify) throw new Error(`Unindexed capture: ${phase}/${file}`)
  await rm(path.join(dir, phase, file))
}
const oldName = name => ({
  'settings-providers': 'settings-accounts', 'settings-providers-end': 'settings-accounts-end',
  'settings-integrations': 'settings-accounts', 'settings-integrations-end': 'settings-accounts-end',
  'integration-github': 'github-account', 'integration-github-manage': 'github-account',
  'github-account-connection': 'github-account', 'github-account-manage': 'github-account',
  'voice-as-you-speak': 'voice-live', 'voice-record-first': 'voice-record-and-clean',
})[name] || name
const table = ['# Capture index', '', 'Each cell links before → after. Before Accounts held both Providers and Integrations; its individual service cards are captured too. All data is synthetic.', '', '| State | 390 Latte light | 390 Latte dark | 390 Plain | 1440 Latte light | 1440 Latte dark | 1440 Plain |', '| --- | --- | --- | --- | --- | --- | --- |']
for (const state of after.reports[0].states.map(state => state.name)) {
  const cells = slots.map(slot => {
    const old = `before/${oldName(state)}-${slot}.png`, current = `after/${state}-${slot}.png`
    assert.ok(expected.has(old), `No before capture for ${state}/${slot}`)
    assert.ok(expected.has(current), `No after capture for ${state}/${slot}`)
    return `[before](${old}) → [after](${current})`
  })
  table.push(`| ${state} | ${cells.join(' | ')} |`)
}
table.push('', 'Plugin lifecycle and installation states: [browser report](plugin-flows/browser.json). Appearance scaling, reload, fresh-browser and retry checks: [browser report](appearance/browser.json).', '')
const index = table.join('\n')
if (verify) assert.equal(await readFile(path.join(dir, 'capture-index.md'), 'utf8'), index)
else await writeFile(path.join(dir, 'capture-index.md'), index)
const galleryData = after.reports[0].states.map(({ name }) => ({ name, old: oldName(name) }))
const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Settings and Activity — before and after</title>
<style>body{margin:24px;font:16px/1.5 system-ui;background:#f4f3f0;color:#222}header{position:sticky;top:0;background:#f4f3f0;padding:12px 0}label{display:inline-flex;align-items:center;gap:8px;margin:0 16px 8px 0}select{font:inherit;padding:8px;max-width:90vw}main{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}figure{margin:0}figcaption{font-weight:700;margin:0 0 8px}img{display:block;width:100%;height:auto}p{margin:4px 0 12px}</style>
<header><label>Page <select id="page"></select></label><label>Viewport / theme <select id="slot"></select></label><p>Before → after. Synthetic host data. Open either image for full size. <a href="README.md">Review</a></p></header>
<main><figure><figcaption>Before</figcaption><a id="before"><img alt="Before"></a></figure><figure><figcaption>After</figcaption><a id="after"><img alt="After"></a></figure></main>
<script>const states=${JSON.stringify(galleryData)},slots=${JSON.stringify(slots)};const page=document.getElementById('page'),slot=document.getElementById('slot');for(const state of states)page.add(new Option(state.name,state.name));for(const value of slots)slot.add(new Option(value,value));function draw(){const state=states.find(s=>s.name===page.value);for(const [phase,name] of [['before',state.old],['after',state.name]]){const link=document.getElementById(phase),src=phase+'/'+name+'-'+slot.value+'.png';link.href=src;link.firstChild.src=src;}history.replaceState(null,'','#'+page.value+'/'+slot.value)}page.onchange=slot.onchange=draw;const saved=location.hash.slice(1).split('/');if(states.some(s=>s.name===saved[0]))page.value=saved[0];if(slots.includes(saved[1]))slot.value=saved[1];draw();</script>
`
if (verify) assert.equal(await readFile(path.join(dir, 'capture-index.html'), 'utf8'), html)
else await writeFile(path.join(dir, 'capture-index.html'), html)
async function pngs(folder = '') {
  const out = []
  for (const entry of await readdir(path.join(dir, folder), { withFileTypes: true })) {
    const file = path.posix.join(folder, entry.name)
    if (entry.isDirectory()) out.push(...await pngs(file))
    else if (entry.name.endsWith('.png')) out.push(file)
  }
  return out.sort()
}
const manifest = []
for (const file of await pngs()) {
  assert.ok(!path.basename(file).startsWith('failure-'), `Remove stale failure capture ${file}`)
  const bytes = await readFile(path.join(dir, file))
  assert.equal(bytes.toString('hex', 0, 8), '89504e470d0a1a0a')
  manifest.push({ file, width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20), bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') })
}
if (verify) assert.deepEqual(await json('capture-manifest.json'), manifest)
else await writeFile(path.join(dir, 'capture-manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
const validationSections = [
  ['npm test', 'tests.txt', text => text.split('\n').filter(line => /^ℹ (tests|suites|pass|fail|skipped|duration)/.test(line)).join('\n')],
  ['SETTINGS_REVIEW_BASELINE=1 SETTINGS_REVIEW_BASELINE_PUBLIC=<baseline public> node test/settings-review-browser.mjs', 'before-browser-tests.txt'],
  ['node test/settings-review-browser.mjs', 'browser-tests.txt'],
  ['node test/ui-audit-browser.mjs', 'full-app-tests.txt'],
  ['node test/plugin-host-browser.mjs', 'plugin-tests.txt'],
  ['node test/appearance-browser.mjs', 'appearance-tests.txt'],
  ['node test/group-surfaces-browser.mjs', 'group-tests.txt'],
  ['node test/original-agent-palette-browser.mjs', 'palette-tests.txt'],
  ['node docs/design-system/capture.mjs', 'inventory-capture.txt', text => text.trim().split('\n').at(-1)],
  ['node docs/design-system/capture-extra.mjs', 'inventory-extra.txt', text => text.trim().split('\n').slice(-4).join('\n')],
  ['node docs/design-system/generate.mjs', 'inventory-generate.txt'],
  ['node docs/design-system/verify.mjs', 'inventory-verify.txt'],
]
let validation = `Real app/relay, synthetic host answers. Chromium ${after.chromium}. No deployment.\nAudit dependencies and browser libraries live under TMPDIR; setup and evidence paths are in README.md.\nFull npm test output: tests.txt.\n\n`
for (const [command, file, select = text => text.trim()] of validationSections) validation += `$ ${command}\n${select(await readFile(path.join(dir, file), 'utf8'))}\n\n`
const message = `PASS: ${before.reports.reduce((n, r) => n + r.states.length, 0)} before / ${after.reports.reduce((n, r) => n + r.states.length, 0)} after review states; six viewport/theme pairs; zero current overflow, small targets, low contrast or missing keyboard focus; ${manifest.length} PNG hashes and dimensions verified.`
validation += `$ node tools/settings-review-evidence.mjs --verify\n${message}\n`
for (const file of [path.join(dir, 'validation.txt'), 'docs/design-system/validation.txt']) {
  if (verify) assert.equal(await readFile(file, 'utf8'), validation)
  else await writeFile(file, validation)
}
console.log(message)
