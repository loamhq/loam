// Reproducible family pair matrix, independent of a browser or audit packages.
import { readFile, writeFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import { loadCoreTokens, validateThemeCSS } from '../src/themes.js'
export function contrast(a, b) {
  const light = hex => {
    const c = hex.slice(1).match(/../g).map(n => parseInt(n, 16) / 255).map(n => n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4)
    return c[0] * .2126 + c[1] * .7152 + c[2] * .0722
  }
  const x = light(a), y = light(b)
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05)
}
export async function resolvedTheme(id, mode) {
  // Latte and Plain ship; the other first-party looks are listed plugins (2026-11-18).
  const css = await readFile(new URL(`../public/themes/${id}/theme.css`, import.meta.url), 'utf8')
    .catch(() => readFile(new URL(`../../plugins/_listed/${id}/theme.css`, import.meta.url), 'utf8'))
  const defaults = await loadCoreTokens()
  validateThemeCSS(css, id, defaults)
  const resolved = new Map(defaults[mode])
  const blocks = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)]
  for (const selector of [`:root[data-look="${id}"]`, `:root[data-look="${id}"][data-theme="${mode}"]`]) {
    for (const block of blocks.filter(b => b[1].trim() === selector)) {
      for (const m of block[2].matchAll(/(--[\w-]+):\s*([^;]+);/g)) resolved.set(m[1], m[2].trim())
    }
  }
  function value(key, seen = new Set()) {
    if (seen.has(key)) throw new Error(`Circular theme token: ${key}`)
    seen.add(key)
    const raw = resolved.get(`--${key}`)
    if (!raw) throw new Error(`Missing theme token: ${key}`)
    return raw.replace(/var\(--([\w-]+)\)/g, (_, ref) => value(ref, new Set(seen)))
  }
  return Object.fromEntries([...resolved.keys()].map(k => [k.slice(2), value(k.slice(2))]))
}
export async function themeContrasts(id = 'latte') {
  const rows = []
  for (const mode of ['light', 'dark']) {
    const t = await resolvedTheme(id, mode)
    const add = (ink, ground, minimum = 4.5) => {
      const ratio = Number(contrast(t[ink], t[ground]).toFixed(3))
      if (!Number.isFinite(ratio)) throw new Error(`Unresolved colour: ${ink}/${ground}`)
      rows.push({ mode, ink, ground, ratio, minimum })
    }
    // Surfaces are tested only with their own family's inks. Hue is avatar paint,
    // never body ink; its fixed white glyph is tested separately in both modes.
    for (const family of ['paper', 'latte']) {
      for (const ground of Object.keys(t).filter(k => k.startsWith(`${family}-`) && t[`${k}-ink`])) {
        add(`${ground}-ink`, ground)
        for (const suffix of ['-2', '-3']) if (t[`${ground}-ink${suffix}`]) add(`${ground}-ink${suffix}`, ground)
      }
    }
    for (const ground of ['paper-page','paper-pane','paper-card','paper-raised','paper-hover']) {
      for (const ink of ['paper-mark-ink','paper-mark-active']) add(ink, ground)
      for (const ink of ['danger','warning','success']) add(ink, ground)
      add('signal-crema', ground, 3) // non-text unread, underline and focus signal
    }
    for (const hue of Object.keys(t).filter(k => k.startsWith('persona-') && !k.endsWith('-tint'))) add('text-on-avatar', hue, 3)
    for (let i = 0; i < 10; i++) add(i < 5 ? 'agent-latte-coffee' : 'agent-latte-foam', `agent-latte-${i}`, 3)
    for (const ink of ['text-code','text-code-muted','text-code-accent','text-code-danger']) add(ink, 'surface-code')
    for (const ground of ['surface-attention','surface-danger']) add('text-inverse', ground)
    add('text-code-accent', 'surface-attention')
    add('latte-espresso-ink', 'danger')
    // Existing dimmed code labels and fixed error-panel supporting text.
    for (const [ink,ground,opacity] of [['text-code-muted','surface-code',.7],['text-inverse','surface-danger',.75]]) {
      const a=t[ink].slice(1).match(/../g).map(n=>parseInt(n,16)), b=t[ground].slice(1).match(/../g).map(n=>parseInt(n,16))
      const blend='#'+a.map((n,i)=>Math.round(n*opacity+b[i]*(1-opacity)).toString(16).padStart(2,'0')).join('')
      rows.push({mode,ink,ground,opacity,ratio:Number(contrast(blend,t[ground]).toFixed(3)),minimum:4.5})
    }
  }
  return rows
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const rows = await themeContrasts(process.argv.includes('--plain') ? 'plain' : 'latte')
  const failures = rows.filter(r => r.ratio < r.minimum)
  if (process.argv.includes('--write')) await writeFile('docs/design-system/contrast.json', JSON.stringify(rows, null, 2) + '\n')
  console.log(`${rows.length} ${process.argv.includes('--plain') ? 'Plain' : 'Latte'} family pairs; minimum ${Math.min(...rows.map(r => r.ratio))}:1; ${failures.length} failures`)
  if (failures.length) { console.error(failures); process.exitCode = 1 }
}
