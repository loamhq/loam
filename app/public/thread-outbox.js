// The sequence is allocated by IndexedDB's write transaction, including
// across tabs. Only an acknowledgement naming the submission removes it.
import { readAcceptedMessage } from './thread-record.js'

let shared
export function threadOutbox(api) {
  if (shared) return shared
  const listeners = new Set()
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('loam-thread-sends') : null
  let database, running = false, again = false, timer, delay = 500, rows = []
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
  function wake() {
    clearTimeout(timer)
    if (running) { again = true; return }
    running = true
    const drain = globalThis.navigator?.locks
      ? navigator.locks.request('loam-thread-sends', pump)
      : pump()
    drain.catch(retry).finally(() => {
      running = false
      if (again) { again = false; wake() }
    })
  }
  function retry() {
    clearTimeout(timer)
    timer = setTimeout(wake, delay)
    delay = Math.min(10000, delay * 2)
  }
  async function pump() {
    await refresh()
    for (;;) {
      const item = rows.find(row => !row.error)
      if (!item) return
      let message
      try {
        const answer = await api('PUT', `/threads/${encodeURIComponent(item.thread)}/messages`, item.body)
        message = readAcceptedMessage(answer)
        if (message.submission_id !== item.body.submission_id) throw new Error('Missing submission acknowledgement')
      } catch (error) {
        if (!error.status || error.status >= 500 || [401, 408, 429].includes(error.status)) {
          retry()
          return
        }
        await store('readwrite', s => s.put({ ...item, error: error.message }))
        await changed()
        continue
      }
      await store('readwrite', s => s.delete(item.order))
      delay = 500
      await changed(message, item.thread)
    }
  }
  channel?.addEventListener('message', e => { refresh(e.data?.message, e.data?.thread).then(wake).catch(retry) })
  globalThis.addEventListener?.('online', wake)
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
  }
  wake()
  return shared
}
