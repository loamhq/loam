// Pure manifest-driven preferences, shared by the server and browser.
// The host merges this same shape by theme, then by option (never wholesale).
export function optionValues(input, themes) {
  return Object.fromEntries(themes.filter(t => t.options?.length).map(theme => [theme.id,
    Object.fromEntries(theme.options.map(option => [option.id,
      option.choices.some(c => c.id === input?.[theme.id]?.[option.id]) ? input[theme.id][option.id] : option.default])),
  ]))
}

export function mergeAppearance(base, patch) {
  if (!patch.options) return { ...base, ...patch }
  const options = { ...base.options }
  for (const [id, values] of Object.entries(patch.options)) options[id] = { ...options[id], ...values }
  return { ...base, ...patch, options }
}

export function applyOptions(root, values, themes, theme) {
  // Remove the previous theme's attributes as well: an option exists only while
  // its declaring theme is active, even when another theme uses the same id.
  for (const item of themes) for (const option of item.options || []) root.removeAttribute(`data-option-${option.id}`)
  for (const option of themes.find(t => t.id === theme)?.options || []) {
    root.setAttribute(`data-option-${option.id}`, values?.[theme]?.[option.id] || option.default)
  }
}
