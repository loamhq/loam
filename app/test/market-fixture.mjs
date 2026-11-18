// The Market against a fixture broker (2026-11-18): an invented list (no
// listing here is in the public marketplace), each listing's details as the
// broker answers them, the themes' own token CSS, and the install path's
// two calls. Used by test/market-browser.mjs and tools/capture-market.mjs.
import { readFile } from 'node:fs/promises'
import { oneProductFixture } from './one-product-fixture.mjs'
import { shippedPlugins } from '../src/shipped-plugins.js'

const link = (repo, id, tag = 'v1.0.0') => `https://github.com/${repo}/tree/${tag}/${id}`
const at = (repo, id, tag = 'v1.0.0') => ({ link: link(repo, id, tag), repo, ref: tag, path: id })
export const LISTINGS = [
  { id: 'images', name: 'Images', author: 'Loam contributors', kind: ['extension'], version: '1.0.0', featured: true,
    description: 'Lets your agents make and edit pictures, and put them in their replies.', ...at('loamhq/loam', 'images', 'v0.2.3'),
    declaration: { touches: { network: [{ host: 'api.openai.com', why: 'sends the description of a picture you asked for' }], record: 'none', memory: 'none', server: true, agents: { tools: ['generate_image (chat, wake)'], prompt: [] }, keys: [{ provider: 'openai', name: 'OpenAI', why: 'draws the pictures' }] }, overrides: [], page_code: false },
    guide: 'Any agent can draw on request: a cover for an essay, a diagram, a picture to go with a story. Pictures are saved as ordinary image files in the vault.', icon: true },
  { id: 'pocket-sync', name: 'Pocket Sync', author: 'Ada Park', kind: ['extension'], version: '0.4.0',
    description: 'Keeps your vault in step with your phone and laptop, device to device, with no cloud between.', ...at('ada-park/pocket-sync', 'pocket-sync', 'v0.4.0'),
    declaration: { touches: { network: [{ host: 'your own devices', why: 'syncs vault files directly between them' }], record: 'none', memory: 'none', server: true }, overrides: [], page_code: false } },
  { id: 'a-pet', name: 'A pet', author: 'Sam Rivera', kind: ['extension'], version: '1.0.0',
    description: 'A small animal that sleeps in the corner of the page and stretches when an agent starts working.', ...at('sam-rivera/loam-pet', 'a-pet'),
    declaration: { touches: { network: [], record: 'none', memory: 'none', server: false }, overrides: [], page_code: true } },
  { id: 'ember', name: 'Ember', author: 'Loam contributors', kind: ['theme'], version: '1.0.0', from: 'autumn',
    description: 'Rust, amber and paper. Warm without being loud.', ...at('loamhq/themes', 'ember') },
  { id: 'moss', name: 'Moss', author: 'Ada Park', kind: ['theme'], version: '1.1.0', from: 'deep-forest',
    description: 'Moss, bark and low light. Calm for long evenings.', ...at('ada-park/moss', 'moss', 'v1.1.0') },
  { id: 'tide', name: 'Tide', author: 'Sam Rivera', kind: ['theme'], version: '1.0.0', from: 'yosemite',
    description: 'Granite and sky. Clear, cool and bright.', ...at('sam-rivera/tide', 'tide') },
  { id: 'story-desk', name: 'Story desk', author: 'Ada Park', kind: ['app'], version: '0.9.0',
    description: 'A place to write a long story with an agent, chapter by chapter, with the world kept in a bible.', ...at('ada-park/story-desk', 'story-desk', 'v0.9.0'),
    declaration: { touches: { network: [], record: 'read', memory: 'none', server: true }, overrides: [], page_code: false } },
  { id: 'habit-wall', name: 'Habit wall', author: 'Sam Rivera', kind: ['app'], version: '1.2.0',
    description: 'A wall of small squares for the things you do each day, filled in by you or by an agent.', ...at('sam-rivera/habit-wall', 'habit-wall', 'v1.2.0'),
    declaration: { touches: { network: [], record: 'none', memory: 'none', server: true }, overrides: [], page_code: false } },
  { id: 'running-coach', name: 'Running coach', author: 'Sam Rivera', kind: ['agent'], version: '1.0.0',
    description: 'An agent with a coach’s character: patient, exact about load, honest when you push too hard.', ...at('sam-rivera/coach', 'running-coach'),
    declaration: { touches: { network: [], record: 'none', memory: 'write', server: false }, overrides: [], page_code: false }, installable: false, why: 'Loam reads and lists agent packages and installs none yet' },
  { id: 'essay-shaping', name: 'Essay shaping', author: 'Loam contributors', kind: ['skill'], version: '1.0.0',
    description: 'Teaches every agent one way to take writing from notes to a draft with a spine.', ...at('loamhq/skills', 'essay-shaping'),
    declaration: { touches: { network: [], record: 'none', memory: 'read', server: false }, overrides: [], page_code: false }, installable: false, why: 'Loam reads and lists skill packages and installs none yet' },
  { id: 'meeting-notes', name: 'Meeting notes', author: 'Ada Park', kind: ['skill'], version: '0.3.0',
    description: 'Turns a transcript into decisions, owners and dates, filed beside the meeting.', ...at('ada-park/meeting-notes', 'meeting-notes', 'v0.3.0'),
    declaration: { touches: { network: [], record: 'write', memory: 'none', server: false }, overrides: [], page_code: false }, installable: false, why: 'Loam reads and lists skill packages and installs none yet' },
]
const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><rect width="48" height="48" rx="12" fill="#B4733A"/><circle cx="33" cy="15" r="6" fill="#F6EDDF"/><path d="M6 40 L19 22 L28 33 L33 27 L42 40Z" fill="#2F2017"/></svg>'

// A thread where agents offer listings from the Market (2026-11-18): one
// card, two together, and a line naming a listing the Market does not have,
// which reads as its words. The lines are the ones bin/loam-market search
// hands an agent as `card`.
export const CARD_ORIGIN = 'https://loam.example'
export const cardLine = (name, id) => `[${name}](${CARD_ORIGIN}/?modal=market&listing=${id})`
export const CARD_MESSAGES = [
  { id: 'm5', author: 'you', kind: 'message', text: 'Could Loam keep my notes on my phone too? I keep wanting them on the train.', did: [] },
  { id: 'm6', author: 'sprout', kind: 'message', did: [{ label: 'Looked in the Market' }],
    text: `There’s one in the Market that does exactly this: it keeps the vault in step with your phone and laptop, device to device.\n\n${cardLine('Pocket Sync', 'pocket-sync')}\n\nIt runs a small program on your server and talks only to your own devices. If you’d rather have one of your own, I can build it.` },
  { id: 'm7', author: 'you', kind: 'message', text: 'And something softer for the evenings?', did: [] },
  { id: 'm8', author: 'quill', kind: 'message', did: [{ label: 'Looked in the Market' }],
    text: `Two that suit long evenings:\n\n${cardLine('Moss', 'moss')}\n${cardLine('Tide', 'tide')}\n\nYou can try either on before you get it.` },
]
export const UNKNOWN_CARD = { id: 'm9', author: 'sprout', kind: 'message', did: [], text: `I also remembered this one, though it may have left the Market:\n\n${cardLine('Nightjar', 'nightjar')}` }

export async function marketFixture({ mode = 'light', look = 'latte', listings = LISTINGS, slow = 0, cards = null } = {}) {
  const app = await oneProductFixture(), writes = []
  if (cards) {
    const route = 'GET /v1/threads/a-quieter-week', read = app.routes[route]
    app.routes[route] = (req, res, url, raw) => {
      const end = res.end.bind(res)
      res.end = body => {
        try {
          const record = JSON.parse(body)
          const at = new Date(Date.parse(record.messages.at(-1)?.at ?? '2026-11-18T21:00:00Z') + 60000)
          record.messages.push(...cards.map((m, i) => ({ ...m, author: m.author === 'you' ? 'you' : m.author, at: new Date(+at + i * 60000).toISOString() })))
          record.thread.unread = 0
          return end(JSON.stringify(record))
        } catch { return end(body) }
      }
      return read(req, res, url, raw)
    }
  }
  const json = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  const wait = () => new Promise(resolve => setTimeout(resolve, slow))
  const appearance = { phone: { mode, size: 100, theme: look }, desktop: { mode, size: 100, theme: look } }
  app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, 200, appearance)
  app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => { for (const [kind, patch] of Object.entries(JSON.parse(raw))) Object.assign(appearance[kind], patch); json(res, 200, appearance) }
  const css = new Map()
  for (const entry of listings.filter(e => e.from)) css.set(entry.id, (await readFile(`public/themes/${entry.from}/theme.css`, 'utf8')).replaceAll(`data-look="${entry.from}"`, `data-look="${entry.id}"`))
  const base = await shippedPlugins('public')
  const installed = new Map()
  const rowOf = entry => ({ id: entry.id, name: entry.name, version: entry.version, author: entry.author, api: 0, kind: entry.kind, places: [], overrides: [], layer: 'yours', folder: `fixtures/${entry.id}`, effective: true, on: true,
    switch: entry.kind.includes('theme') ? 'appearance' : 'plugin', broken: null, warnings: [], has: { page_main: false, theme: entry.kind.includes('theme') }, touches: declarationOf(entry).touches, declaration: declarationOf(entry),
    source: { kind: 'link', repo: entry.repo, ref: entry.ref, ref_kind: 'tag', commit: 'c'.repeat(40), url: entry.link } })
  const declarationOf = entry => entry.declaration ?? { touches: { network: [], record: 'none', memory: 'none', server: false }, overrides: [], page_code: false }
  const rows = () => [...base, ...installed.values()]
  app.routes['GET /v1/plugins'] = (req, res) => json(res, 200, { api: 0, layers: { shipped: [], yours: 'fixture' }, plugins: rows() })
  const list = () => ({ url: 'https://example.invalid/list.json', state: listings.length ? 'listed' : 'empty', checked_at: '2026-11-18T19:30:00Z', skipped: [], message: null, repo: 'loamhq/marketplace',
    plugins: listings.map(({ declaration, guide, icon, from, installable, why, ...entry }) => ({ ...entry, installed: installed.has(entry.id), installed_version: installed.get(entry.id)?.version ?? null, installed_from: installed.has(entry.id) ? entry.repo : null })) })
  app.routes['GET /v1/marketplace'] = async (req, res) => { writes.push({ method: 'GET', path: '/v1/marketplace' }); if (slow) await wait(); json(res, 200, list()) }
  for (const entry of listings) {
    const theme = css.has(entry.id)
    app.routes[`GET /v1/marketplace/${entry.id}`] = async (req, res) => {
      if (slow) await wait()
      json(res, 200, { id: entry.id, listing: list().plugins.find(e => e.id === entry.id), name: entry.name, version: entry.version, author: entry.author, kind: entry.kind,
        declaration: declarationOf(entry), guide: entry.guide ?? null, warnings: [], installable: entry.installable !== false, why: entry.why ?? null,
        source: { kind: 'link', repo: entry.repo, path: entry.path, ref: entry.ref, ref_kind: 'tag', commit: 'c'.repeat(40), url: entry.link }, icon: Boolean(entry.icon), image: false, theme: theme ? { options: [] } : null })
    }
    if (entry.icon) app.routes[`GET /v1/marketplace/${entry.id}/icon`] = (req, res) => { res.writeHead(200, { 'content-type': 'image/svg+xml' }); res.end(ICON) }
    if (theme) {
      app.routes[`GET /v1/marketplace/${entry.id}/theme.css`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/css; charset=utf-8' }); res.end(css.get(entry.id)) }
      app.routes[`GET /v1/plugins/${entry.id}/files/theme.css`] = (req, res) => { res.writeHead(200, { 'content-type': 'text/css; charset=utf-8' }); res.end(css.get(entry.id)) }
    }
  }
  app.routes['POST /v1/plugins'] = async (req, res, url, raw) => {
    const body = JSON.parse(raw); writes.push({ method: 'POST', path: '/v1/plugins', body })
    const entry = listings.find(e => e.link === body.url)
    if (!entry) return json(res, 404, { error: 'No plugin at that link.' })
    const preview = { installed: false, id: entry.id, name: entry.name, version: entry.version, author: entry.author, api: 0, kind: entry.kind, places: [],
      source: { kind: 'link', repo: entry.repo, path: entry.path, ref: entry.ref, ref_kind: 'tag', commit: 'c'.repeat(40), url: entry.link },
      declaration: declarationOf(entry), added: declarationOf(entry).touches, replaces: null, shadows: null, warnings: [], confirm: `ok-${entry.id}`,
      ...(entry.installable === false ? { installable: false, why: entry.why } : {}) }
    if (slow) await wait()
    if (!body.confirm) return json(res, 200, preview)
    if (entry.installable === false) return json(res, 409, { error: `${entry.id} is not installable yet`, preview })
    installed.set(entry.id, rowOf(entry))
    json(res, 200, { installed: true, id: entry.id, plugin: rowOf(entry), replaced: null, source: preview.source, server: null })
  }
  return { ...app, writes, appearance, installed }
}
