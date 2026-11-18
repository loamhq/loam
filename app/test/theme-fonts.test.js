// A listed theme's display faces (2026-11-18): the first-party looks left the
// shipped set for the Market, so their fonts come from the installed folder,
// through a check that lets nothing but @font-face rules for files beside it.
import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFile } from 'node:fs/promises'
import { themeFontsCSS } from '../src/themes.js'
import { appAgainst, closing, fakeBroker } from './harness.js'
import { LISTED_THEMES, readListed } from './listed-themes.mjs'

test('every listed look’s fonts.css passes the check and points at its own files', async () => {
  for (const id of LISTED_THEMES) {
    const out = themeFontsCSS(id, await readListed(id, 'fonts.css'))
    assert.equal((out.match(/@font-face/g) || []).length, 2, id)
    assert.match(out, new RegExp(`src: url\\(/api/plugins/${id}/files/display-latin\\.woff2\\) format\\('woff2'\\);`))
    assert.doesNotMatch(out, /https?:|\/themes\//)
  }
})

test('anything but a font beside it is refused', () => {
  const face = src => `@font-face { font-family: 'X'; font-weight: 400; src: ${src}; }`
  assert.match(themeFontsCSS('ember', face("url('fonts/a.woff2') format('woff2'), url(a.woff)")), /url\(\/api\/plugins\/ember\/files\/fonts\/a\.woff2\) format\('woff2'\), url\(\/api\/plugins\/ember\/files\/a\.woff\)/)
  assert.equal(themeFontsCSS('ember', '/* none */'), '')
  for (const bad of [
    face('url(https://example.invalid/a.woff2)'), face('url(../a.woff2)'), face('url(/a.woff2)'), face('url(a.ttf)'), face('local(Arial)'),
    face('url(a.woff2)') + ' body { color: red }', '@import "x.css";', face('url(a.woff2)').replace("font-weight: 400", 'font-feature-settings: "x"'),
    face('url(a.woff2)').replace("'X'", "'X' url(x)"), '@font-face { src: url(a.woff2); }',
  ]) assert.throws(() => themeFontsCSS('ember', bad), bad)
  assert.throws(() => themeFontsCSS('../x', face('url(a.woff2)')))
})

test('the app serves an installed theme’s checked fonts, and an empty sheet when there are none', async t => {
  const css = await readListed('autumn', 'fonts.css')
  const broker = await fakeBroker({
    'GET /v1/plugins/autumn/files/fonts.css': (req, res) => { res.writeHead(200, { 'content-type': 'text/css' }); res.end(css) },
    'GET /v1/plugins/sly/files/fonts.css': (req, res) => { res.writeHead(200, { 'content-type': 'text/css' }); res.end('body { background: url(https://example.invalid/x) }') },
  })
  const app = await appAgainst(broker)
  t.after(closing(app, broker))
  const ok = await fetch(`${app.url}/theme-fonts/autumn.css`)
  assert.equal(ok.status, 200)
  assert.match(ok.headers.get('content-type'), /^text\/css/)
  assert.match(await ok.text(), /\/api\/plugins\/autumn\/files\/display-latin\.woff2/)
  for (const id of ['sly', 'missing']) {
    const out = await fetch(`${app.url}/theme-fonts/${id}.css`)
    assert.equal(out.status, 200)
    assert.equal(await out.text(), '')
  }
})

test('the page asks for a look’s faces once, and never for Latte’s or Plain’s', async () => {
  const links = []
  const ctx = vm.createContext({ document: { readyState: 'complete', head: { append: l => links.push(l.href) }, createElement: () => ({}) } })
  ctx.globalThis = ctx
  vm.runInContext(await readFile('public/theme-fonts.js', 'utf8'), ctx)
  for (const id of ['latte', 'plain', 'autumn', 'autumn', 'ember', 'Bad"', null]) ctx.LoamThemeFonts(id)
  assert.deepEqual(links, ['/theme-fonts/autumn.css', '/theme-fonts/ember.css'])
})
