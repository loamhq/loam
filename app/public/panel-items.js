import { h } from './ui.js'
import { appURL } from './apps.js'
import { filesHref, parseFilesPath } from './files.js'
import { threadHref, threadFromPath } from './thread-opening.js'

// Addresses contain identities only. Runtime records, editors and transports
// stay with their mount; neither the navigator nor history owns those objects.
export function itemDescriptor(item) {
  if (item?.kind === 'thread' && typeof item.id === 'string' && item.id) return { kind: 'thread', id: item.id }
  // A file of a plugin's own root (`@<id>`, files.root) opens in a panel too.
  if (item?.kind === 'file' && (['vault', 'spaces'].includes(item.root) || /^@[a-z0-9][a-z0-9._-]*$/.test(item.root ?? '')) && typeof item.path === 'string' && item.path && !item.path.split('/').some(s => s === '..' || s === '.')) return { kind: 'file', root: item.root, path: item.path }
  if (item?.kind === 'app' && appURL(item.app?.url ?? item.url)) return { kind: 'app', url: item.app?.url ?? item.url }
  if (typeof item?.kind === 'string' && !['thread', 'file', 'app'].includes(item.kind) && /^[a-z][a-z0-9:._-]*$/i.test(item.kind) && typeof item.id === 'string' && item.id) return { kind: item.kind, id: item.id }
  return null
}
export function itemHref(item) {
  const at = itemDescriptor(item)
  if (!at) return '/'
  if (at.kind === 'directory' && parseFilesPath(at.id)) return at.id
  if (at.kind === 'catalog' && at.id === 'apps') return '/apps'
  if (at.kind === 'thread') return threadHref(at.id)
  if (at.kind === 'file') return filesHref(at.root, at.path)
  if (at.kind === 'app') return `/apps?url=${encodeURIComponent(at.url)}`
  return `/panel?kind=${encodeURIComponent(at.kind)}&id=${encodeURIComponent(at.id)}`
}
export function itemFromURL(value) {
  const url = new URL(value, 'http://placeholder'), id = threadFromPath(url.pathname)
  if (id) return { kind: 'thread', id }
  const file = parseFilesPath(url.pathname)
  if (file?.root && file.path) return { kind: 'file', ...file }
  if (url.pathname === '/apps') return itemDescriptor({ kind: 'app', url: url.searchParams.get('url') })
  if (url.pathname === '/panel') return itemDescriptor({ kind: url.searchParams.get('kind'), id: url.searchParams.get('id') })
  return null
}

export function placeHref(place) {
  if (place === 'chats') return '/'
  if (place === 'files') return '/files'
  if (place === 'apps') return '/apps'
  return '/?screen=' + encodeURIComponent(`plugin:${place}`)
}

export function placeFromURL(url) {
  if (parseFilesPath(url.pathname)) return 'files'
  if (url.pathname === '/apps') return 'apps'
  const screen = url.searchParams.get('screen')
  return screen?.startsWith('plugin:') ? screen.slice(7) : 'chats'
}

// A pasted address gets the same ancestors as a visit from its own place.
// A reload of an existing visit keeps the route the reader actually took.
export function panelAncestors(url) {
  if (url.pathname === '/' && url.searchParams.get('chats') === 'archived') return ['/']
  const folder = parseFilesPath(url.pathname)
  if (folder?.root === 'system') return []
  if (folder?.root) {
    const parts = folder.path.split('/').filter(Boolean)
    return [filesHref(null), ...parts.map((_, i) => filesHref(folder.root, parts.slice(0, i).join('/')))]
  }
  const item = itemFromURL(url)
  if (item?.kind === 'thread') return ['/', ...(['board', 'details'].includes(url.searchParams.get('view')) ? [threadHref(item.id)] : [])]
  if (item) return [placeHref(placeFromURL(url))]
  return []
}

export function sideFromURL(value) {
  try { return itemDescriptor(JSON.parse(new URL(value, 'http://placeholder').searchParams.get('side'))) } catch { return null }
}
export function withSide(value, item) {
  const url = new URL(value, 'http://placeholder'), at = itemDescriptor(item)
  if (at) url.searchParams.set('side', JSON.stringify(at))
  else url.searchParams.delete('side')
  return url.pathname + url.search + url.hash
}

// Upgrade the former address-less app/side store once. Subsequent loads and
// history entries take their identities entirely from the URL.
export function migratePanelURL(value, saved) {
  const url = new URL(value, 'http://placeholder')
  if (!saved.legacy) return url.pathname + url.search + url.hash
  let main = value
  if (url.pathname === '/' && !url.searchParams.has('threads') && !url.searchParams.has('session') && saved.legacyMain?.kind === 'app') {
    const next = new URL(itemHref(saved.legacyMain), url)
    for (const [key, value] of url.searchParams) if (!['side', 'url'].includes(key)) next.searchParams.append(key, value)
    next.hash = url.hash
    main = next.href
  }
  return withSide(main, url.searchParams.has('side') ? sideFromURL(value) : saved.side)
}

// Both slots use this renderer. Sections supply lists; item kinds supply only
// panel contents. The stable slot ancestors keep iframe documents mounted.
export function renderPanel({ host, item, before = [], after = [], header = null, empty = null, context = {} }) {
  const side = host.panelId === 'side'
  const kind = host.registry.entries('panel.item').findLast(entry => entry.id === item?.kind)
  const content = item ? kind ? host.registry.invoke(kind, 'render', { ...context, host, item, panel: host.panelId, wide: true })
    : h('div', { class: 'pane-nothing', role: 'status', text: 'This item is unavailable. Its plugin may be off.' }) : empty
  return h(side ? 'section' : 'main', { class: `pane${side ? ' side-pane' : ''}`, key: side ? 'side-pane' : 'main-pane', 'data-panel': host.panelId, ...(side ? { 'aria-label': 'Side panel' } : {}) },
    ...before, header, content, ...after)
}
