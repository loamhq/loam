// Host-owned appearance. Writes contain only changed fields for one device kind.
import { authenticatedFetch as fetch } from './auth.js'
import { optionValues, mergeAppearance, applyOptions } from './theme-options.js'
import './sun.js'
import { createShift, transitionMs } from './sun-shift.js'
import { formatTime, browserTimeZone } from './time.js'

export function createTheme(media = globalThis.matchMedia?.('(prefers-color-scheme: dark)'), {
  boot = globalThis.__loamAppearance,
  kind = document.documentElement.dataset.deviceKind || (globalThis.matchMedia?.('(pointer: coarse)').matches ? 'phone' : 'desktop'),
  request = async (method, body) => {
    const res = await fetch('/api/settings/appearance', { method, headers: { 'content-type': 'application/json' }, signal: AbortSignal.timeout(5000), keepalive: true, ...(body ? { body: JSON.stringify(body) } : {}) })
    const out = await res.json()
    if (!res.ok) throw new Error(out.error || 'Appearance could not be saved.')
    return out
  },
  changed = () => {},
  onSaved = () => {},
  now = () => Date.now(),
  locate = () => new Promise((resolve, reject) => {
    if (!globalThis.navigator?.geolocation) return reject(new Error('Location unavailable'))
    navigator.geolocation.getCurrentPosition(resolve, reject, { enableHighAccuracy: false, timeout: 10000, maximumAge: 86400000 })
  }),
  reducedMotion,
  shift: customShift,
} = {}) {
  const root = document.documentElement
  const installed = boot?.themes || [{ id: 'latte', name: 'Latte & Newspaper' }, { id: 'plain', name: 'Plain' }]
  const themeId = id => installed.some(t => t.id === id) ? id : 'latte'
  const transitionId = id => ['fast', 'normal', 'long'].includes(id) ? id : 'normal'
  const sun = globalThis.LoamSun
  let draft = sun.local(kind), localOnly = false, sunUnsupported = false, locating = false, sunTimer, activeMode = null
  let value = { mode: 'system', transition: 'normal', size: 100, ...boot?.values?.[kind], ...(sun.location(boot?.values?.location) ? { location: sun.location(boot.values.location) } : {}), ...draft, theme: themeId(boot?.values?.[kind]?.theme), transition: transitionId(draft.transition ?? boot?.values?.[kind]?.transition) }
  let withOptions = installed.some(t => t.options?.length)
  if (withOptions) value.options = optionValues(value.options, installed)
  let pending = { ...draft }, timer, saving = false, revision = 0, reading = 0
  // The Market's Try it (2026-11-18): a look worn until it is stopped; `value`
  // and what is saved are untouched, so stopping is the theme as it was.
  let trying = null
  let error = boot?.available !== true ? 'Appearance settings are not available on the host yet. Changes apply here until you reload.' : null
  const motionQuery = reducedMotion ?? globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')
  const isHidden = () => Boolean(globalThis.document?.hidden || globalThis.document?.visibilityState === 'hidden')
  const isReducedMotion = () => Boolean(motionQuery?.matches)
  const updateThemeColor = (ground = globalThis.getComputedStyle?.(root).getPropertyValue('--paper-page').trim()) => {
    if (ground) globalThis.document?.querySelector?.('meta[name="theme-color"]')?.setAttribute('content', ground)
  }
  const shift = customShift ?? createShift({ root, setThemeColor: updateThemeColor })
  const status = () => ({ saving: saving || Object.keys(pending).length > 0, error: localOnly && !error?.includes('until saved') ? null : error, localOnly, locating })
  function cache(patch) { draft = { ...draft, ...patch }; sun.local(kind, draft) }
  function acknowledge(patch) {
    for (const key of ['mode', 'location', 'transition']) if (JSON.stringify(draft[key]) === JSON.stringify(patch[key])) delete draft[key]
    sun.local(kind, draft); localOnly = Object.keys(draft).length > 0
  }
  function solarPatch(patch) {
    return { ...(patch.mode === 'sun' ? { mode: 'sun' } : {}), ...(patch.location ? { location: patch.location } : {}), ...(patch.transition ? { transition: patch.transition } : {}) }
  }
  function optionsSignature() {
    return withOptions ? JSON.stringify(value.options?.[value.theme] || {}) : ''
  }
  function apply({ allowShift = false, keepActiveShift = false } = {}) {
    // A theme being tried from the Market is worn, never saved (2026-11-18).
    const look = trying ?? value.theme
    clearTimeout(sunTimer)
    const solar = value.mode === 'sun' ? sun.state(now(), value.location) : null
    const chosen = solar ? solar.mode : value.mode === 'system' ? (media?.matches ? 'dark' : 'light') : value.mode
    if (solar) {
      const targetAt = solar.next
      sunTimer = setTimeout(() => {
        const late = now() - targetAt > 60000
        apply({ allowShift: !late })
        changed()
      }, Math.max(1, Math.ceil(targetAt - now())))
      sunTimer.unref?.()
    }
    const optKey = optionsSignature()
    const currentShift = shift.active
    if (keepActiveShift && currentShift && value.mode === 'sun' && currentShift.toMode === chosen && currentShift.look === look && currentShift.optionsKey === optKey && !isHidden() && !isReducedMotion()) {
      root.style.setProperty('--ui-scale', value.size / 100)
      activeMode = chosen
      return chosen
    }
    if (allowShift && value.mode === 'sun' && activeMode && activeMode !== chosen && !isHidden() && !isReducedMotion()) {
      const fromMode = activeMode
      root.dataset.look = look
      globalThis.LoamThemeFonts?.(look)
      applyOptions(root, value.options, installed, look)
      root.style.setProperty('--ui-scale', value.size / 100)
      if (shift.start({
        fromMode,
        toMode: chosen,
        duration: transitionMs(value.transition),
        look,
        optionsKey: optKey,
        onMidpoint: () => changed(),
        onComplete: () => changed(),
      })) {
        activeMode = chosen
        return chosen
      }
    }
    shift.cancel()
    activeMode = chosen
    root.dataset.theme = chosen
    root.dataset.look = look
    globalThis.LoamThemeFonts?.(look)
    applyOptions(root, value.options, installed, look)
    root.style.setProperty('--ui-scale', value.size / 100)
    updateThemeColor()
    return chosen
  }
  async function save() {
    clearTimeout(timer)
    if (saving || !Object.keys(pending).length) return
    const patch = pending
    pending = {}; saving = true; error = null; changed()
    // A rejecting host is tried once per page, never on a focus/error loop.
    if (sunUnsupported) {
      const local = solarPatch(patch)
      if (Object.keys(local).length) { cache(local); localOnly = true; for (const key of Object.keys(local)) delete patch[key] }
    }
    try {
      if (!Object.keys(patch).length) return
      const { location, ...device } = patch
      const out = await request('PATCH', { ...(Object.keys(device).length ? { [kind]: device } : {}), ...(location ? { location } : {}) })
      const solar = solarPatch(patch)
      for (const key of Object.keys(solar)) if (JSON.stringify(key === 'location' ? sun.location(out?.location) : out?.[kind]?.[key]) !== JSON.stringify(solar[key])) {
        throw new Error('The host has not saved Sun yet.')
      }
      if (out?.[kind] && patch.options) for (const [id, options] of Object.entries(patch.options)) {
        for (const [key, chosen] of Object.entries(options)) if (out[kind].options?.[id]?.[key] !== chosen) {
          throw new Error('The host has not saved these theme options yet.')
        }
      }
      acknowledge(patch); onSaved()
    }
    catch (e) {
      const local = solarPatch(patch)
      if (Object.keys(local).length) {
        cache({ ...local, ...draft }); localOnly = true; sunUnsupported = true
        for (const key of Object.keys(local)) delete patch[key]
      }
      if (Object.keys(patch).length) {
        pending = mergeAppearance(patch, pending)
        if (!Object.keys(local).length) error = `${e.message} Changes apply here until saved.`
      }
    }
    finally { saving = false; changed() }
    if (!error && Object.keys(pending).length) await save()
  }
  function set(changes) {
    const patch = { ...changes }
    if (!Object.keys(patch).length) return
    if (Object.keys(patch).some(k => !['mode', 'transition', 'size', 'theme', 'options', 'location', 'board_view'].includes(k))) return
    if (patch.options !== undefined) {
      if (!patch.options || typeof patch.options !== 'object' || Array.isArray(patch.options)) return
      if (!Object.keys(patch.options).length) return
      for (const [id, options] of Object.entries(patch.options)) {
        const manifest = installed.find(t => t.id === id)
        if (!manifest || !options || typeof options !== 'object' || Array.isArray(options)) return
        if (!Object.keys(options).length) return
        for (const [key, chosen] of Object.entries(options)) {
          if (!manifest.options?.find(o => o.id === key)?.choices.some(c => c.id === chosen)) return
        }
      }
    }
    if (patch.board_view !== undefined && !['cards', 'doc'].includes(patch.board_view)) return
    if (patch.theme !== undefined && themeId(patch.theme) !== patch.theme) return
    if (patch.mode !== undefined && !['light', 'dark', 'sun', 'system'].includes(patch.mode)) return
    if (patch.transition !== undefined && !['fast', 'normal', 'long'].includes(patch.transition)) return
    if (patch.location !== undefined) {
      patch.location = sun.location(patch.location)
      if (!patch.location) return
    }
    if (patch.size !== undefined) {
      if (!Number.isFinite(patch.size)) return
      patch.size = Math.max(75, Math.min(150, Math.round(patch.size / 5) * 5))
    }
    revision++
    const firstSun = patch.mode === 'sun' && !sun.location(value.location)
    if (firstSun) patch.location = sun.fallback()
    if (patch.mode === 'sun' || draft.mode !== undefined || patch.location || patch.transition !== undefined || draft.transition !== undefined) {
      cache({ ...(patch.mode ? { mode: patch.mode } : {}), ...(patch.location ? { location: patch.location } : {}), ...(patch.transition ? { transition: patch.transition } : {}) })
    }
    value = mergeAppearance(value, patch); pending = mergeAppearance(pending, patch)
    const keepActiveShift = !('mode' in patch || 'theme' in patch || 'options' in patch || 'transition' in patch)
    apply({ keepActiveShift }); changed(); clearTimeout(timer); timer = setTimeout(save, 180)
    if (firstSun) useLocation()
  }
  async function useLocation() {
    if (locating) return
    locating = true; changed()
    try {
      const position = await locate()
      set({ location: { latitude: position.coords.latitude, longitude: position.coords.longitude, source: 'geolocation', timeZone: browserTimeZone() } })
    } catch { /* The saved time-zone fallback already explains the retry. */ }
    finally { locating = false; changed() }
  }
  async function refresh() {
    apply({ keepActiveShift: true }) // Recheck immediately after sleep/focus, including when offline.
    if (saving || Object.keys(pending).length) return
    const at = revision, read = ++reading
    try {
      const out = await request('GET')
      if (at !== revision || read !== reading || saving || Object.keys(pending).length || !out?.[kind]) return
      const next = out[kind]
      if (!['light', 'dark', 'sun', 'system'].includes(next.mode) || !Number.isInteger(next.size) || next.size < 75 || next.size > 150 || next.size % 5) return
      value = { ...(['cards', 'doc'].includes(next.board_view) ? { board_view: next.board_view } : {}), mode: next.mode, transition: transitionId(next.transition), size: next.size, theme: themeId(next.theme), ...(sun.location(out.location) ? { location: sun.location(out.location) } : {}), ...draft, ...(withOptions ? { options: optionValues(next.options, installed) } : {}) }; error = null; apply({ keepActiveShift: true }); changed()
    } catch (e) { if (at === revision && read === reading) { error = e.message; changed() } }
  }
  media?.addEventListener?.('change', () => { if (value.mode === 'system') { apply(); changed() } })
  motionQuery?.addEventListener?.('change', () => { if (shift.active && isReducedMotion()) { apply(); changed() } })
  globalThis.addEventListener?.('focus', () => { if (value.mode === 'sun') { apply({ keepActiveShift: true }); changed() } })
  document.addEventListener?.('visibilitychange', () => { if (!document.hidden && value.mode === 'sun') { apply(); changed() } })
  // Timer callbacks run on waking; this also catches a changed system clock.
  const wake = setInterval(() => { if (value.mode === 'sun') { apply({ keepActiveShift: true }); changed() } }, 60000)
  wake.unref?.()
  if (Object.keys(pending).length) { timer = setTimeout(save, 180); timer.unref?.() }
  return { apply, set, save, refresh, useLocation, kind, installed,
    get solar() {
      const place = sun.location(value.location) || sun.fallback(), solar = sun.state(now(), place)
      const time = ms => formatTime(Math.round(ms / 60000) * 60000, { timeZone: place.timeZone })
      return { ...solar, place, times: solar.polar ? `Light ${solar.polar === 'day' ? 'all day' : 'returns after polar night'}` : `Light ${time(solar.start)} – ${time(solar.end)}`, coordinates: `${place.latitude.toFixed(1)}°, ${place.longitude.toFixed(1).replace('-', '−')}°` }
    },
    // Wear a theme without choosing it: the Market's Try it. `null` stops.
    // The theme's tokens must already be in the page (themes.css for an
    // installed one; the Market loads a listed one's).
    try(id) {
      const next = typeof id === 'string' && /^[a-z][a-z0-9-]*$/.test(id) ? id : null
      if (next === trying) return
      trying = next; apply(); changed()
    },
    get trying() { return trying },
    install(catalog) {
      installed.splice(0, installed.length, ...catalog); withOptions = installed.some(t => t.options?.length)
      value = { ...value, theme: themeId(value.theme), ...(withOptions ? { options: optionValues(value.options, installed) } : {}) }; apply(); changed()
    }, get value() { return structuredClone(value) }, get status() { return status() },
    get current() { return root.dataset.theme === 'dark' ? 'dark' : 'light' },
    get shifting() { return Boolean(shift.active) },
    toggle() { set({ mode: this.current === 'dark' ? 'light' : 'dark' }); return this.current },
    key(event, desktop) {
      if (!desktop || event.altKey || event.isComposing || !(event.ctrlKey || event.metaKey)) return false
      const size = event.key === '0' ? 100 : ['+', '='].includes(event.key) ? value.size + 5 : event.key === '-' ? value.size - 5 : null
      if (size === null) return false
      event.preventDefault(); set({ size }); return true
    },
  }
}
