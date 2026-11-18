import test from 'node:test'
import assert from 'node:assert/strict'
import { stat } from 'node:fs/promises'
import { themeImage, themeImageURL, themeImagePosition } from '../public/theme-image.js'
import { loadThemes } from '../src/themes.js'
import { shippedPlugins } from '../src/shipped-plugins.js'
import { appAgainst, closing, fakeBroker } from './harness.js'
import { allRoots, readThemeCSS } from './listed-themes.mjs'

test('theme cover metadata stays within the package and validates focal percentages', () => {
  assert.deepEqual(themeImage({}), {})
  assert.deepEqual(themeImage({ image: 'assets/cover.webp' }), { image: 'assets/cover.webp', image_position: '50% 50%' })
  for (const image of ['../cover.webp', '/cover.webp', 'https://example.invalid/a.png', 'a/../cover.png', 'a%2fb.webp', 'data:image/png,abc', 'a.svg', 'a.png?x', 12]) {
    assert.throws(() => themeImage({ image }), /relative image path/)
    assert.throws(() => themeImage({ image: 'cover.webp', image_dark: image }), /relative image path/)
  }
  for (const image_position of ['101% 50%', '-1% 50%', 'left center', '50%;color:red', 12]) {
    assert.throws(() => themeImage({ image: 'a.webp', image_position }), /two percentages/)
    assert.throws(() => themeImage({ image: 'a.webp', image_position_dark: image_position }), /two percentages/)
  }
  assert.equal(themeImageURL({ id: 'example', layer: 'yours', image: 'assets/cover.webp' }), '/api/plugins/example/files/assets/cover.webp')
  assert.equal(themeImageURL({ id: 'plain' }), null)
  const dual = { id: 'example', layer: 'yours', image: 'light.webp', image_dark: 'dark.webp', image_position: '20% 70%' }
  assert.equal(themeImageURL(dual, 'dark'), '/api/plugins/example/files/dark.webp')
  assert.equal(themeImagePosition(dual, 'dark'), '20% 70%')
  assert.equal(themeImageURL({ id: 'example', image: 'a.webp' }, 'dark'), '/themes/example/a.webp')
  assert.throws(() => themeImage({ image_dark: 'dark.webp' }), /needs a default/)
})

test('shipped, boot and host-selected catalogs retain cover metadata and stable theme ids', async () => {
  const plugins = await shippedPlugins('public'), latte = plugins.find(p => p.id === 'latte')
  assert.equal(latte.name, 'Latte & Newspaper')
  assert.equal(latte.image, 'cover.webp')
  const offline = await loadThemes(), hosted = await loadThemes(undefined, { rows: plugins })
  for (const { themes } of [offline, hosted]) {
    // Latte and Plain ship; the other first-party looks are plugins from the Market (2026-11-18).
    assert.deepEqual(themes.map(t => t.id), ['latte', 'plain'])
    assert.equal(themes[0].image_position, '62% 45%')
    assert.equal(themeImageURL(themes[0]), '/themes/latte/cover.webp')
    assert.equal(themes.find(t => t.id === 'plain').image, undefined)
  }
  // A listed look installed in Yours keeps its light and dark covers, from its own folder.
  const [listedAutumn] = (await loadThemes(await allRoots())).themes.filter(t => t.id === 'autumn')
  const autumnRow = { ...listedAutumn, api: 0, kind: ['theme'], effective: true, layer: 'yours' }
  const yours = await loadThemes(undefined, { rows: [...plugins, autumnRow], readTheme: async () => readThemeCSS('autumn') })
  const autumn = yours.themes.find(t => t.id === 'autumn')
  assert.equal(themeImageURL(autumn, 'light'), '/api/plugins/autumn/files/cover.webp')
  assert.equal(themeImageURL(autumn, 'dark'), '/api/plugins/autumn/files/cover-dark.webp')
  assert.equal(themeImagePosition(autumn, 'dark'), '50% 42%')
  const installed = { ...latte, id: 'example', layer: 'yours', image: 'assets/card.webp' }
  const custom = await loadThemes(undefined, { rows: [installed], readTheme: async () => '/* core tokens */' })
  assert.equal(themeImageURL(custom.themes.find(t => t.id === 'example')), '/api/plugins/example/files/assets/card.webp')
  const broken = await loadThemes(undefined, { rows: [{ ...installed, image: '../bad.webp' }], readTheme: async () => '/* core tokens */' })
  assert.match(broken.failures.example, /relative image path/)
  assert.ok((await stat('public/themes/latte/cover.webp')).size < 200_000)
})

test('cover bytes are served as WebP and the pre-paint catalog carries the preview', async t => {
  const broker = await fakeBroker({}), app = await appAgainst(broker)
  t.after(closing(app, broker))
  const response = await fetch(`${app.url}/themes/latte/cover.webp`)
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('content-type'), 'image/webp')
  const bytes = Buffer.from(await response.arrayBuffer())
  assert.equal(bytes.toString('ascii', 0, 4), 'RIFF')
  assert.equal(bytes.toString('ascii', 8, 12), 'WEBP')
  const boot = await (await fetch(`${app.url}/appearance-state.js`)).text()
  assert.match(boot, /"image":"cover.webp"/)
  assert.match(boot, /"name":"Latte & Newspaper"/)
})
