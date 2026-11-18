import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFile, mkdir, mkdtemp, writeFile, rm } from 'node:fs/promises'
import path from 'node:path'
import { tmpdir } from 'node:os'
import { loadThemes, loadCoreTokens, validateThemeOptions, validateThemeCSS } from '../src/themes.js'
import { appearanceBoot, appearanceValues } from '../src/appearance.js'
import { optionValues, mergeAppearance } from '../public/theme-options.js'
import { createTheme } from '../public/theme.js'
import { createAgents, agentLook, setAvatarColours } from '../public/sessions.js'
import { avatarLook, blob } from '../public/ui.js'
import { paintMention } from '../public/thread-identity.js'
import { readField } from '../public/compose.js'
import { contrast, resolvedTheme } from '../tools/theme-contrast.mjs'
import { installDom } from './fakedom.js'
import { allRoots, readThemeCSS } from './listed-themes.mjs'
const themes = (await loadThemes(await allRoots())).themes
const defaults = { latte: { 'agent-colours': 'palette', 'agent-shapes': 'circle', 'latte-mug': 'on', 'latte-inside': 'pour' }, ...Object.fromEntries(['deep-forest', 'autumn', 'yosemite', 'mist-trail', 'foundry'].map(id => [id, { 'agent-shapes': id === 'foundry' ? 'rounded-square' : 'circle', 'face-content': 'mark' }])) }
const custom = { id: 'texture', label: 'Texture', default: 'smooth', choices: [{ id: 'smooth', label: 'Smooth' }, { id: 'grain', label: 'Grain' }] }
function page() {
  const doc = installDom(); doc.documentElement = doc.createElement('html'); doc.querySelector = () => null
  return doc
}

test('manifest options reject duplicates, invalid ids, long labels, missing defaults and unknown core choices', () => {
  assert.deepEqual(validateThemeOptions(undefined, 'example'), [])
  assert.deepEqual(validateThemeOptions([custom], 'example'), [custom])
  assert.doesNotThrow(() => validateThemeOptions([{ ...custom, id: 'constructor' }], 'example'))
  const invalid = [null, {}, [null], [custom, custom], [{ ...custom, id: 'UPPER' }], [{ ...custom, label: ' ' }], [{ ...custom, default: 'lost' }], [{ ...custom, default: null }], [{ ...custom, choices: [] }], [{ ...custom, choices: [custom.choices[0], custom.choices[0]] }], [{ ...custom, choices: [{ id: 'ok', label: 'x' }, { id: 'bad"]', label: 'y' }] }], [{ ...custom, id: 'agent-shapes' }], [{ ...custom, script: 'bad' }]]
  for (const options of invalid) assert.throws(() => validateThemeOptions(options, 'example'))
})

test('third-party CSS options use the same loader, catalog, validated scopes and generated previews', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'option-theme-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  await mkdir(path.join(root, 'example'))
  const manifest = { schemaVersion: 1, id: 'example', name: 'Example', version: '1.0.0', author: 'Contributors', modes: ['light', 'dark'], stylesheet: 'theme.css', options: [custom] }
  const css = ':root[data-look="example"][data-option-texture="grain"] { --radius-md: 17px; }'
  await writeFile(path.join(root, 'example/PLUGIN.md'), `---\nid: example\nname: Example\nversion: 1.0.0\nauthor: Contributors\napi: 0\nkind: [theme]\noptions: ${JSON.stringify(manifest.options)}\n---\n`)
  await writeFile(path.join(root, 'example/theme.css'), css)
  const loaded = await loadThemes([new URL('../public/themes/', import.meta.url), root])
  assert.deepEqual(loaded.themes.find(t => t.id === 'example').options, [custom])
  assert.ok(loaded.css.includes(css))
  assert.match(loaded.css, /\.theme-preview\[data-look="example"\]/)
  const core = await loadCoreTokens()
  for (const bad of [css.replace('grain', 'other'), css.replace('texture', 'unknown'), css.replace('data-look="example"', 'data-look="latte"'), css.replace(' {', '[data-option-texture="smooth"] {'), css.replace(' {', ' .child {')]) assert.throws(() => validateThemeCSS(bad, 'example', core, [custom]))
  assert.doesNotThrow(() => validateThemeCSS(css.replace('[data-option', '[data-theme="dark"][data-option'), 'example', core, [custom]))
})

test('only declared choices enter public boot; older host values get option defaults before paint', async () => {
  assert.deepEqual(optionValues({ latte: { 'agent-colours': 'bad', secret: 'hidden' }, unknown: { x: 'hidden' } }, themes), defaults)
  const source = await appearanceBoot({ json: async () => ({ status: 200, body: JSON.stringify({ desktop: { mode: 'dark', size: 100, theme: 'latte', options: { latte: { 'agent-colours': 'latte-tones', secret: 'hidden' } } } }) }) }, themes)
  assert.doesNotMatch(source, /hidden|secret/)
  const root = { dataset: {}, attrs: {}, style: { setProperty() {} }, setAttribute(k, v) { this.attrs[k] = v } }
  const ctx = vm.createContext({ document: { documentElement: root }, matchMedia: () => ({ matches: false }) })
  vm.runInContext(source, ctx)
  vm.runInContext(await readFile('public/theme-boot.js', 'utf8'), ctx)
  assert.equal(root.attrs['data-option-agent-colours'], 'latte-tones')
  assert.equal(root.attrs['data-option-agent-shapes'], 'circle')
  assert.equal(root.dataset.theme, 'dark')
  assert.deepEqual(appearanceValues(null, themes).desktop.options, defaults)
})

test('nested option writes merge per theme and device, survive fresh clients, and clear inactive attributes', async () => {
  const doc = page(), host = appearanceValues(null, themes), calls = []
  const request = async (method, body) => { calls.push(body); if (body) for (const [kind, patch] of Object.entries(body)) host[kind] = mergeAppearance(host[kind], patch); return structuredClone(host) }
  const controller = () => createTheme(null, { kind: 'desktop', boot: { themes, values: structuredClone(host), available: true }, request })
  const theme = controller(); theme.apply()
  theme.set({ options: { latte: { 'agent-colours': 'latte-tones' } } })
  theme.set({ options: { latte: { 'agent-shapes': 'circle' } } })
  await theme.save()
  assert.deepEqual(calls[0], { desktop: { options: { latte: { 'agent-colours': 'latte-tones', 'agent-shapes': 'circle' } } } })
  assert.deepEqual(host.phone.options, defaults)
  assert.equal(controller().value.options.latte['agent-shapes'], 'circle')
  theme.set({ theme: 'plain' }); assert.equal(doc.documentElement.hasAttribute('data-option-agent-shapes'), false)
  theme.set({ theme: 'latte' }); assert.equal(doc.documentElement.getAttribute('data-option-agent-shapes'), 'circle')
  theme.set({ options: { latte: { 'agent-shapes': 'bad' } } }); assert.equal(theme.value.options.latte['agent-shapes'], 'circle')
  await theme.save()
  host.desktop.options.latte['agent-shapes'] = 'rounded-square'; await theme.refresh()
  assert.equal(doc.documentElement.getAttribute('data-option-agent-shapes'), 'rounded-square')
  const detached = theme.value; detached.options.latte['agent-shapes'] = 'circle'
  assert.equal(theme.value.options.latte['agent-shapes'], 'rounded-square')
})

test('a refused or silently dropped options write stays visible and retries without losing later choices', async () => {
  page()
  const host = appearanceValues(null, themes)
  let waiting, fail = true
  const theme = createTheme(null, { kind: 'desktop', boot: { themes, values: host, available: true }, request: async (method, body) => {
    if (fail) { await new Promise(resolve => { waiting = resolve }); return { desktop: { mode: 'system', size: 100, theme: 'latte' } } }
    host.desktop = mergeAppearance(host.desktop, body.desktop); return structuredClone(host)
  } })
  theme.set({ options: { latte: { 'agent-colours': 'latte-tones' } } }); const save = theme.save()
  theme.set({ options: { latte: { 'agent-shapes': 'circle' } } }); waiting(); await save
  assert.match(theme.status.error, /host has not saved/)
  assert.equal(theme.value.options.latte['agent-colours'], 'latte-tones')
  assert.equal(theme.value.options.latte['agent-shapes'], 'circle')
  fail = false; await theme.save()
  assert.equal(theme.status.error, null)
  assert.deepEqual(host.desktop.options.latte, { ...defaults.latte, 'agent-colours': 'latte-tones', 'agent-shapes': 'circle' })
})

test('the avatar component owns images, initials, shape and colours, including text-free mention faces', t => {
  page(); t.after(() => setAvatarColours())
  const agents = createAgents(Array.from({ length: 10 }, (_, i) => ({ id: `sample-${i}`, name: `Agent ${i}` })))
  const tones = agents.list.map(a => agentLook(a.id, agents).tone)
  assert.equal(new Set(tones).size, 10, 'use the whole ramp before repeating')
  assert.equal(new Set(tones.slice(0, 7)).size, 7)
  assert.equal(Math.min(...tones.slice(0, 7)), 0)
  assert.equal(Math.max(...tones.slice(0, 7)), 9)
  for (let n = 1; n < 7; n++) assert.ok(Math.abs(tones[n] - tones[n - 1]) >= 3, 'spread roster neighbours')
  const look = agentLook('sample-0', agents), paint = avatarLook(look)
  assert.equal(avatarLook(paint), paint)
  assert.match(paint.shape, /--agent-avatar-shape/)
  assert.match(paint.color, /--agent-avatar-tone-0/)
  setAvatarColours({ 'sample-0': 'persona-teal' })
  const configured = avatarLook(agentLook('sample-0', agents))
  assert.equal(configured.color, 'var(--agent-avatar-tone-0, var(--persona-teal))')
  assert.equal(configured.tint, 'var(--agent-avatar-tint-0, var(--persona-teal-tint))')
  assert.equal(configured.ink, 'var(--agent-avatar-ink-0, var(--persona-teal-ink, var(--text-on-avatar)))')
  const image = blob({ ...look, imageSrc: '/image-fixture.png' }, { size: 32 })
  assert.equal(image.textContent, '')
  assert.equal(image.firstChild.getAttribute('src'), '/image-fixture.png')
  assert.equal(image.__css.background, undefined)
  const mention = document.createElement('span'); mention.textContent = '@sample-0'
  paintMention(mention, look)
  assert.equal(mention.textContent, '@sample-0')
  assert.equal(mention.childNodes[1].getAttribute('data-initial'), look.initial)
  const field = document.createElement('div'); field.appendChild(mention)
  assert.equal(readField(field).md, '@sample-0')
  paintMention(mention, null); assert.equal(mention.childNodes.length, 1)
})

test('all ten warm tones clear the display-initial 3:1 floor with coffee or foam ink', async () => {
  for (const mode of ['light','dark']) {
    const t = await resolvedTheme('latte', mode)
    assert.equal(new Set(Array.from({length:10},(_,n)=>t[`agent-latte-${n}`].toLowerCase())).size,10)
    for (let n=0;n<10;n++) assert.ok(contrast(t[`agent-latte-${n}`],t[n<5?'agent-latte-coffee':'agent-latte-foam'])>=3,`tone ${n}`)
  }
})
