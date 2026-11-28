// DOM rectangles and pointer events use screen CSS pixels; scroll offsets and
// positioned styles inside the zoomed app use its unscaled layout pixels.
//
// The 100% UI default corresponds to 0.9 of the original unscaled layout, and
// user choices span 60%–200% in 5% steps (CSS zoom 0.54–1.80).
export const SIZE_MIN = 60
export const SIZE_MAX = 200
export const SIZE_STEP = 5
export const SIZE_DEFAULT = 100
export const SIZE_BASE = 0.9
export const LEGACY_SIZE_MIN = 75
export const LEGACY_SIZE_MAX = 150

export const isValidSize = (value) =>
  Number.isInteger(value) && value >= SIZE_MIN && value <= SIZE_MAX && value % SIZE_STEP === 0

export const clampSize = (value) =>
  Math.max(SIZE_MIN, Math.min(SIZE_MAX, Math.round(value / SIZE_STEP) * SIZE_STEP))

export const sizeToScale = (size) =>
  Math.round((isValidSize(size) ? size : SIZE_DEFAULT) * 9) / 1000

// Natural +/- steps across 60%–200%: 5% up to 150% and 10% above 150%, keeping
// every stop on the 5% grid (~5%–8% relative step across the whole range).
export function stepSize(size, direction) {
  if (!direction) return SIZE_DEFAULT
  const current = Number.isFinite(size) ? clampSize(size) : SIZE_DEFAULT
  if (direction > 0) {
    return current < 150
      ? Math.min(150, Math.floor(current / 5) * 5 + 5)
      : Math.min(SIZE_MAX, Math.floor(current / 10) * 10 + 10)
  }
  return current <= 150
    ? Math.max(SIZE_MIN, Math.ceil(current / 5) * 5 - 5)
    : Math.max(150, Math.ceil(current / 10) * 10 - 10)
}

// Convert a pre-relabel stored percentage (where 100% was zoom 1.0) onto the
// 0.9-base scale so non-default choices keep the same visual size, while the
// old default (100%) adopts the new 10%-smaller default (100% -> zoom 0.9).
export function migrateLegacySize(oldSize) {
  if (!Number.isInteger(oldSize) || oldSize === SIZE_DEFAULT) return SIZE_DEFAULT
  return clampSize(oldSize / SIZE_BASE)
}

export function uiScale() {
  const scale = Number(globalThis.document?.documentElement?.style?.getPropertyValue?.('--ui-scale'))
  return scale >= 0.5 && scale <= 2 ? scale : 1
}

export const layoutPixels = (pixels) => pixels / uiScale()
