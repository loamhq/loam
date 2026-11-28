// Inline visuals in agent replies: sealed iframe host, live theme & font sync,
// auto-height without chrome, calm streaming placeholder, Expand dialog, and
// Save to Files.
import { h, kept, icon, dress } from './ui.js'
import { installation } from './installation.js'
import {
  VISUAL_TOKENS,
  visualTitle,
  visualFallbackLine,
  holdPartialVisualFence,
  visualFilename,
  buildStandaloneVisualHtml,
} from './visual-doc.js'

const FRAME_TIMEOUT_MS = 8000
const VISUALS_MAX = 200
const visualViews = new Map()
const liveFrames = new Map()
let visualSerial = 0
let frameTokenSerial = 0
let listenersInstalled = false
let broadcastQueued = false

// ------------------------------------------------------------------ hashing

function hashText(text) {
  let h1 = 0x811c9dc5
  const s = String(text ?? '')
  for (let i = 0; i < s.length; i += 1) {
    h1 ^= s.charCodeAt(i)
    h1 = Math.imul(h1, 0x01000193)
  }
  return `${s.length}:${(h1 >>> 0).toString(36)}`
}

function nextFrameId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `v-${Date.now().toString(36)}-${++frameTokenSerial}`
}

// ------------------------------------------------------ tokens & local fonts

export function collectVisualTokens(hostEl = null) {
  const root = globalThis.document?.documentElement
  const getStyle = globalThis.getComputedStyle
  const tokens = {}
  if (!root || typeof getStyle !== 'function') {
    return { tokens, mode: 'light', look: 'latte' }
  }
  const rootStyle = getStyle(root)
  const names = new Set(VISUAL_TOKENS)
  // Pick up any additional custom properties defined by installed plugin themes.
  for (let i = 0; i < rootStyle.length; i += 1) {
    const prop = rootStyle[i]
    if (prop && prop.startsWith('--')) names.add(prop)
  }
  for (const name of names) {
    const val = rootStyle.getPropertyValue(name).trim()
    if (val) tokens[name] = val
  }
  // Resolve `--reading-ground` from the visual's own pane/container so desktop
  // panes (`--paper-pane`) and phones (`--paper-page`) match seamlessly.
  const target = hostEl?.isConnected ? hostEl : root
  const ground = getStyle(target).getPropertyValue('--reading-ground').trim()
  if (ground) tokens['--reading-ground'] = ground
  const mode = root.dataset?.theme === 'dark' ? 'dark' : 'light'
  const look = root.dataset?.look || 'latte'
  return { tokens, mode, look }
}

const fontDataCache = new Map()
const lookFontsCache = new Map()

function bytesToBase64(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const step = 0x8000
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode(...bytes.subarray(i, i + step))
  }
  return globalThis.btoa(binary)
}

async function fontUrlToDataUri(url) {
  if (fontDataCache.has(url)) return fontDataCache.get(url)
  const task = (async () => {
    try {
      if (typeof globalThis.fetch !== 'function' || typeof globalThis.btoa !== 'function') return null
      const res = await globalThis.fetch(url, { credentials: 'same-origin' })
      if (!res.ok) return null
      const mime = /\.woff(?:\?|$)/i.test(url) ? 'font/woff' : 'font/woff2'
      const buf = await res.arrayBuffer()
      return `data:${mime};base64,${bytesToBase64(buf)}`
    } catch {
      return null
    }
  })()
  fontDataCache.set(url, task)
  return task
}

async function inlineCssFonts(cssText) {
  const matches = [...String(cssText ?? '').matchAll(/url\(\s*(['"]?)(\/[^'")]+)\1\s*\)/g)]
  if (!matches.length) return String(cssText ?? '')
  const replacements = new Map()
  await Promise.all(
    [...new Set(matches.map((m) => m[2]))].map(async (url) => {
      if (!/^\/(?:fonts|api\/plugins\/[a-z0-9-]+\/files|api\/marketplace\/[a-z0-9-]+\/font)\//i.test(url)) return
      const dataUri = await fontUrlToDataUri(url)
      if (dataUri) replacements.set(url, dataUri)
    }),
  )
  return String(cssText ?? '').replace(/url\(\s*(['"]?)(\/[^'")]+)\1\s*\)/g, (whole, _q, url) => {
    const uri = replacements.get(url)
    return uri ? `url(${uri})` : whole
  })
}

async function fetchSheetText(href) {
  try {
    if (typeof globalThis.fetch !== 'function') return ''
    const res = await globalThis.fetch(href, { credentials: 'same-origin' })
    return res.ok ? await res.text() : ''
  } catch {
    return ''
  }
}

export function loadThemeFontsCss(look = 'latte') {
  const key = String(look || 'latte')
  if (lookFontsCache.has(key)) return lookFontsCache.get(key)
  const task = (async () => {
    if (typeof globalThis.fetch !== 'function') return ''
    const sheets = ['/fonts/fonts.css']
    if (key !== 'latte' && key !== 'plain' && /^[a-z][a-z0-9-]*$/.test(key)) {
      const marketLink = globalThis.document?.querySelector?.(`link[href^="/api/marketplace/${key}/fonts.css"]`)
      sheets.push(marketLink ? `/api/marketplace/${key}/fonts.css` : `/theme-fonts/${key}.css`)
    }
    const raw = (await Promise.all(sheets.map(fetchSheetText))).filter(Boolean).join('\n')
    return raw ? await inlineCssFonts(raw) : ''
  })()
  lookFontsCache.set(key, task)
  return task
}

// ------------------------------------------------------ frame message bridge

function pruneDisconnected() {
  for (const [id, ctrl] of liveFrames) {
    if (ctrl.iframe && ctrl.iframe.isConnected === false && ctrl.mountedOnce) {
      ctrl.cleanup?.()
      liveFrames.delete(id)
    }
  }
}

function sendThemeTo(ctrl, fontsCss = undefined) {
  if (!ctrl.ready || !ctrl.iframe?.contentWindow) return
  const snap = collectVisualTokens(ctrl.hostEl)
  const msg = {
    loamVisual: true,
    id: ctrl.id,
    type: 'theme',
    tokens: snap.tokens,
    mode: snap.mode,
    ...(typeof fontsCss === 'string' ? { fontsCss } : {}),
  }
  try {
    ctrl.iframe.contentWindow.postMessage(msg, '*')
  } catch { /* detached */ }
  if (fontsCss === undefined && snap.look !== ctrl.lastLook) {
    ctrl.lastLook = snap.look
    loadThemeFontsCss(snap.look).then((css) => {
      if (liveFrames.get(ctrl.id) === ctrl && ctrl.ready && ctrl.lastLook === snap.look) {
        sendThemeTo(ctrl, css)
      }
    })
  }
}

export function broadcastVisualTheme() {
  if (broadcastQueued) return
  broadcastQueued = true
  const run = () => {
    broadcastQueued = false
    pruneDisconnected()
    if (!liveFrames.size) return
    const { look } = collectVisualTokens()
    for (const ctrl of liveFrames.values()) sendThemeTo(ctrl)
    loadThemeFontsCss(look).then((css) => {
      for (const ctrl of liveFrames.values()) {
        if (ctrl.lastLook === look) sendThemeTo(ctrl, css)
      }
    })
  }
  if (typeof globalThis.requestAnimationFrame === 'function') globalThis.requestAnimationFrame(run)
  else Promise.resolve().then(run)
}

function watchStylesheetLink(node) {
  if (!node || node.nodeName !== 'LINK') return
  const href = node.getAttribute?.('href') || ''
  if (!/\/(?:themes\.css|theme-fonts\/|api\/marketplace\/[^/]+\/(?:theme|fonts)\.css)/.test(href)) return
  node.addEventListener?.('load', () => {
    lookFontsCache.clear()
    broadcastVisualTheme()
  })
}

function ensureGlobalListeners() {
  if (listenersInstalled) return
  listenersInstalled = true
  globalThis.window?.addEventListener?.('message', (event) => {
    const data = event.data
    if (!data || data.loamVisual !== true || typeof data.id !== 'string') return
    const ctrl = liveFrames.get(data.id)
    if (!ctrl || event.source !== ctrl.iframe?.contentWindow) return
    if (data.type === 'ready') {
      ctrl.onReady()
    } else if (data.type === 'size') {
      const hPx = Number(data.height)
      if (Number.isFinite(hPx) && hPx >= 1 && hPx <= 6000) {
        const naturalWidth = Number(data.naturalWidth)
        ctrl.onSize({
          height: Math.ceil(hPx),
          canExpand: Boolean(data.canExpand),
          naturalWidth: Number.isFinite(naturalWidth) && naturalWidth > 0 ? Math.ceil(naturalWidth) : 0,
        })
      }
    }
  })
  const root = globalThis.document?.documentElement
  if (root && typeof globalThis.MutationObserver === 'function') {
    const rootObserver = new MutationObserver(() => broadcastVisualTheme())
    rootObserver.observe(root, { attributes: true })
  }
  const head = globalThis.document?.head
  if (head && typeof globalThis.MutationObserver === 'function') {
    for (const link of head.querySelectorAll?.('link[rel="stylesheet"]') ?? []) watchStylesheetLink(link)
    const headObserver = new MutationObserver((records) => {
      for (const r of records) for (const node of r.addedNodes) watchStylesheetLink(node)
    })
    headObserver.observe(head, { childList: true })
  }
  globalThis.window?.addEventListener?.('resize', () => broadcastVisualTheme())
}

// ------------------------------------------------------------ save to Files

function encodeVaultPath(relPath) {
  return String(relPath ?? '')
    .split('/')
    .filter(Boolean)
    .map(encodeURIComponent)
    .join('/')
}

async function ensureFolder(api, folder) {
  const parts = String(folder ?? '').split('/').filter(Boolean)
  let cur = ''
  for (const part of parts) {
    cur = cur ? `${cur}/${part}` : part
    try {
      await api('POST', '/files/folder', { root: 'vault', path: cur })
    } catch (err) {
      if (err?.status !== 409) throw err
    }
  }
}

export async function saveVisualToFiles({ html, title = '', api, hostEl = null, now = new Date() }) {
  if (typeof api !== 'function') throw new Error('Saving to Files is not available here.')
  const snap = collectVisualTokens(hostEl)
  const fontsCss = await loadThemeFontsCss(snap.look)
  const standalone = buildStandaloneVisualHtml({
    html,
    title: title || 'Visual',
    tokens: snap.tokens,
    mode: snap.mode,
    fontsCss,
  })
  const folder = String(installation()?.inbox ?? '').replace(/^\/+|\/+$/g, '')
  const makePath = (suffix) => {
    const name = visualFilename(title, now, suffix)
    return folder ? `${folder}/${name}` : name
  }
  let createdFolder = false
  for (let suffix = 0; suffix < 20; suffix += 1) {
    const target = makePath(suffix)
    try {
      const body = await api('POST', '/files/file', { root: 'vault', path: target, text: standalone })
      return { root: body?.root ?? 'vault', path: body?.path ?? target }
    } catch (err) {
      if (err?.status === 404 && folder && !createdFolder) {
        createdFolder = true
        await ensureFolder(api, folder)
        suffix -= 1
        continue
      }
      if (err?.status === 409) continue
      throw err
    }
  }
  throw new Error('Could not find a free file name for this visual.')
}

// ------------------------------------------------------ fallback & sealed frame

export function visualFallbackNode(lang, html, note = 'Shown inline in threads') {
  const title = visualTitle(lang, html)
  const label = title ? `Visual · ${title}` : 'Visual'
  return h(
    'details',
    { class: 'md-visual-fallback' },
    h(
      'summary',
      { class: 'md-visual-fallback-head' },
      h('span', { class: 'md-visual-fallback-title', text: label }),
      h('span', { class: 'md-visual-fallback-note', text: note }),
    ),
    h('pre', { class: 'md-visual-fallback-src' }, h('code', { text: String(html ?? '') })),
  )
}

function mountSealedFrame({ html, title, hostEl, onFail, onMetrics = null, large = false }) {
  ensureGlobalListeners()
  const id = nextFrameId()
  const iframe = h('iframe', {
    class: `reply-visual-frame${large ? ' large' : ''} loading`,
    sandbox: 'allow-scripts',
    referrerpolicy: 'no-referrer',
    src: `/visual-frame.html#v=${encodeURIComponent(id)}`,
    title: title || 'Visual',
    scrolling: 'no',
  })
  let timer = null
  const ctrl = {
    id,
    iframe,
    hostEl,
    ready: false,
    mountedOnce: false,
    lastLook: null,
    cleanup() {
      if (timer) { clearTimeout(timer); timer = null }
    },
    onReady() {
      ctrl.cleanup()
      ctrl.ready = true
      ctrl.mountedOnce = true
      const snap = collectVisualTokens(hostEl)
      ctrl.lastLook = snap.look
      try {
        iframe.contentWindow?.postMessage({
          loamVisual: true,
          id,
          type: 'render',
          html,
          expanded: Boolean(large),
          tokens: snap.tokens,
          mode: snap.mode,
        }, '*')
      } catch { /* detached */ }
      loadThemeFontsCss(snap.look).then((fontsCss) => {
        if (liveFrames.get(id) === ctrl && ctrl.ready && ctrl.lastLook === snap.look) {
          sendThemeTo(ctrl, fontsCss)
        }
      })
    },
    onSize(info) {
      ctrl.mountedOnce = true
      dress(iframe, { height: `${info.height}px` })
      iframe.classList?.remove?.('loading')
      onMetrics?.(info)
    },
  }
  liveFrames.set(id, ctrl)
  iframe.addEventListener?.('error', () => {
    ctrl.cleanup()
    liveFrames.delete(id)
    onFail?.()
  })
  if (typeof globalThis.setTimeout === 'function' && globalThis.window?.addEventListener) {
    timer = setTimeout(() => {
      timer = null
      if (!ctrl.ready && iframe.isConnected) {
        liveFrames.delete(id)
        onFail?.()
      }
    }, FRAME_TIMEOUT_MS)
    timer.unref?.()
  }
  return {
    iframe,
    destroy() {
      ctrl.cleanup()
      liveFrames.delete(id)
    },
  }
}

// ------------------------------------------------------------ Expand dialog

let activeDialog = null

export function closeVisualView() {
  if (!activeDialog) return
  const { root, before, keys, destroyFrame } = activeDialog
  activeDialog = null
  destroyFrame?.()
  document.removeEventListener('keydown', keys, true)
  document.documentElement.classList.remove('visual-view-open')
  root.parentNode?.removeChild(root)
  if (before?.isConnected) before.focus?.({ preventScroll: true })
}

export function openVisualView({ html, lang = 'visual', title = '', api = null, openRef = null, naturalWidth = 0 } = {}) {
  closeVisualView()
  const heading = title || visualTitle(lang, html) || 'Visual'
  const stage = h('div', { class: 'visual-view-stage' })
  let bestWidth = Number.isFinite(Number(naturalWidth)) && Number(naturalWidth) > 0 ? Math.ceil(Number(naturalWidth)) : 0
  let savedPath = null
  let saving = false
  let saveError = ''
  const saveStatus = h('span', { class: 'visual-view-status', role: 'status' })
  const saveBtn = api
    ? h('button', {
        class: 'pillbtn visual-view-save visual-view-stop',
        type: 'button',
        title: 'Save to Files',
        onclick: async (e) => {
          e.stopPropagation()
          if (saving) return
          if (savedPath && openRef) {
            closeVisualView()
            openRef(`/files/vault/${encodeVaultPath(savedPath)}`)
            return
          }
          saving = true
          saveError = ''
          saveBtn.disabled = true
          saveStatus.textContent = 'Saving…'
          try {
            const res = await saveVisualToFiles({ html, title: heading, api, hostEl: stage })
            savedPath = res.path
            saveStatus.textContent = `Saved to ${res.path}`
            saveBtn.replaceChildren(icon.folder(14), h('span', { text: 'Open in Files' }))
            saveBtn.disabled = false
          } catch (err) {
            saveError = err?.message || 'Could not save to Files.'
            saveStatus.textContent = saveError
            saveBtn.disabled = false
          } finally {
            saving = false
          }
        },
      }, icon.folder(14), h('span', { text: 'Save to Files' }))
    : null
  const closeBtn = h('button', {
    class: 'iconbtn visual-view-close visual-view-stop',
    type: 'button',
    'aria-label': 'Close',
    title: 'Close',
    onclick: (e) => { e.stopPropagation(); closeVisualView() },
  }, icon.close(16))

  const surface = h(
    'div',
    { class: 'visual-view-sheet', onclick: (e) => e.stopPropagation() },
    h(
      'div',
      { class: 'visual-view-top' },
      h('div', { class: 'visual-view-title', text: heading }),
      saveStatus,
      saveBtn,
      closeBtn,
    ),
    stage,
  )
  const root = h(
    'dialog',
    {
      class: 'visual-view',
      role: 'dialog',
      'aria-modal': 'true',
      'aria-label': heading,
      oncancel: (e) => { e.preventDefault(); closeVisualView() },
      onclick: (e) => { if (e.target === root) closeVisualView() },
    },
    surface,
  )
  const applySheetWidth = (w) => {
    const sheetW = w > 0 ? Math.min(1440, Math.max(840, Math.ceil(w) + 160)) : 880
    surface.style?.setProperty?.('--visual-view-w', `${sheetW}px`)
  }
  applySheetWidth(bestWidth)
  const mounted = mountSealedFrame({
    html,
    title: heading,
    hostEl: stage,
    large: true,
    onMetrics: (info) => {
      if (info.naturalWidth > bestWidth) {
        bestWidth = info.naturalWidth
        applySheetWidth(bestWidth)
      }
    },
    onFail: () => {
      stage.replaceChildren(visualFallbackNode(lang, html, 'Visual couldn’t be drawn'))
    },
  })
  stage.append(mounted.iframe)

  const keys = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeVisualView() }
    else if (e.key === 'Tab') {
      const stops = [...root.querySelectorAll('.visual-view-stop')].filter((n) => !n.disabled)
      if (!stops.length) return
      const i = stops.indexOf(document.activeElement)
      e.preventDefault()
      stops[(i + (e.shiftKey ? -1 : 1) + stops.length) % stops.length]?.focus()
    }
  }
  activeDialog = { root, before: document.activeElement, keys, destroyFrame: mounted.destroy }
  document.addEventListener('keydown', keys, true)
  document.documentElement.classList.add('visual-view-open')
  document.body.append(root)
  root.showModal?.()
  closeBtn.focus({ preventScroll: true })
}

// --------------------------------------------------- replyVisuals controller

export function replyVisuals(message, key = null, { streaming = false, api = null, openRef = null } = {}) {
  function drawClosed(block, index) {
    const lang = block.lang ?? 'visual'
    const html = block.text ?? ''
    const title = visualTitle(lang, html)
    const cacheKey = key === null ? null : `${key}|${index}|${lang}|${hashText(html)}`
    const cached = cacheKey === null ? null : visualViews.get(cacheKey)
    if (cached) {
      cached.api = api
      cached.openRef = openRef
      return cached.stub
    }

    const record = { api, openRef, savedPath: null, saving: false, naturalWidth: 0 }
    const container = h('div', {
      class: 'reply-visual',
      key: `visual:${++visualSerial}`,
      'data-visual-title': title || null,
    })
    const slot = h('div', { class: 'reply-visual-slot' })
    const tools = h('div', { class: 'reply-visual-tools' })
    const saveNote = h('span', { class: 'reply-visual-status', role: 'status' })

    const expandBtn = h(
      'button',
      {
        class: 'reply-visual-action',
        type: 'button',
        hidden: true,
        title: 'Expand visual',
        'aria-label': title ? `Expand ${title}` : 'Expand visual',
        onclick: (e) => {
          e.stopPropagation()
          openVisualView({ html, lang, title, api: record.api, openRef: record.openRef, naturalWidth: record.naturalWidth })
        },
      },
      icon.expand(13),
      h('span', { text: 'Expand' }),
    )

    const saveBtn = h(
      'button',
      {
        class: 'reply-visual-action',
        type: 'button',
        title: 'Save to Files',
        'aria-label': title ? `Save ${title} to Files` : 'Save visual to Files',
        onclick: async (e) => {
          e.stopPropagation()
          if (record.saving) return
          if (record.savedPath && record.openRef) {
            record.openRef(`/files/vault/${encodeVaultPath(record.savedPath)}`)
            return
          }
          if (!record.api) return
          record.saving = true
          tools.classList?.add?.('active')
          saveBtn.disabled = true
          saveNote.textContent = 'Saving…'
          try {
            const res = await saveVisualToFiles({ html, title, api: record.api, hostEl: container })
            record.savedPath = res.path
            saveNote.textContent = 'Saved'
            saveBtn.replaceChildren(icon.folder(13), h('span', { text: 'Open in Files' }))
            saveBtn.disabled = false
          } catch (err) {
            saveNote.textContent = err?.message || 'Couldn’t save'
            saveBtn.disabled = false
          } finally {
            record.saving = false
          }
        },
      },
      icon.folder(13),
      h('span', { text: 'Save to Files' }),
    )

    tools.append(saveNote, expandBtn, saveBtn)
    const mounted = mountSealedFrame({
      html,
      title: title || 'Visual',
      hostEl: container,
      onMetrics: ({ canExpand, naturalWidth }) => {
        record.naturalWidth = naturalWidth
        expandBtn.hidden = !canExpand
      },
      onFail: () => {
        container.classList?.add?.('failed')
        slot.replaceChildren(visualFallbackNode(lang, html, 'Visual couldn’t be drawn'))
      },
    })
    slot.append(mounted.iframe)
    container.append(slot, tools)

    record.stub = kept(container)
    if (cacheKey !== null) {
      visualViews.delete(cacheKey)
      visualViews.set(cacheKey, record)
      if (visualViews.size > VISUALS_MAX) visualViews.delete(visualViews.keys().next().value)
    }
    return record.stub
  }

  function drawStreaming(block) {
    const title = visualTitle(block.lang, '')
    const label = title ? `Drawing ${title}…` : 'Drawing visual…'
    return h(
      'div',
      { class: 'reply-visual reply-visual-streaming', role: 'status', 'aria-live': 'polite' },
      h('span', { class: 'reply-visual-dot', 'aria-hidden': 'true' }),
      h('span', { class: 'reply-visual-streaming-text', text: label }),
    )
  }

  return {
    streaming,
    signature: `visuals:${Boolean(streaming)}`,
    visuals: {
      draw(block, index) {
        if (!block.closed) {
          return streaming ? drawStreaming(block) : visualFallbackNode(block.lang, block.text, 'Incomplete visual')
        }
        return drawClosed(block, index)
      },
    },
    live(text) {
      return streaming ? holdPartialVisualFence(text) : text
    },
    fallbackLabel: visualFallbackLine,
  }
}
