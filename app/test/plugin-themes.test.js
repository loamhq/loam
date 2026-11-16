import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { createPluginThemes } from '../public/plugin-themes.js'
import { shippedPlugins } from '../src/shipped-plugins.js'

test('theme refresh keeps the loaded sheet through success, failure and overlapping refreshes', async () => {
  const doc = installDom(), create = doc.createElement.bind(doc)
  doc.createElement = tag => {
    const node = create(tag)
    node.after = next => node.parentNode.insertBefore(next, node.nextSibling)
    node.remove = () => node.parentNode?.removeChild(node)
    return node
  }
  const head = doc.createElement('head'), initial = doc.createElement('link')
  initial.href = '/themes.css'; head.append(initial)
  doc.querySelector = () => head.firstChild
  const installed = [], theme = { install: catalog => installed.push(catalog), apply() {} }
  const refresh = createPluginThemes({ theme, request: async () => ({ json: async () => ({ plugins: [{ id: 'latte', name: 'Latte' }, { id: 'plain', name: 'Plain' }] }) }) })
  const row = version => [{ id: 'latte', name: 'Latte & Newspaper', image: 'cover.webp', image_position: '62% 45%', api: 0, effective: true, layer: 'shipped', kind: ['theme'], version }]
  await refresh(row('1.0.0'))
  const first = head.childNodes[1]
  assert.equal(head.firstChild, initial)
  assert.equal(first.href, '/themes.css?plugins=1')
  await refresh(row('1.0.1'))
  const second = head.childNodes[1]
  assert.equal(first.parentNode, null)
  first.onload() // A stale completion cannot remove the current sheet.
  assert.equal(head.firstChild, initial)
  second.onload()
  assert.deepEqual(head.childNodes, [second])
  await refresh(row('1.0.2'))
  const failed = head.childNodes[1]
  failed.onerror()
  assert.deepEqual(head.childNodes, [second])
  await refresh(row('1.0.3'))
  head.childNodes[1].onload()
  assert.equal(head.childNodes.length, 1)
  assert.equal(head.firstChild.href, '/themes.css?plugins=4')
  assert.equal(installed.length, 4)
  assert.equal(installed.at(-1)[0].image, 'cover.webp')
  assert.equal(installed.at(-1)[0].image_position, '62% 45%')
})

test('live refresh fills dark-cover metadata omitted by an older host without hiding broken themes', async () => {
  const doc = installDom(); doc.querySelector = () => null
  const shipped = await shippedPlugins('public'), autumn = shipped.find(row => row.id === 'autumn')
  const { image_dark, image_position_dark, ...oldRow } = autumn
  oldRow.options = [{ id:'agent-shapes', label:'Agent shapes', default:'distinct', choices:[{id:'distinct',label:'Each its own'},{id:'circle',label:'Circle'},{id:'rounded-square',label:'Rounded square'}] }]
  let installed
  const refresh = createPluginThemes({ theme: { install: catalog => { installed = catalog }, apply() {} }, request: async () => ({ json: async () => ({ plugins: shipped }) }) })
  await refresh([{ ...oldRow, warnings: ['Unknown field: image_dark'] }])
  assert.equal(installed.find(t => t.id === 'autumn').image_dark, 'cover-dark.webp')
  assert.equal(installed.find(t => t.id === 'autumn').image_position_dark, '50% 42%')
  assert.deepEqual(installed.find(t => t.id === 'autumn').options, autumn.options)
  await refresh([{ ...autumn, broken: 'A real validation failure' }, shipped.find(row => row.id === 'plain')])
  assert.equal(installed.some(t => t.id === 'autumn'), false)
})
