import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { PLACE_NAMES, COMPONENT_IDS } from '../public/plugin-registry.js'
import { createCoreRegistry } from '../public/core-registrations.js'
import { createPluginLoader, pluginData, pluginSettings } from '../public/plugin-loader.js'
import { createPluginStyles } from '../public/plugin-styles.js'

test('the readable API types track runtime places, components, data and scope methods', async () => {
  const types = await readFile('public/loam.d.ts', 'utf8')
  for (const [name, expected] of [['PlaceName', PLACE_NAMES], ['ComponentId', COMPONENT_IDS]]) {
    const union = new RegExp(`export type ${name} = ([^\\n]+)`).exec(types)[1]
    assert.deepEqual([...union.matchAll(/'([^']+)'/g)].map(match => match[1]), [...expected])
  }
  const data = pluginData(async () => ({})), body = types.split('export interface Data {')[1].split('export interface FieldActivity')[0]
  for (const [key, value] of Object.entries(data)) {
    assert.match(body, new RegExp(`\\b${key}[(:]`))
    if (typeof value === 'object') {
      const declaration = new RegExp(`${key}: \\{([^}]+)\\}`).exec(body)[1]
      assert.deepEqual([...declaration.matchAll(/\b(\w+)\(/g)].map(match => match[1]), Object.keys(value))
    }
  }
  const registry = createCoreRegistry(), loader = createPluginLoader({ registry, ui: { host: {} }, request: async () => ({}) })
  const scope = loader.scope({ id: 'types' })
  assert.deepEqual(Object.keys(scope).sort(), ['places','commands','events','own','dispose','data','ui','settings','api'].sort())
  for (const method of Object.keys(scope.events)) assert.match(types, new RegExp(`\\b${method}\\(`))
  scope.dispose(); await loader.dispose()
})

test('core imports no audio package, and audio packages import only their own files', async () => {
  const seen = new Set()
  async function graph(file, packageRoot = null) {
    file = path.resolve(file)
    if (seen.has(file)) return
    seen.add(file)
    const source = await readFile(file, 'utf8')
    for (const match of source.matchAll(/(?:from\s+|import\s*\()(['"])(\.[^'"]+\.js)\1/g)) {
      const imported = path.resolve(path.dirname(file), match[2])
      if (packageRoot) assert.ok(imported.startsWith(packageRoot + '/'), `${file} imports core: ${imported}`)
      else assert.ok(!imported.includes('/public/plugins/') && !imported.includes('/plugins/_listed/'), `${file} imports an optional package`)
      await graph(imported, packageRoot)
    }
  }
  await graph('public/app.js')
  await graph('src/server.js')
  for (const id of ['dictation', 'read-aloud']) {
    await graph(`../plugins/_listed/${id}/page/main.js`, path.resolve(`../plugins/_listed/${id}`))
    // Each carries its own server half (1.1.0), which imports only its own files.
    await graph(`../plugins/_listed/${id}/server/main.js`, path.resolve(`../plugins/_listed/${id}`))
  }
  const store = await readFile('public/store.js', 'utf8')
  assert.doesNotMatch(store, /dictation|readAloud|speechSettings/)
})

test('Yours activates after delayed defaults and wins components and commands even after a late default registration', async () => {
  const registry = createCoreRegistry(), order = [], rows = ['yours', 'shipped'].map(layer => ({ id: layer, layer, api: 0, effective: true, on: true, switch: 'plugin', has: { page_main: true } }))
  let shippedScope, shippedCommand
  const loader = createPluginLoader({ registry, ui: {}, request: async () => ({ plugins: rows }), importer: async url => ({ async activate(loam) {
    const layer = url.includes('/yours/') ? 'yours' : 'shipped'
    if (layer === 'shipped') { shippedScope = loam; await new Promise(resolve => setTimeout(resolve, 10)) }
    order.push(layer)
    const command = loam.commands.register({ id: 'winner', run: () => layer }); if (layer === 'shipped') shippedCommand = command
    loam.places.message.renderer.add({ id: layer, component: 'core.message', render: () => layer })
  } }) })
  await loader.refresh()
  assert.deepEqual(order, ['shipped', 'yours'])
  shippedCommand.remove()
  shippedScope.commands.register({ id: 'winner', run: () => 'late shipped' })
  shippedScope.places.message.renderer.add({ id: 'late', component: 'core.message', render: () => 'late shipped' })
  assert.equal(shippedScope.commands.run('winner'), 'yours')
  assert.equal(registry.component('core.message', { defaultView: () => 'core' }), 'yours')
  rows[0].on = false; await loader.refresh()
  assert.equal(registry.component('core.message', { defaultView: () => 'core' }), 'late shipped')
  await loader.dispose()
})

function styleDocument() {
  const nodes = [], moves = []
  const doc = {
    head: { children: nodes, insertBefore(node, before) {
      moves.push(node)
      const at = nodes.indexOf(node)
      if (at >= 0) nodes.splice(at, 1)
      nodes.splice(before ? nodes.indexOf(before) : nodes.length, 0, node)
    } },
    createElement: () => ({ dataset: {}, remove() { const at = nodes.indexOf(this); if (at >= 0) nodes.splice(at, 1) } }),
  }
  return { nodes, moves, doc }
}

test('personal CSS follows defaults even when shipped styles arrive later; removal restores the layer', () => {
  const { nodes, moves, doc } = styleDocument()
  const styles = createPluginStyles(doc), yours = styles.add({ id: 'personal', layer: 'yours' }, 'assets/plain.css')
  styles.add({ id: 'default', layer: 'shipped' }, 'page/style.css')
  assert.deepEqual(nodes.map(node => node.dataset.plugin), ['default', 'personal'])
  assert.equal(nodes[1].href, '/api/plugins/personal/files/assets/plain.css')
  assert.equal(moves.length, 2, 'late shipped CSS is inserted before Yours without moving its sheet')
  moves.length = 0; styles.order(); styles.refresh()
  assert.equal(moves.length, 0, 'unchanged catalog/order does not detach any sheet')
  yours.remove(); yours.remove()
  assert.deepEqual(nodes.map(node => node.dataset.plugin), ['default'])
  const local = styles.add({ id: 'local', layer: 'shipped', local: '/plugins/local/page/main.js' }, 'page/style.css', true)
  assert.equal(nodes.at(-1).href, '/plugins/local/page/style.css'); local.remove()
})

test('device preference migration retains old keys and failed storage retains normalized values', () => {
  const held = new Map([['previous.key', JSON.stringify({ count: 7 })]])
  const adapter = { getItem: key => held.get(key), setItem: (key, value) => held.set(key, value) }
  const prefs = pluginSettings('example', adapter).device('previous.key', value => ({ count: value?.count ?? 0 }))
  assert.equal(prefs.value.count, 7); prefs.value = { count: 8 }
  assert.deepEqual(JSON.parse(held.get('previous.key')), { count: 8 })
  const broken = pluginSettings('example', { getItem() { throw Error() }, setItem() { throw Error() } }).device('previous.key', value => value ?? 0)
  broken.value = 9; assert.equal(broken.value, 9)
})

test('CSS refresh keeps the loaded sheet until success and cannot restore a removed personal override', () => {
  const { nodes, moves, doc } = styleDocument()
  const styles = createPluginStyles(doc), handle = styles.add({ id: 'personal', layer: 'yours' }, 'custom.css'), original = nodes[0]
  styles.refresh(); styles.refresh()
  const next = nodes[1]
  assert.deepEqual(moves, [original, next], 'only the new pending link is inserted')
  assert.equal(nodes[0], original)
  assert.match(next.href, /custom\.css\?load=1$/)
  moves.length = 0; next.onload()
  assert.deepEqual(nodes, [next])
  assert.equal(moves.length, 0, 'successful swap leaves the loaded replacement attached')
  styles.refresh(); const refused = nodes[1]; refused.onerror()
  assert.deepEqual(nodes, [next], 'failed replacement preserves the loaded sheet')
  styles.refresh(); const stale = nodes[1]
  handle.remove(); stale.onload()
  assert.deepEqual(nodes, [])
})
