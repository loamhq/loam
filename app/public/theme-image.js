// Theme assets stay inside their package, including installed Yours themes.
export function themeImage(manifest) {
  if (manifest.image == null) {
    if (manifest.image_dark != null || manifest.image_position_dark != null) throw new Error('A dark theme image needs a default image.')
    return {}
  }
  const image = value => {
    if (typeof value !== 'string' || !/^(?:[a-zA-Z0-9_-]+\/)*[a-zA-Z0-9_-]+\.(?:webp|avif|jpe?g|png)$/i.test(value)) {
      throw new Error('Theme image must be a relative image path inside the theme folder.')
    }
    return value
  }
  const position = value => {
    if (typeof value !== 'string' || !/^\d{1,3}(?:\.\d+)?% \d{1,3}(?:\.\d+)?%$/.test(value) || value.split(' ').some(v => parseFloat(v) > 100)) {
      throw new Error('Theme image_position must be two percentages between 0% and 100%.')
    }
    return value
  }
  return { image: image(manifest.image), image_position: position(manifest.image_position ?? '50% 50%'),
    ...(manifest.image_dark != null ? { image_dark: image(manifest.image_dark) } : {}),
    ...(manifest.image_position_dark != null ? { image_position_dark: position(manifest.image_position_dark) } : {}) }
}

export function themeImageURL(theme, mode = 'light') {
  if (!theme.image) return null
  const metadata = themeImage(theme), image = mode === 'dark' ? metadata.image_dark ?? metadata.image : metadata.image
  if (!/^[a-z][a-z0-9-]*$/.test(theme.id)) throw new Error('Invalid theme image id.')
  return theme.layer === 'yours' ? `/api/plugins/${theme.id}/files/${image}` : `/themes/${theme.id}/${image}`
}

export function themeImagePosition(theme, mode = 'light') {
  const metadata = themeImage(theme)
  return (mode === 'dark' ? metadata.image_position_dark : null) ?? metadata.image_position ?? '50% 50%'
}
