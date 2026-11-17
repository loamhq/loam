// Gradual colour-shift engine for live Sun mode boundaries.
// Surfaces drift across the whole duration; inks hold contrast and flip near the midpoint.

export const SUN_TRANSITIONS = Object.freeze({
  fast: 3000,
  normal: 10000,
  long: 20000,
})

export function transitionMs(id) {
  return SUN_TRANSITIONS[id] ?? SUN_TRANSITIONS.normal
}

const clamp01 = (v) => Math.min(1, Math.max(0, v))
const lin = (v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
const gam = (v) => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055)

function parseUnit(token, hundred = 1) {
  if (token.endsWith('%')) {
    const n = Number(token.slice(0, -1))
    return Number.isFinite(n) ? (n / 100) * hundred : NaN
  }
  const n = Number(token)
  return Number.isFinite(n) ? n : NaN
}

function parseHue(token) {
  if (token.endsWith('deg')) return (Number(token.slice(0, -3)) * Math.PI) / 180
  if (token.endsWith('rad')) return Number(token.slice(0, -3))
  if (token.endsWith('turn')) return Number(token.slice(0, -4)) * 2 * Math.PI
  if (token.endsWith('grad')) return (Number(token.slice(0, -4)) * Math.PI) / 200
  return (Number(token) * Math.PI) / 180
}

function hslToRgb(hDeg, s, l, a) {
  const h = (((hDeg % 360) + 360) % 360) / 360
  if (s === 0) return [l, l, l, a]
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  const chan = (t) => {
    const u = ((t % 1) + 1) % 1
    if (u < 1 / 6) return p + (q - p) * 6 * u
    if (u < 1 / 2) return q
    if (u < 2 / 3) return p + (q - p) * (2 / 3 - u) * 6
    return p
  }
  return [chan(h + 1 / 3), chan(h), chan(h - 1 / 3), a]
}

function oklchToSrgb01([L, C, H, a]) {
  const A = C * Math.cos(H)
  const B = C * Math.sin(H)
  const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
  const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
  const s = (L - 0.0894841775 * A - 1.2914855480 * B) ** 3
  return [
    clamp01(gam(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s)),
    clamp01(gam(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s)),
    clamp01(gam(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)),
    clamp01(a),
  ]
}

/** Parse a resolved CSS colour string into [r, g, b, a] in [0, 1], or null. */
export function parseColor(value) {
  if (typeof value !== 'string') return null
  const s = value.trim()
  if (!s) return null
  if (s[0] === '#') {
    const raw = s.slice(1)
    if (!/^(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(raw)) return null
    const hex = raw.length <= 4 ? [...raw].map((c) => c + c).join('') : raw
    const r = parseInt(hex.slice(0, 2), 16) / 255
    const g = parseInt(hex.slice(2, 4), 16) / 255
    const b = parseInt(hex.slice(4, 6), 16) / 255
    const a = hex.length === 8 ? parseInt(hex.slice(6, 8), 16) / 255 : 1
    return [r, g, b, a]
  }
  const open = s.indexOf('(')
  if (open <= 0 || !s.endsWith(')')) return null
  const fn = s.slice(0, open).trim().toLowerCase()
  const body = s.slice(open + 1, -1).trim()
  if (!body || body.includes('(')) return null
  const parts = body.replace(/\s*\/\s*/, ' ').split(/[\s,]+/).filter(Boolean)
  if (fn === 'rgb' || fn === 'rgba') {
    if (parts.length !== 3 && parts.length !== 4) return null
    const rgb = parts.slice(0, 3).map((p) => parseUnit(p, 255) / 255)
    const a = parts[3] !== undefined ? parseUnit(parts[3], 1) : 1
    if (rgb.some((n) => !Number.isFinite(n)) || !Number.isFinite(a)) return null
    return [clamp01(rgb[0]), clamp01(rgb[1]), clamp01(rgb[2]), clamp01(a)]
  }
  if (fn === 'hsl' || fn === 'hsla') {
    if (parts.length !== 3 && parts.length !== 4) return null
    const h = (parseHue(parts[0]) * 180) / Math.PI
    const sat = parseUnit(parts[1], 100) / 100
    const lit = parseUnit(parts[2], 100) / 100
    const a = parts[3] !== undefined ? parseUnit(parts[3], 1) : 1
    if (![h, sat, lit, a].every(Number.isFinite)) return null
    return hslToRgb(h, clamp01(sat), clamp01(lit), clamp01(a))
  }
  if (fn === 'oklab' || fn === 'oklch') {
    if (parts.length !== 3 && parts.length !== 4) return null
    const L = parseUnit(parts[0], 1)
    const a = parts[3] !== undefined ? parseUnit(parts[3], 1) : 1
    if (fn === 'oklab') {
      const A = parseUnit(parts[1], 0.4), B = parseUnit(parts[2], 0.4)
      if (![L, A, B, a].every(Number.isFinite)) return null
      return oklchToSrgb01([L, Math.hypot(A, B), Math.atan2(B, A), a])
    }
    const C = parseUnit(parts[1], 0.4), H = parseHue(parts[2])
    if (![L, C, H, a].every(Number.isFinite)) return null
    return oklchToSrgb01([L, C, H, a])
  }
  if (fn === 'color') {
    if ((parts.length !== 4 && parts.length !== 5) || parts[0].toLowerCase() !== 'srgb') return null
    const rgb = parts.slice(1, 4).map((p) => parseUnit(p, 1))
    const a = parts[4] !== undefined ? parseUnit(parts[4], 1) : 1
    if (rgb.some((n) => !Number.isFinite(n)) || !Number.isFinite(a)) return null
    return [clamp01(rgb[0]), clamp01(rgb[1]), clamp01(rgb[2]), clamp01(a)]
  }
  return null
}

/** Convert sRGB [0..1] + alpha to OKLCH [L, C, H_rad, a]. */
export function toOklch([r, g, b, a = 1]) {
  r = lin(r); g = lin(g); b = lin(b)
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  const L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
  const A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
  return [L, Math.hypot(A, B), Math.atan2(B, A), a]
}

/** Convert OKLCH [L, C, H_rad, a] to sRGB bytes [0..255] + alpha. */
export function fromOklch([L, C, H, a = 1]) {
  const [r, g, b, alpha] = oklchToSrgb01([L, C, H, a])
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255), alpha]
}

/** Interpolate two OKLCH colours, letting greys borrow the other end's hue. */
export function mixOklch(x, y, t) {
  let [L1, C1, H1, a1] = x
  let [L2, C2, H2, a2] = y
  if (C1 < 0.02) H1 = H2
  if (C2 < 0.02) H2 = H1
  let dH = H2 - H1
  if (dH > Math.PI) dH -= 2 * Math.PI
  if (dH < -Math.PI) dH += 2 * Math.PI
  return fromOklch([L1 + (L2 - L1) * t, C1 + (C2 - C1) * t, H1 + dH * t, a1 + (a2 - a1) * t])
}

export function formatColor([r, g, b, a = 1]) {
  if (a >= 0.9995) {
    const fn = 'rgb'
    return `${fn}(${r},${g},${b})`
  }
  const fn = 'rgba'
  return `${fn}(${r},${g},${b},${a.toFixed(3)})`
}

export const smoothstep = (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t))
export const surfaceProgress = (t) => 0.7 * t + 0.3 * smoothstep(t)
export function inkProgress(sP) {
  if (sP <= 0.485) return 0
  if (sP >= 0.515) return 1
  return smoothstep((sP - 0.485) / 0.03)
}

/**
 * Classify resolved light/dark custom properties into drifting surfaces and flipping inks.
 * Tokens that get lighter going into dark mode flip near the midpoint; tokens that get
 * darker drift across the full duration, except `-ink` tokens paired with a flipping surface.
 */
export function classifyTokens(lightMap, darkMap) {
  const get = (map, key) => (map instanceof Map ? map.get(key) : map?.[key])
  const keys = lightMap instanceof Map ? lightMap.keys() : Object.keys(lightMap || {})
  const entries = []
  const byName = new Map()
  for (const name of keys) {
    const lRaw = get(lightMap, name), dRaw = get(darkMap, name)
    if (!lRaw || !dRaw || lRaw === dRaw) continue
    const lCol = parseColor(lRaw), dCol = parseColor(dRaw)
    if (!lCol || !dCol) continue
    const l = toOklch(lCol), d = toOklch(dCol)
    const entry = { name, l, d, ink: d[0] > l[0] + 0.001 }
    entries.push(entry)
    byName.set(name, entry)
  }
  // Keep inverted-button ink paired with its flipping fill so labels remain readable.
  for (const entry of entries) {
    if (entry.ink || !entry.name.endsWith('-ink')) continue
    if (byName.get(entry.name.slice(0, -4))?.ink) entry.ink = true
  }
  return { entries, lightGround: get(lightMap, '--paper-page') || '', darkGround: get(darkMap, '--paper-page') || '' }
}

export function readModeTokens(root, getComputedStyle) {
  const cs = getComputedStyle(root)
  const map = new Map()
  for (let i = 0; i < cs.length; i++) {
    const name = cs[i]
    if (!name.startsWith('--')) continue
    const value = cs.getPropertyValue(name).trim()
    if (value) map.set(name, value)
  }
  return map
}

export function createShift({
  root,
  setThemeColor = (ground) => { if (ground) globalThis.document?.querySelector?.('meta[name="theme-color"]')?.setAttribute('content', ground) },
  getComputedStyle = globalThis.getComputedStyle,
  raf = globalThis.requestAnimationFrame,
  caf = globalThis.cancelAnimationFrame,
  perfNow = () => globalThis.performance?.now?.() ?? Date.now(),
} = {}) {
  let state = null

  function clearOverrides(props) {
    for (const name of props) root.style.removeProperty(name)
  }

  function cancel({ snapTo } = {}) {
    if (!state) return
    const current = state
    state = null
    caf?.(current.rafId)
    clearOverrides(current.props)
    if (snapTo) {
      root.dataset.theme = snapTo
      setThemeColor(snapTo === 'dark' ? current.darkGround : current.lightGround)
    }
  }

  function start({ fromMode, toMode, duration, look, optionsKey, onMidpoint, onComplete } = {}) {
    cancel()
    if (typeof getComputedStyle !== 'function' || typeof raf !== 'function' || !duration || duration <= 0) return false
    root.dataset.theme = 'light'
    const lightMap = readModeTokens(root, getComputedStyle)
    root.dataset.theme = 'dark'
    const darkMap = readModeTokens(root, getComputedStyle)
    root.dataset.theme = fromMode

    const { entries, lightGround, darkGround } = classifyTokens(lightMap, darkMap)
    if (!entries.length) return false

    const props = entries.map((e) => e.name)
    const startTime = perfNow()
    const current = {
      fromMode,
      toMode,
      look,
      optionsKey,
      props,
      lightGround,
      darkGround,
      crossedMidpoint: false,
      rafId: 0,
    }
    state = current

    const renderFrame = (p) => {
      if (p >= 0.5 && !current.crossedMidpoint) {
        current.crossedMidpoint = true
        root.dataset.theme = toMode
        setThemeColor(toMode === 'dark' ? darkGround : lightGround)
        onMidpoint?.()
      }
      if (p >= 1) {
        state = null
        clearOverrides(props)
        root.dataset.theme = toMode
        setThemeColor(toMode === 'dark' ? darkGround : lightGround)
        onComplete?.()
        return
      }
      const sP = surfaceProgress(p)
      const iP = inkProgress(sP)
      for (const item of entries) {
        const from = toMode === 'dark' ? item.l : item.d
        const to = toMode === 'dark' ? item.d : item.l
        root.style.setProperty(item.name, formatColor(mixOklch(from, to, item.ink ? iP : sP)))
      }
      current.rafId = raf(step)
    }

    const step = () => {
      if (state !== current) return
      const p = Math.min(1, Math.max(0, (perfNow() - startTime) / duration))
      renderFrame(p)
    }

    renderFrame(0)
    return true
  }

  return {
    start,
    cancel,
    get active() { return state ? { fromMode: state.fromMode, toMode: state.toMode, look: state.look, optionsKey: state.optionsKey } : null },
  }
}
