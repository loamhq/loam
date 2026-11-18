// Runs inside a real page. Checks visible glyphs/text against the effective
// opaque ground, including inherited ink and transparent nested controls.
export function inspectColourFamilies() {
  if (document.documentElement.dataset.look !== 'latte') return []
  // Any computed colour read as sRGB bytes: Chromium reports a colour mixed
  // or written in oklab/oklch/lab as `oklab(…)`, one mixed in srgb as
  // `color(srgb …)` with 0–1 channels, so reading the first three numbers
  // as 0–255 misread both (2026-11-17). A canvas converts every syntax the
  // browser knows. The key, "r,g,b,a", is what grounds and inks match on.
  const pen = document.createElement('canvas').getContext('2d', { willReadFrequently: true })
  const rgba = css => {
    pen.clearRect(0, 0, 1, 1); pen.fillStyle = '#000'; pen.fillStyle = css; pen.fillRect(0, 0, 1, 1)
    return [...pen.getImageData(0, 0, 1, 1).data]
  }
  const key = css => rgba(css).join(',')
  const colour = token => {
    const n = document.createElement('span'); n.style.color = `var(--${token})`; document.body.append(n)
    const value = key(getComputedStyle(n).color); n.remove(); return value
  }
  const paperInks = ['paper-ink','paper-ink-2','paper-ink-3'].map(colour)
  const latteInks = ['latte-coffee','latte-coffee-2','latte-espresso-ink'].map(colour)
  const grounds = new Map(['paper-page','paper-pane','paper-raised','paper-card','paper-hover','latte-foam','latte-track','latte-hover','latte-disabled','latte-espresso','latte-espresso-hover','latte-espresso-pressed'].map(k => [colour(k), k]))
  const ratio = (a,b) => {
    const light = colour => {
      const c = colour.split(',').slice(0,3).map(n=>Number(n)/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4)
      return c[0]*.2126+c[1]*.7152+c[2]*.0722
    }
    const x=light(a),y=light(b); return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)
  }
  const violations = []
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('.blob,.fs-blob,iframe')) continue
    const ownText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
    if (!ownText && !el.matches('svg,input,textarea,select')) continue
    const r = el.getBoundingClientRect(), style = getComputedStyle(el)
    if (!r.width || !r.height || r.bottom <= 0 || r.top >= innerHeight || style.visibility === 'hidden') continue
    let ground = null, paint = null
    for (let p = el; p; p = p.parentElement) {
      const bg = key(getComputedStyle(p).backgroundColor)
      if (!bg.endsWith(',0')) { ground = grounds.get(bg); paint = bg; break }
    }
    const ink = key(style.color)
    const crossed = ground?.startsWith('latte-') ? paperInks.includes(ink) : ground?.startsWith('paper-') ? latteInks.includes(ink) : false
    const minimum=el.matches('svg')?3:4.5
    const lowContrast=ground && ratio(ink,paint)<minimum
    if (crossed || lowContrast) violations.push({ issue:crossed?'family':'contrast', ratio:Number(ratio(ink,paint).toFixed(3)), minimum, element: el.tagName.toLowerCase(), classes: el.getAttribute('class'), parent: el.parentElement?.className?.baseVal ?? el.parentElement?.className, text: el.textContent.trim().slice(0,60), ground, ink:style.color })
  }
  return violations
}
