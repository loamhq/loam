import { readAcceptedMessage } from './thread-record.js'

const SEND_TIMEOUT = 15_000
const RETRY_DELAY = 2_000

export async function sendRequest(api, method, path, body, until = Date.now() + SEND_TIMEOUT) {
  const remaining = until - Date.now()
  if (remaining <= 0) throw new Error('The send took too long.')
  const controller = new AbortController()
  let timer
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      controller.abort()
      reject(new Error('The send took too long.'))
    }, remaining)
  })
  try {
    // Bound the response body and any shared sign-in check as well as fetch.
    return await Promise.race([api(method, path, body, { signal: controller.signal }), timeout])
  } finally {
    clearTimeout(timer)
  }
}

let shared
export function threadOutbox(api) {
  if (shared) return shared
  const listeners = new Set()
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('loam-thread-sends') : null
  let database, running = false, again = false, timer, rows = []
  function db() {
    if (!database) database = new Promise((resolve, reject) => {
      const req = indexedDB.open('loam-thread-sends', 1)
      req.onupgradeneeded = () => req.result.createObjectStore('messages', { keyPath: 'order', autoIncrement: true })
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => { database = null; reject(req.error) }
    })
    return database
  }
  async function store(mode, operation) {
    const connection = await db()
    return new Promise((resolve, reject) => {
      const tx = connection.transaction('messages', mode)
      const req = operation(tx.objectStore('messages'))
      tx.oncomplete = () => resolve(req.result)
      tx.onabort = () => reject(tx.error || req.error)
    })
  }
  async function refresh(message, thread) {
    rows = await store('readonly', s => s.getAll())
    for (const listener of listeners) listener(message, thread)
  }
  async function changed(message, thread) {
    await refresh(message, thread)
    channel?.postMessage({ message, thread })
  }
  // Sequence numbers and the active attempt belong to IndexedDB across tabs.
  // The transaction ends before the network call. If a tab freezes, another
  // can pick up the queue at the lease deadline using its own clock.
  async function write(read, update) {
    let result
    await store('readwrite', s => {
      const req = read(s)
      req.onsuccess = () => { result = update(s, req.result) }
      return req
    })
    return result
  }
  function claim() {
    return write(s => s.getAll(), (s, pending) => {
      const now = Date.now()
      const active = pending.find(item => item.leaseUntil > now)
      if (active) return { wakeAt: active.leaseUntil }
      let next, wakeAt = Infinity
      const opening = new Set()
      for (const item of pending) {
        if (item.error || opening.has(item.thread)) continue
        // A branch quote is accepted only on that thread's opening message.
        if (item.body.reply_thread) opening.add(item.thread)
        if (item.retryAt > now) {
          wakeAt = Math.min(wakeAt, item.retryAt)
        } else if (!item.attempts) {
          next = item
          break
        } else {
          next ??= item
        }
      }
      if (!next) return { wakeAt }
      const item = { ...next, attempts: (next.attempts ?? 0) + 1, leaseUntil: now + SEND_TIMEOUT }
      s.put(item)
      return { item }
    })
  }
  function schedule(at) {
    clearTimeout(timer)
    if (Number.isFinite(at)) timer = setTimeout(wake, Math.max(0, at - Date.now()))
  }
  function wake() {
    clearTimeout(timer)
    if (running) { again = true; return }
    running = true
    pump().catch(storageRetry).finally(() => {
      running = false
      if (again) { again = false; wake() }
    })
  }
  function storageRetry() {
    schedule(Date.now() + RETRY_DELAY)
  }
  async function pump() {
    const { item, wakeAt } = await claim()
    if (!item) {
      await refresh()
      schedule(wakeAt)
      return
    }
    await changed()
    let message
    try {
      const answer = await sendRequest(api, 'PUT', `/threads/${encodeURIComponent(item.thread)}/messages`, item.body, item.leaseUntil)
      const accepted = readAcceptedMessage(answer)
      if (accepted.submission_id !== item.body.submission_id) throw new Error('Missing submission acknowledgement')
      message = accepted
    } catch (error) {
      await write(s => s.get(item.order), (s, current) => {
        // A late failure must never overwrite a newer attempt or resurrect an
        // acknowledged row.
        if (current?.attempts !== item.attempts) return
        const transient = !error.status || error.status >= 500 || [401, 408, 429].includes(error.status)
        const retryAt = Date.now() + RETRY_DELAY * 2 ** Math.min(4, item.attempts - 1)
        s.put({ ...current, leaseUntil: 0, ...(transient ? { retryAt } : { error: error.message }) })
      })
    }
    if (message) await store('readwrite', s => s.delete(item.order))
    await changed(message, item.thread)
    again = true
  }
  channel?.addEventListener('message', e => { refresh(e.data?.message, e.data?.thread).then(wake).catch(storageRetry) })
  globalThis.addEventListener?.('online', wake)
  globalThis.addEventListener?.('pageshow', wake)
  shared = {
    listen(fn) { listeners.add(fn) },
    rows(thread) { return rows.filter(row => row.thread === thread) },
    async add(thread, body, attachments, reply) {
      const entry = { thread, body, attachments, reply }
      const order = await store('readwrite', s => s.add(entry))
      // The local commit transfers ownership from the composer. A failed
      // refresh after this point must never put the text back for a new send.
      rows = [...rows.filter(row => row.order !== order), { ...entry, order }]
      channel?.postMessage({ thread })
      wake()
    },
    async remove(order) {
      await store('readwrite', s => s.delete(order))
      await changed()
    },
    async retry(order) {
      await write(s => s.get(order), (s, item) => {
        if (item && !item.error && !(item.leaseUntil > Date.now())) s.put({ ...item, retryAt: 0 })
      })
      await changed()
      wake()
    },
  }
  wake()
  return shared
}
