// Parser-blocking: host values arrive before the stylesheet or app module.
// Device kind follows the primary pointer, never a desktop window's width.
;(function () {
  var kind = globalThis.matchMedia?.('(pointer: coarse)').matches ? 'phone' : 'desktop'
  var value = globalThis.__loamAppearance?.values?.[kind] || { mode: 'system', size: 100 }
  var localSize = globalThis.LoamSize?.local(kind)
  value = { ...value, location: globalThis.__loamAppearance?.values?.location, ...globalThis.LoamSun?.local(kind), ...(localSize !== undefined ? { size: localSize } : {}) }
  var root = document.documentElement
  root.dataset.look = value.theme || 'latte'
  globalThis.LoamThemeFonts?.(root.dataset.look)
  var theme = globalThis.__loamAppearance?.themes?.find(function (t) { return t.id === root.dataset.look })
  for (var option of theme?.options || []) {
    root.setAttribute('data-option-' + option.id, value.options?.[theme.id]?.[option.id] || option.default)
  }
  root.dataset.deviceKind = kind
  root.dataset.theme = value.mode === 'sun' ? globalThis.LoamSun.state(Date.now(), value.location).mode
    : value.mode === 'dark' || (value.mode === 'system' && globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light'
  var size = Number.isInteger(value.size) && value.size >= 60 && value.size <= 200 && value.size % 5 === 0 ? value.size : 100
  root.style.setProperty('--ui-scale', Math.round(size * 9) / 1000)
})()
