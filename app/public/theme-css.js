const slug = /^[a-z][a-z0-9-]*$/
const coreOptions = {
  'agent-colours': ['palette', 'latte-tones'],
  'agent-shapes': ['distinct', 'circle', 'rounded-square', 'latte-art', 'signature'],
  'face-content': ['mark', 'initials'],
  'latte-mug': ['on', 'off'],
  'latte-inside': ['pour', 'initials'],
}
export function validateThemeOptions(options, id) {
  if (options === undefined) return []
  const seen = new Set(), label = s => typeof s === 'string' && s.trim() && s.length <= 80
  if (!Array.isArray(options) || options.length > 8) throw new Error(`Invalid theme options: ${id}`)
  for (const option of options) {
    if (!option || typeof option !== 'object' || Object.keys(option).some(k => !['id', 'label', 'choices', 'default'].includes(k)) || typeof option.id !== 'string' || !slug.test(option.id) || option.id.length > 48 || seen.has(option.id) || !label(option.label) || !Array.isArray(option.choices) || option.choices.length < 2 || option.choices.length > 8) throw new Error(`Invalid theme option: ${id}`)
    seen.add(option.id)
    const choices = new Set()
    for (const choice of option.choices) {
      if (!choice || Object.keys(choice).some(k => !['id', 'label'].includes(k)) || typeof choice.id !== 'string' || !slug.test(choice.id) || choice.id.length > 48 || !label(choice.label) || choices.has(choice.id) || (Object.hasOwn(coreOptions, option.id) && !coreOptions[option.id].includes(choice.id))) throw new Error(`Invalid theme choice: ${id}: ${option.id}`)
      choices.add(choice.id)
    }
    if (!choices.has(option.default)) throw new Error(`Invalid theme default: ${id}: ${option.id}`)
  }
  return options
}
// Preserve quoted family names and case-sensitive var() references.
const normalize = value => value.replace(/'[^']*'|"[^"]*"|\s+|#[a-f\d]{3,8}\b/gi, part => /^\s/.test(part) ? '' : part.startsWith('#') ? part.toLowerCase() : part)

// The core owns the contract, including inherited defaults in each mode.
export function parseCoreTokens(css) {
  const blocks = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)]
  const declarations = body => new Map([...body.matchAll(/(--[a-z][a-z0-9-]*):\s*([^;]+);/g)].map(m => [m[1], m[2].trim()]))
  const light = declarations(blocks.find(b => b[1].trim() === ':root')[2])
  const dark = new Map([...light, ...declarations(blocks.find(b => b[1].trim() === ':root[data-theme="dark"]')[2])])
  return { light, dark }
}

// Themes are partial token-only data. A theme can intentionally change geometry,
// but cannot add component rules, fetch resources or repeat a core default.
export function validateThemeCSS(css, id, defaults, options = []) {
  const tokens = new Set(defaults.light.keys())
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const base = `:root[data-look="${id}"]`
  const scopes = new Map([[base, ['light', 'dark']], [`${base}[data-theme="light"]`, ['light']], [`${base}[data-theme="dark"]`, ['dark']]])
  const seen = new Set()
  let end = 0
  for (const rule of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (text.slice(end, rule.index).trim()) throw new Error(`Invalid theme syntax: ${id}`)
    const selector = rule[1].trim()
    if (!scopes.has(selector) && selector.startsWith(base)) {
      let tail = selector.slice(base.length), modes = ['light', 'dark']
      const mode = /^\[data-theme="(light|dark)"\]/.exec(tail)
      if (mode) { modes = [mode[1]]; tail = tail.slice(mode[0].length) }
      const attrs = [...tail.matchAll(/\[data-option-([a-z][a-z0-9-]*)="([a-z][a-z0-9-]*)"\]/g)]
      if (attrs.length && attrs.map(m => m[0]).join('') === tail && new Set(attrs.map(m => m[1])).size === attrs.length && attrs.every(m => options.find(o => o.id === m[1])?.choices.some(c => c.id === m[2]))) scopes.set(selector, modes)
    }
    if (!scopes.has(selector) || seen.has(selector)) throw new Error(`Invalid theme scope: ${id}`)
    seen.add(selector)
    const assigned = new Set()
    for (const declaration of rule[2].split(';').map(s => s.trim()).filter(Boolean)) {
      const match = /^(--[a-z][a-z0-9-]*):\s*(.+)$/.exec(declaration)
      if (!match || /^--c\d+$/.test(match[1]) || !tokens.has(match[1]) || assigned.has(match[1])) throw new Error(`Invalid theme token: ${id}: ${declaration}`)
      const value = match[2]
      if (/[{}@<>\\]|!important|(?:url|image|image-set|attr|env)\s*\(/i.test(value)) throw new Error(`Invalid theme value: ${id}`)
      for (const ref of value.matchAll(/var\(\s*(--[\w-]+)/g)) {
        if (!tokens.has(ref[1])) throw new Error(`Unknown theme reference: ${ref[1]}`)
        const family = /^(--(?:paper|latte))-/.exec(match[1])?.[1]
        if (family && !ref[1].startsWith(`${family}-`)) throw new Error(`Crossed colour family: ${id}: ${match[1]} -> ${ref[1]}`)
      }
      // Ignore formatting differences, including whitespace and colour case.
      for (const mode of scopes.get(selector)) {
        if (normalize(value) === normalize(defaults[mode].get(match[1]))) throw new Error(`Redundant theme token: ${id}: ${mode}: ${match[1]}`)
      }
      assigned.add(match[1])
    }
    end = rule.index + rule[0].length
  }
  if (text.slice(end).trim()) throw new Error(`Invalid theme syntax: ${id}`)
  return css
}
