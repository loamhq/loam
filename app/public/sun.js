// Shared by the parser-blocking boot script, controller and server sanitizer.
// NOAA/Meeus solar equations, apparent horizon -0.833°; no service or dependency.
// https://gml.noaa.gov/grad/solcalc/calcdetails.html
;(function () {
  if (globalThis.LoamSun) return
  const rad = Math.PI / 180, day = 86400000, halfHour = 1800000
  const sin = x => Math.sin(x * rad), cos = x => Math.cos(x * rad)
  function solarTerms(ms) {
    const t = (ms / day + 2440587.5 - 2451545) / 36525
    const l = (280.46646 + t * (36000.76983 + .0003032 * t)) % 360
    const m = 357.52911 + t * (35999.05029 - .0001537 * t)
    const e = .016708634 - t * (.000042037 + .0000001267 * t)
    const c = sin(m) * (1.914602 - t * (.004817 + .000014 * t)) + sin(2 * m) * (.019993 - .000101 * t) + sin(3 * m) * .000289
    const omega = 125.04 - 1934.136 * t
    const apparent = l + c - .00569 - .00478 * sin(omega)
    const obliquity = 23 + (26 + (21.448 - t * (46.815 + t * (.00059 - t * .001813))) / 60) / 60 + .00256 * cos(omega)
    const declination = Math.asin(sin(obliquity) * sin(apparent))
    const y = Math.tan(obliquity * rad / 2) ** 2
    const equation = 4 / rad * (y * sin(2 * l) - 2 * e * sin(m) + 4 * e * y * sin(m) * cos(2 * l) - .5 * y * y * sin(4 * l) - 1.25 * e * e * sin(2 * m))
    return { declination, equation }
  }
  // utcDate identifies the calendar date at the saved place, not a UTC event day.
  function solarDay(utcDate, latitude, longitude) {
    const midnight = Date.UTC(utcDate.getUTCFullYear(), utcDate.getUTCMonth(), utcDate.getUTCDate())
    const noon = midnight + (720 - 4 * longitude) * 60000
    const hourAngle = declination => (sin(-.833) - sin(latitude) * Math.sin(declination)) / (cos(latitude) * Math.cos(declination))
    const atNoon = solarTerms(noon), angle = hourAngle(atNoon.declination)
    if (angle < -1) return { polar: 'day', sunrise: null, sunset: null }
    if (angle > 1) return { polar: 'night', sunrise: null, sunset: null }
    function crossing(sign) {
      let at = noon
      for (let i = 0; i < 3; i++) {
        const terms = solarTerms(at)
        const ha = Math.acos(Math.max(-1, Math.min(1, hourAngle(terms.declination)))) / rad
        at = midnight + (720 - 4 * longitude - terms.equation + sign * 4 * ha) * 60000
      }
      return at
    }
    return { polar: null, sunrise: crossing(-1), sunset: crossing(1) }
  }
  function validZone(zone) {
    if (typeof zone !== 'string' || zone.length > 100) return false
    try { new Intl.DateTimeFormat('en', { timeZone: zone }); return true } catch { return false }
  }
  function location(value) {
    if (!value || typeof value !== 'object' || !Number.isFinite(value.latitude) || Math.abs(value.latitude) > 90 || !Number.isFinite(value.longitude) || Math.abs(value.longitude) > 180 || !['geolocation', 'timezone'].includes(value.source) || !validZone(value.timeZone)) return null
    return { latitude: Math.round(value.latitude * 10) / 10, longitude: Math.round(value.longitude * 10) / 10, source: value.source, timeZone: value.timeZone }
  }
  // Representative cities, intentionally approximate. Aliases cover older IANA
  // names still returned by browsers. Unlisted zones use their UTC offset at 0°.
  const cities = [
    ['America/Los_Angeles US/Pacific', 37.8, -122.4], ['America/Vancouver', 49.3, -123.1],
    ['America/New_York US/Eastern America/Toronto', 40.7, -74], ['America/Chicago US/Central', 41.9, -87.6],
    ['America/Denver US/Mountain', 39.7, -105], ['America/Phoenix US/Arizona', 33.4, -112.1],
    ['America/Anchorage US/Alaska', 61.2, -149.9], ['Pacific/Honolulu US/Hawaii', 21.3, -157.9],
    ['America/Halifax', 44.6, -63.6], ['America/St_Johns', 47.6, -52.7], ['America/Winnipeg', 49.9, -97.1],
    ['America/Edmonton', 53.5, -113.5], ['America/Regina', 50.4, -104.6], ['America/Mexico_City', 19.4, -99.1],
    ['America/Bogota America/Lima', 4.7, -74.1], ['America/Sao_Paulo', -23.6, -46.6],
    ['America/Argentina/Buenos_Aires America/Buenos_Aires', -34.6, -58.4], ['America/Santiago', -33.4, -70.7],
    ['Europe/London Europe/Dublin', 51.5, -.1], ['Europe/Paris Europe/Brussels', 48.9, 2.4],
    ['Europe/Berlin Europe/Amsterdam Europe/Prague', 52.5, 13.4], ['Europe/Madrid', 40.4, -3.7],
    ['Europe/Lisbon', 38.7, -9.1], ['Europe/Rome', 41.9, 12.5], ['Europe/Zurich Europe/Vienna', 47.4, 8.5],
    ['Europe/Oslo', 59.9, 10.8], ['Europe/Stockholm', 59.3, 18.1], ['Europe/Helsinki', 60.2, 24.9],
    ['Europe/Copenhagen', 55.7, 12.6], ['Atlantic/Reykjavik', 64.1, -21.9], ['Europe/Warsaw', 52.2, 21],
    ['Europe/Athens', 38, 23.7], ['Europe/Kyiv Europe/Kiev', 50.5, 30.5], ['Europe/Moscow', 55.8, 37.6],
    ['Europe/Istanbul Asia/Istanbul', 41, 29], ['Africa/Cairo', 30, 31.2], ['Africa/Lagos', 6.5, 3.4],
    ['Africa/Johannesburg', -26.2, 28], ['Africa/Nairobi', -1.3, 36.8], ['Africa/Casablanca', 33.6, -7.6],
    ['Asia/Jerusalem Asia/Tel_Aviv', 31.8, 35.2], ['Asia/Dubai', 25.2, 55.3], ['Asia/Riyadh', 24.7, 46.7],
    ['Asia/Kolkata Asia/Calcutta', 19.1, 72.9], ['Asia/Karachi', 24.9, 67], ['Asia/Dhaka', 23.8, 90.4],
    ['Asia/Kathmandu Asia/Katmandu', 27.7, 85.3], ['Asia/Bangkok', 13.8, 100.5],
    ['Asia/Singapore Asia/Kuala_Lumpur', 1.4, 103.8], ['Asia/Jakarta', -6.2, 106.8],
    ['Asia/Shanghai Asia/Chongqing PRC', 31.2, 121.5], ['Asia/Hong_Kong', 22.3, 114.2],
    ['Asia/Taipei', 25, 121.6], ['Asia/Tokyo Japan', 35.7, 139.7], ['Asia/Seoul ROK', 37.6, 127],
    ['Asia/Manila', 14.6, 121], ['Australia/Sydney Australia/ACT', -33.9, 151.2],
    ['Australia/Melbourne Australia/Victoria', -37.8, 145], ['Australia/Brisbane Australia/Queensland', -27.5, 153],
    ['Australia/Adelaide Australia/South', -34.9, 138.6], ['Australia/Perth Australia/West', -32, 115.9],
    ['Australia/Darwin Australia/North', -12.5, 130.8], ['Australia/Hobart Australia/Tasmania', -42.9, 147.3],
    ['Pacific/Auckland NZ', -36.8, 174.8], ['Pacific/Fiji', -18.1, 178.4], ['Pacific/Apia', -13.8, -171.8], ['Pacific/Kiritimati', 1.9, -157.4],
    ['UTC Etc/UTC Etc/GMT GMT', 51.5, 0],
  ]
  function fallback(zone = globalThis.LOAM_INSTALLATION?.timeZone ?? Intl.DateTimeFormat().resolvedOptions().timeZone) {
    if (!validZone(zone)) zone = 'UTC'
    const city = cities.find(([zones]) => zones.split(' ').includes(zone))
    const parts = new Intl.DateTimeFormat('en', { timeZone: zone, timeZoneName: 'longOffset' }).formatToParts(new Date())
    const offset = parts.find(p => p.type === 'timeZoneName')?.value.match(/GMT([+-])(\d+)(?::(\d+))?/)
    const longitude = offset ? (offset[1] === '-' ? -1 : 1) * (Number(offset[2]) + Number(offset[3] || 0) / 60) * 15 : 0
    return location({ latitude: city?.[1] ?? 0, longitude: city?.[2] ?? Math.max(-180, Math.min(180, longitude)), source: 'timezone', timeZone: zone })
  }
  function state(now = Date.now(), place = fallback()) {
    place = location(place) || fallback()
    const parts = new Intl.DateTimeFormat('en', { timeZone: place.timeZone, year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric', hourCycle: 'h23' }).formatToParts(new Date(now))
    const part = key => Number(parts.find(p => p.type === key).value)
    const offset = Date.UTC(part('year'), part('month') - 1, part('day'), part('hour'), part('minute'), part('second')) - Math.floor(now / 1000) * 1000
    // Political zones can put a western longitude on the next calendar day.
    const date = Date.UTC(part('year'), part('month') - 1, part('day')) + Math.round((place.longitude * 240000 - offset) / day) * day
    const today = solarDay(new Date(date), place.latitude, place.longitude)
    let light = today.polar === 'day', next = Infinity
    for (let i = -2; i <= 2; i++) {
      const solar = solarDay(new Date(date + i * day), place.latitude, place.longitude)
      if (solar.polar) continue
      const start = solar.sunrise - halfHour, end = solar.sunset + halfHour
      if (now >= start && now < end) light = true
      for (const boundary of [start, end]) if (boundary > now) next = Math.min(next, boundary)
    }
    return { ...today, mode: light ? 'light' : 'dark', next: Number.isFinite(next) ? next : now + 6 * 3600000, start: today.sunrise === null ? null : today.sunrise - halfHour, end: today.sunset === null ? null : today.sunset + halfHour }
  }
  // Only unacknowledged Sun/location edits are cached, never other preferences.
  function local(kind, value) {
    const key = 'loam.appearance.sun'
    try {
      const stored = JSON.parse(localStorage.getItem(key) || '{}')
      if (arguments.length > 1) {
        if (value?.mode) stored[kind] = { mode: value.mode }
        else delete stored[kind]
        if (value?.location) stored.location = value.location
        else delete stored.location
        if (Object.keys(stored).length) localStorage.setItem(key, JSON.stringify(stored))
        else localStorage.removeItem(key)
      }
      const clean = {}
      if (['sun', 'light', 'dark', 'system'].includes(stored[kind]?.mode)) clean.mode = stored[kind].mode
      if (location(stored.location)) clean.location = location(stored.location)
      return clean
    } catch { return {} }
  }
  globalThis.LoamSun = { solarDay, location, fallback, state, local }
})()
