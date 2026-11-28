// Compatibility cache for unacknowledged extended-range sizes (< 75% or > 150%)
// until the host accepts 60–200%. Host-supported sizes are never cached here.
;(function () {
  if (globalThis.LoamSize) return
  const key = 'loam.appearance.size'
  const isExtended = (v) => Number.isInteger(v) && v % 5 === 0 && ((v >= 60 && v < 75) || (v > 150 && v <= 200))
  function local(kind, size) {
    try {
      const stored = JSON.parse(localStorage.getItem(key) || '{}')
      if (arguments.length > 1) {
        if (isExtended(size)) stored[kind] = size
        else delete stored[kind]
        if (Object.keys(stored).length) localStorage.setItem(key, JSON.stringify(stored))
        else localStorage.removeItem(key)
      }
      return isExtended(stored[kind]) ? stored[kind] : undefined
    } catch { return undefined }
  }
  globalThis.LoamSize = { local, isExtended }
})()
