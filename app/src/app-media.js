import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile, rename, rm, readdir, stat } from 'node:fs/promises'
import path from 'node:path'
import { captureAppPage } from './app-capture.js'
import { createAppIcons } from './app-icons.js'
import { previewTarget, createPreviewFetch } from './app-preview-route.js'

const hash = value => createHash('sha256').update(value).digest('hex')
const HOUR = 3600000
// Refresh entries skipped by the former framing gate or full-budget waits.
const CAPTURE_REVISION = 2
export function previewable(app, appsDomain) {
  if (app.kind === 'plugin') return /^\/x\/[a-z0-9][a-z0-9_-]*\/$/i.test(app.url) && app.on && app.state === 'running'
  try { const url = new URL(app.url); return url.protocol === 'https:' && !url.port && !url.username && !url.password && url.hostname.endsWith(`.${appsDomain}`) } catch { return false }
}
export function createAppMedia({ dir = '/storage/app-media', chrome = 'chromium', oomScoreAdj = null, ttlHours = 6, appsDomain = '', previewBase = '', previewAddr = '' } = {}, {
  capture = captureAppPage, icons = null, now = Date.now, log = null, read = readFile,
} = {}) {
  icons ??= createAppIcons({ fetcher: createPreviewFetch({ base: previewBase, addr: previewAddr }) })
  const tasks = new Map(), notes = new Map(), reading = new Map()
  let capturing = false
  const queue = []
  const ready = mkdir(path.join(dir, 'tmp'), { recursive: true }).then(() => true).catch(() => false)
  const location = app => path.join(dir, hash(app.url))
  const fingerprint = app => hash(JSON.stringify([CAPTURE_REVISION, app.url, app.kind, app.frame, app.on, app.state, app.created, app.updated, app.modified, app.version, app.icon,
    ...(previewBase && ['site', 'project'].includes(app.kind) ? [previewBase, previewAddr] : [])]))
  const descriptor = (app, note, pending = false) => {
    const base = `/api/apps/media/image?url=${encodeURIComponent(app.url)}`
    return { url: app.url, pending, refresh_after_ms: note?.until ? Math.max(0, note.until - now()) : null,
      preview: note?.preview ? `${base}&kind=preview&v=${note.preview}` : null, icon: note?.icon ? `${base}&kind=icon&v=${note.icon}` : null }
  }
  // One read of an app's note at a time, and a read never replaces a note
  // a capture has set meanwhile: two resolves at once used to read twice,
  // and a read that answered after the capture finished put its stale
  // "none" back and started a second capture.
  function noteFor(app) {
    if (notes.has(app.url)) return Promise.resolve(notes.get(app.url))
    if (!reading.has(app.url)) {
      reading.set(app.url, Promise.resolve()
        .then(() => read(path.join(location(app), 'meta.json'), 'utf8'))
        .then(text => JSON.parse(text), () => null).catch(() => null)
        .then(note => {
          reading.delete(app.url)
          if (!notes.has(app.url)) notes.set(app.url, note)
          return notes.get(app.url)
        }))
    }
    return reading.get(app.url)
  }
  async function atomic(file, body) { await writeFile(file + '.new', body); await rename(file + '.new', file) }
  async function sweep() {
    const rows = []
    for (const entry of await readdir(dir)) if (/^[a-f0-9]{64}$/.test(entry)) {
      const info = await stat(path.join(dir, entry)).catch(() => null)
      if (info?.isDirectory()) rows.push({ entry, touched: info.mtimeMs })
    }
    rows.sort((a, b) => b.touched - a.touched)
    const protectedIDs = new Set([...tasks.keys()].map(hash))
    let retained = rows.filter(row => protectedIDs.has(row.entry)).length
    for (const row of rows) {
      if (protectedIDs.has(row.entry)) continue
      if (retained >= 128 || now() - row.touched > 7 * 24 * HOUR) {
        await rm(path.join(dir, row.entry), { recursive: true, force: true })
        for (const url of notes.keys()) if (hash(url) === row.entry) notes.delete(url)
      } else retained++
    }
  }
  async function photograph(options) {
    if (capturing) await new Promise(resolve => queue.push(resolve))
    capturing = true
    const started = performance.now()
    try { return { body: await capture(options), ms: Math.round(performance.now() - started) } }
    finally { const next = queue.shift(); if (next) next(); else capturing = false }
  }
  async function refresh(app, origin, previous) {
    const folder = location(app)
    await mkdir(folder, { recursive: true })
    const target = previewTarget(app, { origin, base: previewBase, addr: previewAddr })
    const eligible = previewable(app, appsDomain)
    const [picture, mark] = await Promise.allSettled([
      eligible ? photograph({ url: target.url, dir: path.join(dir, 'tmp'), executable: chrome, ...(oomScoreAdj !== null ? { oomScoreAdj } : {}), ...(target.hostResolverRules ? { hostResolverRules: target.hostResolverRules } : {}) }) : Promise.resolve(null),
      icons(target.url, { icon: app.icon ? target.rewrite(new URL(app.icon, app.kind === 'plugin' ? origin : app.url).href) : null, rewrite: target.rewrite }),
    ])
    const note = { fingerprint: fingerprint(app), checked: now(), until: now() + ttlHours * HOUR,
      preview: previous?.preview ?? null, icon: null, iconType: null }
    if (picture.status === 'fulfilled' && picture.value?.body) {
      await atomic(path.join(folder, 'preview.jpg'), picture.value.body)
      note.preview = hash(picture.value.body)
      log?.info({ event: 'app-preview', app: app.name, state: 'ok', ms: picture.value.ms })
    } else if (!eligible) note.preview = null
    else {
      note.until = now() + Math.min(ttlHours * HOUR, HOUR / 2)
      log?.warn({ event: 'app-preview', app: app.name, state: note.preview ? 'kept' : 'fallback', message: picture.reason?.message || 'Page preview is unavailable.' })
    }
    if (mark.status === 'fulfilled' && mark.value) {
      await atomic(path.join(folder, 'icon'), mark.value.body)
      note.icon = hash(mark.value.body); note.iconType = mark.value.type
    }
    await atomic(path.join(folder, 'meta.json'), JSON.stringify(note))
    notes.set(app.url, note)
    await sweep().catch(() => {})
  }
  async function resolve(app, { origin }) {
    if (!await ready) return descriptor(app, null)
    const note = await noteFor(app)
    if (note?.fingerprint === fingerprint(app) && note.until > now()) return descriptor(app, note)
    if (!tasks.has(app.url) && tasks.size < 64) {
      const task = refresh(app, origin, note).catch(() => {
        notes.set(app.url, { ...note, fingerprint: fingerprint(app), until: now() + HOUR / 2 })
      }).finally(() => tasks.delete(app.url))
      tasks.set(app.url, task)
    }
    return descriptor(app, note, tasks.has(app.url))
  }
  async function image(app, kind) {
    if (!['preview', 'icon'].includes(kind)) return null
    const note = await noteFor(app)
    if (!note?.[kind]) return null
    try { return { body: await readFile(path.join(location(app), kind === 'preview' ? 'preview.jpg' : 'icon')), type: kind === 'preview' ? 'image/jpeg' : note.iconType } } catch { return null }
  }
  return { resolve, image, idle: () => Promise.all([...tasks.values()]) }
}
