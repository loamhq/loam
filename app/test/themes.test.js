import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, readdir, mkdir, mkdtemp, writeFile, rm } from 'node:fs/promises'
import path from 'node:path'
import { tmpdir } from 'node:os'
import { loadThemes, loadCoreTokens, validateThemeCSS } from '../src/themes.js'
import { appearanceValues } from '../src/appearance.js'
import { createTheme } from '../public/theme.js'
import { installDom } from './fakedom.js'
import { appAgainst, closing, fakeBroker } from './harness.js'
import { themeContrasts } from '../tools/theme-contrast.mjs'
import { agentLook, setAvatarColours } from '../public/sessions.js'
import { AGENT_COLOURS, AGENT_TINTS, LEGACY_AGENT_COLOURS, readAvatarColours } from '../public/avatar-colours.js'

// The claim document is served outside the app under an isolated CSP. It must
// carry its own tokens; release-u4 tests enforce its self-contained boundary.
test('all app CSS and JavaScript use semantic colour tokens; only core defaults and theme folders define literal colours', async () => {
  for (const file of await readdir('public', { recursive: true })) {
    if (!/\.(css|js|html)$/.test(file) || file.startsWith('themes/') || file === 'tokens.css' || file === 'claim.html') continue
    const source = (await readFile(path.join('public', file), 'utf8')).replace(/\/\*[\s\S]*?\*\//g, '')
    assert.doesNotMatch(source, /--c\d+\b|[\x27"`]c\d+[\x27"`]/, file)
    assert.doesNotMatch(source, /var\(\s*--(?:surface-(?:page|pane|raised|sunken|hover|disabled)(?:-glass)?|text(?:-muted|-faint|-on-accent)?|action(?:-hover|-pressed)?|accent(?:-quiet|-tint)?|hairline|rule|hover|pressed|highlight)(?=[\s,)])/, `${file}: retired colour role`)
    assert.doesNotMatch(source, /#[a-f\d]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\s*\(/i, file)
    if (file.endsWith('.css')) assert.doesNotMatch(source, /(?:color|background|fill|stroke)\s*:\s*(?:white|black|red|blue|green|orange|yellow|gray|grey)\b/i, file)
  }
})

test('installed manifests contain partial, token-only overrides', async () => {
  const installed = await loadThemes(), defaults = await loadCoreTokens()
  assert.deepEqual(installed.themes.map(t => t.id), ['latte', 'autumn', 'deep-forest', 'foundry', 'mist-trail', 'plain', 'yosemite'])
  for (const { id } of installed.themes) {
    const css = await readFile(`public/themes/${id}/theme.css`, 'utf8')
    assert.equal(validateThemeCSS(css, id, defaults), css)
    const valid = `:root[data-look="${id}"] { --radius-md: 17px; }`
    for (const mutation of [valid.replace('--radius-md:', 'color:'), valid + 'body { display:none; }', valid + '@import "bad.css";', valid.replace('17px', 'url(https://example.invalid/track)'), valid.replace('--radius-md:', '--unknown:'), valid.replace('--radius-md:', '--c1:'), valid.replace('17px', 'var(--unknown)'), valid + valid, '@media (prefers-color-scheme:dark) {' + valid + '}']) {
      assert.throws(() => validateThemeCSS(mutation, id, defaults), mutation)
    }
  }
})

test('core defaults reject redundant overrides in shared and mode-specific scopes', async () => {
  const defaults = await loadCoreTokens()
  for (const mode of ['light', 'dark']) for (const [token, value] of defaults[mode]) {
    const css = `:root[data-look="example"][data-theme="${mode}"] { ${token}: ${value}; }`
    assert.throws(() => validateThemeCSS(css, 'example', defaults), /Redundant theme token/, css)
  }
  for (const [token, value] of defaults.light) {
    assert.throws(() => validateThemeCSS(`:root[data-look="example"] { ${token}: ${value}; }`, 'example', defaults), /Redundant theme token/)
  }
  assert.throws(() => validateThemeCSS(':root[data-look="example"][data-theme="light"] { --paper-page: #E8E8E8; }', 'example', defaults), /Redundant/)
  assert.throws(() => validateThemeCSS(':root[data-look="example"] { --shadow-float: 0  3px 10px var( --shadow-ink ); }', 'example', defaults), /Redundant/)
  assert.equal(validateThemeCSS('/* Entirely inherited. */', 'example', defaults), '/* Entirely inherited. */')
})

test('shared geometry lives once and every theme may override it', async () => {
  const defaults = await loadCoreTokens(), css = await readFile('public/tokens.css', 'utf8')
  for (const token of defaults.light.keys()) if (/^--(?:type|space|radius|motion|ease|shadow-(?:float|popover))/.test(token)) {
    assert.equal([...css.matchAll(new RegExp(`${token}:`, 'g'))].length, 1, token)
    assert.equal(defaults.light.get(token), defaults.dark.get(token), token)
  }
  for (const selector of [':root[data-look="example"]', ':root[data-look="example"][data-theme="light"]', ':root[data-look="example"][data-theme="dark"]']) {
    assert.doesNotThrow(() => validateThemeCSS(`${selector} { --radius-md: 17px; }`, 'example', defaults))
  }
})

test('every runtime avatar hue and tint resolves in both themes, including deployed configuration aliases', async t => {
  t.after(() => setAvatarColours())
  const { tokens } = await loadThemes()
  for (const token of [...AGENT_COLOURS, ...AGENT_TINTS]) assert.ok(tokens.has(`--${token}`), token)
  for (const supplied of [...AGENT_COLOURS, 'c54', 'c56']) {
    const normalized = readAvatarColours({ sample: supplied })
    assert.match(normalized.sample, /^persona-/)
    setAvatarColours(normalized)
    const face = agentLook('sample')
    for (const ref of [face.color, face.tint]) {
      const token = /^var\((--[\w-]+)\)$/.exec(ref)?.[1]
      assert.ok(tokens.has(token), ref)
    }
  }
})

test('the same manifest loader reads an additional directory layer without changing shipped themes', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'theme-loader-'))
  await mkdir(path.join(root, 'example'), { recursive: true })
  try {
    const manifest = await readFile('public/themes/latte/PLUGIN.md', 'utf8')
    await writeFile(path.join(root, 'example/PLUGIN.md'), manifest.replace('id: latte', 'id: example').replace('name: Latte & Newspaper', 'name: Example'))
    await writeFile(path.join(root, 'example/theme.css'), ':root[data-look="example"] { --radius-md: 17px; }')
    const themes = await loadThemes([new URL('../public/themes/', import.meta.url), root])
    assert.ok(themes.themes.some(t => t.id === 'example'))
    assert.match(themes.css, /data-look="example".*--radius-md: 17px/)
    await writeFile(path.join(root, 'example/theme.css'), ':root[data-look="example"] { --radius-md: 14px; }')
    await assert.rejects(loadThemes([new URL('../public/themes/', import.meta.url), root]), /Redundant theme token/)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('supported text pairs clear 4.5:1 and avatar initials clear 3:1 in light and dark', async () => {
  for (const { id } of (await loadThemes()).themes) for (const row of await themeContrasts(id)) assert.ok(row.ratio >= row.minimum, `${id}: ${JSON.stringify(row)}`)
})

test('theme changes share host persistence, survive a fresh client, and reject unknown IDs', async () => {
  const doc = installDom(); doc.documentElement = doc.createElement('html'); doc.documentElement.dataset = {}; doc.querySelector = () => null
  const host = appearanceValues(null), writes = []
  const request = async (method, body) => { if (body) { writes.push(body); for (const [kind, patch] of Object.entries(body)) Object.assign(host[kind], patch) } return structuredClone(host) }
  const theme = createTheme(null, { kind: 'desktop', boot: { values: host, available: true }, request })
  theme.set({ theme: 'plain' }); assert.equal(doc.documentElement.dataset.look, 'plain')
  await theme.save()
  assert.deepEqual(writes, [{ desktop: { theme: 'plain' } }])
  theme.set({ theme: '../bad' }); assert.equal(theme.value.theme, 'plain')
  const fresh = createTheme(null, { kind: 'desktop', boot: { values: structuredClone(host), available: true }, request })
  assert.equal(fresh.value.theme, 'plain'); assert.equal(host.phone.theme, 'latte')
  host.desktop.theme = 'latte'; await fresh.refresh(); assert.equal(fresh.value.theme, 'latte')
})

test('boot catalog and render-blocking styles come from the same installed manifests', async t => {
  const broker = await fakeBroker({ 'GET /v1/settings/appearance': (req, res) => res.end(JSON.stringify({ desktop: { mode: 'dark', size: 100, theme: 'plain' } })) })
  const app = await appAgainst(broker); t.after(closing(app, broker))
  const boot = await (await fetch(`${app.url}/appearance-state.js`)).text()
  assert.match(boot, /"theme":"plain"/); assert.match(boot, /"name":"Latte & Newspaper"/)
  const css = await fetch(`${app.url}/themes.css`)
  assert.match(css.headers.get('content-type'), /text\/css/)
  assert.match(await css.text(), /:root\[data-look="latte"\]/)
  const html = await (await fetch(app.url)).text()
  assert.ok(html.indexOf('/themes.css') > html.indexOf('/style.css'))
})

test('paper and latte references cannot cross in a theme, and legacy avatar slots keep their hues', async () => {
  const defaults = await loadCoreTokens()
  for (const [a,b] of [['paper-card','latte-track'],['paper-card-ink','latte-coffee'],['latte-track-ink','paper-ink'],['paper-ink','persona-teal']]) {
    assert.throws(() => validateThemeCSS(`:root[data-look="example"] { --${a}: var(--${b}); }`, 'example', defaults), /Crossed colour family/)
  }
  assert.equal(AGENT_COLOURS.length, 11)
  for (const [old, current] of Object.entries(LEGACY_AGENT_COLOURS)) assert.equal(readAvatarColours({ fixture: old }).fixture, current)
  const numbers = [49,51,52,54,56,58,60,62,64,66,68]
  const old = ['slate','olive','rust','plum','teal','rose','gold','blue','taupe','sage','mauve']
  for (let i=0;i<numbers.length;i++) assert.equal(readAvatarColours({ fixture:`c${numbers[i]}` }).fixture, readAvatarColours({ fixture:`persona-${old[i]}` }).fixture)
})
