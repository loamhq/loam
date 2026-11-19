// Everything `app.js` reaches for that a browser would have given it.
//
// It was inside `test/app.test.js` until the desktop layout needed a second
// test file: the page is a module with one instance, so a layout that is read
// off the window's width can only be driven by a process that sets that width
// before the page is imported. Two processes, one browser, and the width is the
// one thing they differ in.
//
// The timers are remembered so the page's own heartbeat and its reconnection
// waits can be stopped when the tests are over; nothing else about them is
// faked.

import { installDom } from './fakedom.js'
import { audioRows } from './audio-plugins.mjs'

export function browser(appUrl, { innerWidth = 430, audioEnabled = true } = {}) {
  const document = installDom()
  const root = document.createElement('div')
  root.__document = true
  const html = document.createElement('html')
  html.dataset = {}
  document.documentElement = html
  document.getElementById = (id) => (id === 'app' ? root : null)
  document.querySelector = () => null
  // The listeners the page puts on the document itself — the drag swallower,
  // the visibility change, and the Escape that closes what is over the screen.
  // They were dropped on the floor until 2026-09-22, which meant the one key
  // this app binds globally could not be pressed by any test.
  const listeners = new Map()
  document.addEventListener = (type, fn) => {
    if (!listeners.has(type)) listeners.set(type, [])
    listeners.get(type).push(fn)
  }
  document.visibilityState = 'visible'

  const handles = new Set()
  const realTimeout = globalThis.setTimeout
  const realInterval = globalThis.setInterval
  const watch = () => {
    globalThis.setTimeout = (fn, ms, ...rest) => {
      const handle = realTimeout(fn, ms, ...rest)
      handles.add(handle)
      return handle
    }
    globalThis.setInterval = (fn, ms, ...rest) => {
      const handle = realInterval(fn, ms, ...rest)
      handles.add(handle)
      return handle
    }
  }
  const unwatch = () => {
    globalThis.setTimeout = realTimeout
    globalThis.setInterval = realInterval
  }
  globalThis.requestAnimationFrame = (fn) => realTimeout(fn, 0)
  globalThis.window = { addEventListener: () => {} }
  // The width the page reads to decide which layout it is drawing. A browser
  // with no `matchMedia` — which this is — falls back to it, so a test says
  // "this is a desktop window" by saying how wide it is and nothing else.
  globalThis.innerWidth = innerWidth
  // A browser that has speech input, so the composer draws its mic — and, once
  // the mic is pressed, a recogniser a test can say things into. It records the
  // listeners the page attaches and hands them back through `emit`, which is
  // the whole of it: no audio, no recognition, just the events a real one
  // sends, in the order it sends them.
  globalThis.SpeechRecognition = class {
    constructor() {
      this.listeners = new Map()
      this.started = false
      globalThis.SpeechRecognition.last = this
    }

    addEventListener(type, fn) {
      if (!this.listeners.has(type)) this.listeners.set(type, [])
      this.listeners.get(type).push(fn)
    }

    start() {
      this.started = true
    }

    stop() {
      this.started = false
    }

    emit(type, event) {
      for (const fn of this.listeners.get(type) ?? []) fn(event)
    }
  }
  if (!globalThis.navigator?.language) {
    Object.defineProperty(globalThis, 'navigator', { value: { language: 'en-GB' }, configurable: true })
  }
  globalThis.localStorage = storage()
  const realFetch = globalThis.fetch
  // Every byte of every event stream the page has read, in the order it read
  // them. A test that writes an event into a stream waits on this to know the
  // page has it, rather than on a length of time: a sleep long enough on an
  // idle box was not long enough with the suite's other files running beside
  // it (2026-10-02).
  let heard = ''
  const tee = (res) => {
    if (!res.body || !(res.headers.get('content-type') ?? '').startsWith('text/event-stream')) return res
    const decoder = new TextDecoder()
    const body = res.body.pipeThrough(
      new TransformStream({
        transform(chunk, controller) {
          heard += decoder.decode(chunk, { stream: true })
          controller.enqueue(chunk)
        },
      }),
    )
    return new Response(body, { status: res.status, statusText: res.statusText, headers: res.headers })
  }
  // Audio workflow fixtures explicitly opt in. Their synthetic old host has no
  // catalog; the production fresh catalog/defaults are checked separately in
  // installation.test.js. This keeps those tests exercising their feature:
  // Dictation and Read aloud, from the Market since 0.2.9, are added as an
  // install that has them on (test/audio-plugins.mjs).
  globalThis.fetch = (url, options) => realFetch(String(url).startsWith('/') ? `${appUrl}${url}` : url, options).then(async res => {
    if (audioEnabled && String(url) === '/shipped-plugins.json') {
      const body = await res.json()
      return Response.json({ ...body, plugins: [...body.plugins.map(row => row.switch === 'plugin' ? { ...row, on: true } : row), ...await audioRows()] })
    }
    return tee(res)
  })

  return {
    root,
    watch,
    unwatch,
    // How much the page has read off its event streams, and whether it has read
    // a given frame since a given point.
    heard: () => heard.length,
    hasHeard: (frame, since = 0) => heard.indexOf(frame, since) !== -1,
    // A key pressed with nothing in particular focused, which is what a person
    // pressing Escape over a dialog is: the event reaches the document, and
    // whatever the page put there answers it. `defaultPrevented` comes back so
    // a test can say the page took the key rather than letting it through.
    press(key, over = {}) {
      const event = { type: 'keydown', key, defaultPrevented: false, preventDefault() { event.defaultPrevented = true }, ...over }
      for (const fn of listeners.get('keydown') ?? []) fn(event)
      return event
    },
    // The page hidden or shown — a phone's screen going off and coming back —
    // as the document's own `visibilitychange`.
    show(visible = true) {
      document.visibilityState = visible ? 'visible' : 'hidden'
      for (const fn of listeners.get('visibilitychange') ?? []) fn({ type: 'visibilitychange' })
    },
    stop() {
      for (const handle of handles) {
        clearTimeout(handle)
        clearInterval(handle)
      }
      unwatch()
      globalThis.fetch = realFetch
    },
  }
}

export function storage() {
  const held = new Map()
  return {
    getItem: (k) => (held.has(k) ? held.get(k) : null),
    setItem: (k, v) => held.set(k, String(v)),
    removeItem: (k) => held.delete(k),
    key: (i) => [...held.keys()][i] ?? null,
    get length() {
      return held.size
    },
  }
}

// Finding things on the page, by the class the design gives them.
export const find = (node, cls) => {
  if (node.nodeType === 1 && (node.getAttribute('class') ?? '').split(' ').includes(cls)) return node
  for (const child of node.childNodes ?? []) {
    const hit = find(child, cls)
    if (hit) return hit
  }
  return null
}

export const all = (node, cls, out = []) => {
  if (node.nodeType === 1 && (node.getAttribute('class') ?? '').split(' ').includes(cls)) out.push(node)
  for (const child of node.childNodes ?? []) all(child, cls, out)
  return out
}

export const text = (node) => (node ? node.textContent : null)
