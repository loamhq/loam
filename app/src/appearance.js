import { optionValues } from '../public/theme-options.js'
import '../public/sun.js'
// Only these public preference fields may be embedded in the boot script.
export function appearanceValues(value, themes = [{ id: 'latte' }, { id: 'plain' }]) {
  const clean = (v) => ({
    mode: ['light', 'dark', 'sun', 'system'].includes(v?.mode) ? v.mode : 'system',
    transition: ['fast', 'normal', 'long'].includes(v?.transition) ? v.transition : 'normal',
    size: Number.isInteger(v?.size) && v.size >= 75 && v.size <= 150 && v.size % 5 === 0 ? v.size : 100,
    theme: themes.some(t => t.id === v?.theme) ? v.theme : 'latte',
    ...(themes.some(t => t.options?.length) ? { options: optionValues(v?.options, themes) } : {}),
  })
  return { phone: clean(value?.phone), desktop: clean(value?.desktop), ...(globalThis.LoamSun.location(value?.location) ? { location: globalThis.LoamSun.location(value.location) } : {}) }
}

export async function appearanceBoot(broker, themes = [{ id: 'latte', name: 'Latte' }, { id: 'plain', name: 'Plain' }]) {
  let data
  try {
    const out = await broker.json('GET', '/v1/settings/appearance', undefined, { signal: AbortSignal.timeout(3000) })
    if (out.status !== 200) throw new Error('Appearance settings are not available on the host yet.')
    data = { values: appearanceValues(JSON.parse(out.body), themes), available: true, themes }
  } catch {
    data = { values: appearanceValues(null, themes), available: false, themes }
  }
  return `globalThis.__loamAppearance = ${JSON.stringify(data).replace(/</g, '\\u003c')};\n`
}
