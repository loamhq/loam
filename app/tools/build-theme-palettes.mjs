// Rebuild the five approved palettes and manifests; never copies core defaults.
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { loadCoreTokens, validateThemeCSS } from '../src/themes.js'
import { newThemes } from './new-themes.js'
import { contrast } from './theme-contrast.mjs'
const palettes = JSON.parse(await readFile(new URL('./theme-palettes.json', import.meta.url)))
const defaults = await loadCoreTokens()
export const roleTokens = { page: 'paper-page', pane: 'paper-pane', raised: 'paper-raised', card: 'paper-card', ink: 'paper-ink', ink2: 'paper-ink-2', ink3: 'paper-ink-3', rule: 'paper-rule', foam: 'latte-foam', coffee: 'latte-coffee', coffee2: 'latte-coffee-2', esp: 'latte-espresso', espink: 'latte-espresso-ink', signal: 'signal-crema' }
const rgb = hex => hex.slice(1).match(/../g).map(v => parseInt(v, 16))
const mix = (a, b, share) => '#' + rgb(a).map((v, i) => Math.round(v * share + rgb(b)[i] * (1 - share)).toString(16).padStart(2, '0')).join('').toUpperCase()
const alpha = (hex, a) => `rgba(${rgb(hex).join(',')},${a})`
const block = (id, mode, values) => {
  const relevant = mode ? [mode] : ['light', 'dark']
  const lines = Object.entries(values).filter(([k, v]) => relevant.every(m => defaults[m].get('--' + k)?.toLowerCase().replaceAll(' ', '') !== v.toLowerCase().replaceAll(' ', '')))
  return `:root[data-look="${id}"]${mode ? `[data-theme="${mode}"]` : ''} {\n${lines.map(([k, v]) => `  --${k}: ${v};`).join('\n')}\n}`
}
const options = [
  { id: 'agent-shapes', label: 'Agent faces', choices: [{ id: 'signature', label: 'Signature' }, { id: 'distinct', label: 'Each its own' }, { id: 'circle', label: 'Circle' }, { id: 'rounded-square', label: 'Rounded square' }], default: 'circle' },
  { id: 'face-content', label: 'Inside', choices: [{ id: 'mark', label: 'Mark' }, { id: 'initials', label: 'Initials' }], default: 'mark' },
]
// Semantic colours stay independent of decorative signal and neutral ink.
const status = {
  'deep-forest': ['#963F32', '#77500E', '#35603F', '#F1A294', '#E3BC69', '#9BCBA7'],
  autumn: ['#9C382D', '#78500F', '#42603C', '#F3A595', '#E9BD69', '#ABCCA0'],
  yosemite: ['#943E34', '#785011', '#386044', '#F0A597', '#E2BD73', '#A2CBB1'],
  'mist-trail': ['#993F30', '#79500F', '#346046', '#F0A493', '#E0BE77', '#9FCDB3'],
  foundry: ['#973C2C', '#77501A', '#396048', '#EFA18B', '#E4BA75', '#A1C8AD'],
}
for (const theme of newThemes) {
  const themeOptions = options.map(option => option.id === 'agent-shapes' && theme.id === 'foundry' ? { ...option, default: 'rounded-square' } : option)
  const folder = new URL(`../public/themes/${theme.id}/`, import.meta.url)
  await mkdir(folder, { recursive: true })
  const shared = { 'font-body': 'Figtree, system-ui, sans-serif', 'font-thread': "'Thread Figtree', Figtree, system-ui, sans-serif", 'font-mono': "'JetBrains Mono', ui-monospace, monospace", 'font-display': `'${theme.font}', Georgia, serif` }
  if (theme.id === 'foundry') Object.assign(shared, { 'radius-sm': '4px', 'radius-md': '8px', 'radius-lg': '10px', 'radius-xl': '14px', 'radius-pill': '8px' })
  const blocks = [block(theme.id, null, shared)]
  for (const mode of ['light', 'dark']) {
    const r = palettes[theme.id][mode], dark = mode === 'dark'
    const code = dark ? r.page : r.ink, codeInk = dark ? r.ink : r.pane
    const [danger, warning, success] = status[theme.id].slice(dark ? 3 : 0)
    // Keep approved secondary coffee at AA on every derived control fill.
    const control = share => {
      let value = mix(r.coffee, r.foam, share)
      while (contrast(r.coffee2, value) < 4.5 && share > 0) value = mix(r.coffee, r.foam, share = Math.max(0, share - .005))
      return value
    }
    const derived = {
      'paper-hover': mix(r.ink, r.pane, dark ? .075 : .045),
      'paper-rule-strong': mix(r.ink, r.page, dark ? .48 : .55),
      'paper-hover-alpha': alpha(r.ink, dark ? '.05' : '.045'),
      'paper-pressed': alpha(r.ink, dark ? '.10' : '.09'),
      'paper-page-glass': alpha(r.page, '.94'), 'paper-pane-glass': alpha(r.pane, '.94'),
      'paper-highlight': mix(r.ink, r.page, dark ? .15 : .12),
      'latte-track': control(.06), 'latte-hover': control(.045),
      'latte-espresso-hover': mix(r.espink, r.esp, .025),
      'latte-espresso-pressed': mix(r.espink, r.esp, .05),
      'latte-disabled': control(.06),
      'latte-control-selected': 'var(--latte-hover)', 'latte-control-selected-ink': 'var(--latte-hover-ink)',
      danger, warning, success,
      'surface-code': code, 'text-code': codeInk,
      'text-code-muted': mix(codeInk, code, .82), 'text-code-accent': mix(codeInk, code, .9),
      'text-code-danger': mix(dark ? danger : status[theme.id][3], codeInk, .85), 'rule-code': mix(codeInk, code, .48),
      'surface-attention': mix(warning, '#171717', .24), 'surface-danger': mix(danger, '#171717', .24), 'text-inverse': codeInk,
      'shadow-ink': alpha(palettes[theme.id].light.ink, dark ? '.55' : '.18'),
      scrim: alpha(palettes[theme.id].light.ink, dark ? '.65' : '.45'),
      ...(contrast(r.ink3, r.card) < 4.5 ? { 'paper-card-ink-3': r.ink2, 'paper-mark-ink': r.ink2 } : {}),
    }
    const approved = Object.fromEntries(Object.entries(r).map(([k, v]) => [roleTokens[k], v]))
    blocks.push(block(theme.id, mode, { ...approved, ...derived }))
  }
  const css = '/* Generated by tools/build-theme-palettes.mjs from the approved roles. */\n' + blocks.join('\n\n') + '\n'
  validateThemeCSS(css, theme.id, defaults, themeOptions)
  await writeFile(new URL('theme.css', folder), css)
  await writeFile(new URL('PLUGIN.md', folder), `---\nid: ${theme.id}\nname: ${theme.name}\nversion: 1.0.0\nauthor: Loam contributors\napi: 0\nkind: [theme]\nimage: cover.webp\nimage_position: ${theme.position}\n${theme.positionDark ? `image_dark: cover-dark.webp\nimage_position_dark: ${theme.positionDark}\n` : ''}options: ${JSON.stringify(themeOptions)}\nplaces: []\noverrides: []\ntouches:\n  network: []\n  record: none\n  memory: none\n  server: false\n---\n\n${theme.name} is drawn from ${theme.inspiration}.\n`)
}
console.log('Generated five token-only themes and manifests; core-default validation passed.')
