import test from 'node:test'
import assert from 'node:assert/strict'
import { readConfig } from '../src/config.js'
import { fileLink } from '../src/file-links.js'
import { copyLinkLabel } from '../public/files.js'

test('published subtree mappings preserve base paths and prefer the most specific folder', () => {
  const config = readConfig({ FILES_WEB_URLS: JSON.stringify([
    { root: 'spaces', path: 'shared/www', url: 'https://files.example.org/pages/' },
    { root: 'spaces', path: 'shared/www/special', url: 'https://special.example.org/' },
  ]) })
  assert.equal(fileLink(config, 'spaces', 'shared/www/a b/index.html').url, 'https://files.example.org/pages/a%20b/index.html')
  assert.equal(fileLink(config, 'spaces', 'shared/www/special/index.html').url, 'https://special.example.org/index.html')
  assert.equal(fileLink(config, 'spaces', 'shared/www').url, 'https://files.example.org/pages/')
  assert.equal(fileLink(config, 'vault', 'a b.html').url, '/files/vault/a%20b.html')
  assert.equal(fileLink(config, 'spaces', 'other/www/index.html').kind, 'Loam link')
})

test('invalid publication mappings fail startup instead of producing misleading links', () => {
  for (const raw of ['no json', '{}', '[null]', ...[
    { root: 'spaces', path: '../www', url: 'https://files.example.org' },
    { root: 'spaces', path: '/www', url: 'https://files.example.org' },
    { root: 'spaces', path: 'a//www', url: 'https://files.example.org' },
    { root: 'spaces', path: 'www', url: 'http://files.example.org' },
    { root: 'spaces', path: 'www', url: 'https://user:pass@files.example.org' },
    { root: 'spaces', path: 'www', url: 'https://files.example.org/?path=' },
    { root: 'spaces', path: 'www', url: 'https://files.example.org/#page' },
  ].map(row => JSON.stringify([row]))]) assert.throws(() => readConfig({ FILES_WEB_URLS: raw }), /FILES_WEB_URLS/)
})

test('Copy link explicitly labels an app-only destination', () => {
  assert.equal(copyLinkLabel({}), 'Checking link…')
  assert.equal(copyLinkLabel({ shareLink: { kind: 'Loam link' } }), 'Copy app link')
  assert.equal(copyLinkLabel({ shareLink: { kind: 'Vault web link' } }), 'Copy link')
  assert.equal(copyLinkLabel({ shareLink: { kind: 'Web link' } }), 'Copy link')
})
