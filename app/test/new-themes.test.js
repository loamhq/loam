import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile, stat } from 'node:fs/promises'
import { loadThemes } from '../src/themes.js'
import { resolvedTheme, contrast, themeContrasts } from '../tools/theme-contrast.mjs'
import { newThemes } from '../tools/new-themes.js'
import { shippedPlugins } from '../src/shipped-plugins.js'
import { allRoots, listedFile, readListed } from './listed-themes.mjs'

test('the five first-party listed themes preserve approved role values, core scales and agent palette in both modes', async () => {
  const palettes = JSON.parse(await readFile('tools/theme-palettes.json', 'utf8'))
  const roles = { page: 'paper-page', pane: 'paper-pane', raised: 'paper-raised', card: 'paper-card', ink: 'paper-ink', ink2: 'paper-ink-2', ink3: 'paper-ink-3', rule: 'paper-rule', foam: 'latte-foam', coffee: 'latte-coffee', coffee2: 'latte-coffee-2', esp: 'latte-espresso', espink: 'latte-espresso-ink', signal: 'signal-crema' }
  const catalog = await loadThemes(await allRoots())
  assert.deepEqual(catalog.failures, {})
  for (const { id, name, font, weight } of newThemes) {
    const manifest = catalog.themes.find(t => t.id === id)
    assert.equal(manifest.name, name)
    assert.deepEqual(manifest.options.map(o => [o.id, o.default, o.choices.map(c => c.id)]), [['agent-shapes', id === 'foundry' ? 'rounded-square' : 'circle', ['signature', 'distinct', 'circle', 'rounded-square']], ['face-content', 'mark', ['mark', 'initials']]])
    assert.ok((await stat(listedFile(id, 'cover.webp'))).size < 30000)
    for (const mode of ['light', 'dark']) {
      const values = await resolvedTheme(id, mode), core = await resolvedTheme('plain', mode)
      for (const [role, token] of Object.entries(roles)) assert.equal(values[token], palettes[id][mode][role], `${id}/${mode}/${role}`)
      for (const token of Object.keys(core).filter(k => /^(type-|space-|radius-|motion-|ease-|persona-|agent-latte-)/.test(k))) {
        if (id === 'foundry' && token.startsWith('radius-')) assert.ok(parseInt(values[token]) < parseInt(core[token]), token)
        else assert.equal(values[token], core[token], token)
      }
      assert.match(values['font-body'], /^Figtree,/)
      assert.match(values['font-mono'], /JetBrains Mono/)
      assert.ok(values['font-display'].includes(font))
      for (const ground of ['paper-page', 'paper-pane', 'paper-card', 'paper-raised']) {
        assert.ok(contrast(values['signal-crema'], values[ground]) >= 3, `${id}/${mode}: signal UI`)
        assert.ok(contrast(values['paper-rule-strong'], values[ground]) >= 3, `${id}/${mode}: strong UI boundary`)
      }
    }
    for (const subset of ['latin', 'latin-ext']) {
      const bytes = await readListed(id, `display-${subset}.woff2`, null)
      assert.equal(bytes.toString('ascii', 0, 4), 'wOF2')
    }
    assert.match(await readListed(id, 'OFL.txt'), /SIL OPEN FONT LICENSE/)
    const fonts = await readListed(id, 'fonts.css')
    assert.doesNotMatch(fonts, /https?:/)
    // Relative to its own folder: it is served from the plugin's files route.
    assert.doesNotMatch(fonts, /url\(\//)
    assert.match(fonts, /latin-ext/)
    assert.ok(fonts.includes(`font-weight: ${weight};`))
  }
})

test('semantic status colours retain red, amber and green hue families and AA in every theme/mode', async () => {
  const hsl = hex => {
    const [r,g,b] = hex.slice(1).match(/../g).map(v => parseInt(v,16)/255), max = Math.max(r,g,b), min = Math.min(r,g,b), c = max-min
    const h = c === 0 ? 0 : (max === r ? (g-b)/c : max === g ? (b-r)/c+2 : (r-g)/c+4)*60
    return { h:(h+360)%360, c }
  }
  for (const {id} of newThemes) {
    assert.deepEqual((await themeContrasts(id)).filter(r => r.ratio < r.minimum), [], id)
    for (const mode of ['light','dark']) {
      const t = await resolvedTheme(id, mode)
      for (const [token,lo,hi] of [['danger',0,22],['warning',30,48],['success',90,160]]) {
        const {h,c} = hsl(t[token])
        assert.ok(c >= .14, `${id}/${mode}/${token}: too little chroma ${c}`)
        assert.ok(h >= lo && h <= hi, `${id}/${mode}/${token}: wrong hue ${h}`)
      }
      for (const [token,lo,hi] of [['text-code-danger',0,25],['surface-danger',0,40],['surface-attention',25,60]]) {
        const {h,c} = hsl(t[token]); assert.ok(c > .04 && h >= lo && h <= hi, `${id}/${mode}/${token}: ${h}, ${c}`)
      }
    }
  }
})

test('shipped options follow the local material implementation when a host catalog is older', async () => {
  // Latte is the shipped look with options since the others became listed
  // plugins (2026-11-18), whose options are their installed row's.
  const rows = await shippedPlugins('public'), current = await loadThemes()
  for (const row of rows.filter(row => row.id === 'latte')) row.options = [{ id:'agent-shapes', label:'Agent shapes', default:'distinct', choices:[{id:'distinct',label:'Each its own'},{id:'circle',label:'Circle'}] }]
  const hosted = await loadThemes(undefined,{rows})
  assert.ok(current.themes.find(t=>t.id==='latte').options.length)
  assert.deepEqual(hosted.themes.find(t=>t.id==='latte').options,current.themes.find(t=>t.id==='latte').options)
})
