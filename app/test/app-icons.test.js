import test from 'node:test'
import assert from 'node:assert/strict'
import { createAppIcons } from '../src/app-icons.js'

const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect width="40" height="40"/></svg>'
test('declared icons are resolved to verified image bytes, with no conventional-path probes or credentials', async () => {
  const seen = [], origin = 'https://garden.example.org'
  const icon = createAppIcons({ fetcher: async (url, options) => {
    seen.push({ url, options })
    if (url.includes('apple.png')) return new Response('missing', { status: 404 })
    if (url.endsWith('.webmanifest')) return new Response(JSON.stringify({ icons: [{ src: 'small.png', sizes: '48x48' }, { src: 'large.svg', sizes: '192x192' }, null] }))
    if (url.endsWith('large.svg')) return new Response(svg)
    return new Response('<link rel="apple-touch-icon" href="images/apple.png?a=1&amp;b=2"><link rel="manifest" href="assets/site.webmanifest"><link rel="icon" href="/brand.svg">')
  } })
  const found = await icon(origin + '/app/')
  assert.equal(found.type, 'image/svg+xml'); assert.equal(found.body.toString(), svg)
  assert.deepEqual(seen.map(r => r.url), [origin + '/app/', origin + '/app/assets/site.webmanifest', origin + '/app/images/apple.png?a=1&b=2', origin + '/app/assets/large.svg'])
  assert.ok(seen.every(r => !r.options.headers.authorization && !r.options.headers.cookie && r.options.redirect === 'manual'))
})
test('no declared icon, private redirects, oversized pages and HTTP failures resolve to no image without probing', async () => {
  for (const response of [new Response('<h1>No icon</h1>'), new Response(null, { status: 302, headers: { location: 'https://login.example.org/' } }), new Response('x'.repeat(129 * 1024)), new Response('unavailable', { status: 503 })]) {
    const seen = []
    const icon = createAppIcons({ fetcher: async url => { seen.push(url); return response } })
    assert.equal(await icon('https://garden.example.org/'), null)
    assert.equal(seen.length, 1)
    assert.equal(await icon('file:///etc/passwd'), null)
    assert.equal(await icon('https://user:password@example.org/'), null)
  }
})
test('broken manifests preserve declared favicons; HTML masquerading as an image is not served', async () => {
  const seen = []
  const icon = createAppIcons({ fetcher: async url => {
    seen.push(url)
    return new Response(url.endsWith('/manifest') ? '{broken' : url.endsWith('/brand.svg') ? svg : '<link rel=manifest href=/manifest><link rel=icon href=/brand.svg>')
  } })
  assert.equal((await icon('https://garden.example.org/')).body.toString(), svg)
  assert.equal(seen.length, 3)
  const login = createAppIcons({ fetcher: async () => new Response('<html><h1>Sign in</h1></html>') })
  assert.equal(await login('https://garden.example.org/', { icon: '/icon.png' }), null)
})
test('icon resolution bounds concurrent network readers', async () => {
  let active = 0, max = 0
  const icon = createAppIcons({ fetcher: async () => {
    active++; max = Math.max(max, active)
    await new Promise(resolve => setTimeout(resolve, 10)); active--
    return new Response('<h1>No icon declared</h1>')
  } })
  await Promise.all(Array.from({ length: 9 }, (_, i) => icon(`https://app-${i}.example.org/`)))
  assert.equal(max, 4)
})
