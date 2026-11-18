import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { all, find, text } from './browser.js'
import { h } from '../public/ui.js'
import { createApiKeys, providerLine, providerWarn } from '../public/api-keys.js'

const openai = (over = {}) => ({ id: 'openai', name: 'OpenAI', where: 'https://platform.openai.com/api-keys', checks: true,
  fields: [{ name: 'OPENAI_API_KEY', label: 'API key', secret: true }], set: false, source: null, shown: {}, checked: null, refused: null,
  plugins: [{ id: 'images', name: 'Images', on: true, why: 'Makes images', optional: true, uses: 'shared', has_key: false, checked: null }], ...over })

test('a provider line says whether a key is there and who uses it', () => {
  assert.equal(providerLine(openai()), 'No key · for Images')
  assert.equal(providerLine(openai({ plugins: [{ ...openai().plugins[0], optional: false }] })), 'No key · Images needs one')
  assert.equal(providerLine(openai({ set: true, source: 'saved' })), 'Key saved · Images')
  assert.equal(providerLine(openai({ set: true, source: 'box', plugins: [] })), 'Set on this server')
  assert.equal(providerLine(openai({ set: true, source: 'saved', checked: { ok: false } })), 'Key refused when last checked · Images')
  assert.equal(providerWarn(openai()), false, 'an optional key missing is no alarm')
  assert.equal(providerWarn(openai({ plugins: [{ ...openai().plugins[0], optional: false }] })), true)
})

test('Accounts lists providers, and a saved key is sent once and never kept in the page', async () => {
  installDom()
  const calls = []
  let list = [openai(), { ...openai({ id: 'google', name: 'Google Gemini', plugins: [] }) }]
  const api = async (method, path, body) => {
    calls.push([method, path, body])
    if (method === 'GET') return { providers: list }
    if (method === 'PUT') { list = [openai({ set: true, source: 'saved', shown: { OPENAI_API_KEY: '…1234' }, checked: { ok: true, why: 'OpenAI accepted the key.' } }), list[1]]; return list[0] }
    return list[0]
  }
  const keys = createApiKeys({ api })
  await keys.load()
  const root = h('div', {}, keys.view({ open: () => {} }))
  assert.deepEqual(all(root, 'setrow-title').map(text), ['OpenAI', 'Google Gemini'])
  const page = h('div', {}, keys.view({ provider: 'openai', back: null }))
  assert.equal(text(find(page, 'sheet-title')), 'OpenAI')
  assert.ok(text(page).includes('Used by'))
  const form = find(page, 'keys-form'), input = find(form, 'keys-input')
  assert.equal(input.getAttribute('type'), 'password')
  input.value = 'sk-synthetic-000001234'
  form.__on.submit({ preventDefault() {}, currentTarget: form })
  await new Promise(r => setTimeout(r, 0))
  assert.deepEqual(calls.at(-1), ['PUT', '/keys/openai', { values: { OPENAI_API_KEY: 'sk-synthetic-000001234' } }])
  assert.equal(input.value, '', 'the field is cleared before the request goes')
  assert.ok(!JSON.stringify(keys.st).includes('sk-synthetic'), 'no value is kept in the page state')
  assert.equal(keys.st.message, 'OpenAI accepted the key.')
  const after = h('div', {}, keys.view({ provider: 'openai', back: null }))
  assert.ok(text(after).includes('…1234'))
  assert.deepEqual(all(after, 'pillbtn').map(text).slice(0, 2), ['Replace key', 'Forget'])
  assert.equal(text(find(after, 'keys-recheck')), 'Check again')
  assert.equal(keys.summary(), '1 key')
})

test('a server without shared keys says so plainly', async () => {
  installDom()
  const keys = createApiKeys({ api: async () => { const e = new Error('nope'); e.status = 404; throw e } })
  await keys.load()
  assert.match(text(h('div', {}, keys.view({}))), /does not hold shared keys yet/)
})
