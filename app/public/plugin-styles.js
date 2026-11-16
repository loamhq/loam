// Trusted plugin CSS is ordinary CSS. Its layer is a cascade ordering promise,
// not a selector allowlist. One ordered set of links follows Loam's sheets.
export function createPluginStyles(doc = globalThis.document) {
  const entries = new Set()
  let serial = 0, revision = 0
  function order() {
    const wanted = [...entries].sort((a, b) => (a.row.layer === 'yours') - (b.row.layer === 'yours') || a.serial - b.serial)
      .flatMap(entry => entry.pending ? [entry.link, entry.pending] : [entry.link])
    const managed = new Set(wanted)
    const placed = [...doc.head.children].filter(node => managed.has(node))
    for (const [index, node] of wanted.entries()) {
      // Reappending a loaded stylesheet detaches its CSS until a fresh fetch
      // finishes. Catalog refreshes follow sends and other ordinary writes;
      // leave correctly ordered sheets attached, including during hot reload.
      if (node === placed[index]) continue
      doc.head.insertBefore(node, placed[index] ?? null)
      const previous = placed.indexOf(node)
      if (previous >= 0) placed.splice(previous, 1)
      placed.splice(index, 0, node)
    }
  }
  function add(row, file, local = false) {
    const link = doc.createElement('link')
    link.rel = 'stylesheet'
    link.href = local && row.local ? new URL('../' + file, new URL(row.local, globalThis.location?.href ?? 'http://localhost')).pathname : `/api/plugins/${encodeURIComponent(row.id)}/files/${file.split('/').map(encodeURIComponent).join('/')}`
    link.dataset.plugin = row.id
    const entry = { row, link, fresh: true, pending: null, serial: ++serial }; entries.add(entry); order()
    return { remove() { if (!entries.delete(entry)) return; entry.link.remove(); entry.pending?.remove() } }
  }
  function refresh() {
    for (const entry of entries) {
      if (entry.fresh) { entry.fresh = false; continue }
      if (entry.row.layer !== 'yours') continue
      entry.pending?.remove()
      const next = doc.createElement('link')
      next.rel = 'stylesheet'; next.dataset.plugin = entry.row.id
      next.href = entry.link.href.split('?')[0] + `?load=${++revision}`
      entry.pending = next
      next.onload = () => {
        if (!entries.has(entry) || entry.pending !== next) return
        entry.link.remove(); entry.link = next; entry.pending = null; order()
      }
      next.onerror = () => { if (entry.pending === next) entry.pending = null; next.remove() }
    }
    order()
  }
  return { add, order, refresh }
}
