import { optionValues } from '../public/theme-options.js'
import { isValidSize, SIZE_DEFAULT } from '../public/scale.js'
import '../public/sun.js'
// Only these public preference fields may be embedded in the boot script.
export function appearanceValues(value, themes = [{ id: 'latte' }, { id: 'plain' }]) {
  const clean = (v) => ({
    mode: ['light', 'dark', 'sun', 'system'].includes(v?.mode) ? v.mode : 'system',
    transition: ['fast', 'normal', 'long'].includes(v?.transition) ? v.transition : 'normal',
    size: isValidSize(v?.size) ? v.size : SIZE_DEFAULT,
    theme: themes.some(t => t.id === v?.theme) ? v.theme : 'latte',
    ...(themes.some(t => t.options?.length) ? { options: optionValues(v?.options, themes) } : {}),
  })
  return { phone: clean(value?.phone), desktop: clean(value?.desktop), ...(globalThis.LoamSun.location(value?.location) ? { location: globalThis.LoamSun.location(value.location) } : {}) }
}

async function composerBoot(broker) {
  try {
    const out = await broker.json('GET', '/v1/settings/composer', undefined, { signal: AbortSignal.timeout(3000) })
    if (out.status === 200) {
      const value = JSON.parse(out.body).send_key
      if (value === 'enter' || value === 'modifier') return value
    }
  } catch { /* The app retries when the broker comes back. */ }
  return null
}

async function faceSlotsBoot(broker) {
  try {
    const out = await broker.json('GET', '/v1/agents', undefined, { signal: AbortSignal.timeout(3000) })
    if (out.status === 200) {
      const agents = JSON.parse(out.body)?.agents
      if (Array.isArray(agents)) {
        const slots = {}
        for (const a of agents) {
          if (typeof a?.id === 'string' && a.id && Number.isInteger(a.face_slot) && a.face_slot >= 0) slots[a.id] = a.face_slot
        }
        return slots
      }
    }
  } catch { /* The app loads agents when the broker comes back. */ }
  return {}
}

export async function appearanceBoot(broker, themes = [{ id: 'latte', name: 'Latte' }, { id: 'plain', name: 'Plain' }]) {
  const sendKey = composerBoot(broker)
  const faceSlots = faceSlotsBoot(broker)
  let data
  try {
    const out = await broker.json('GET', '/v1/settings/appearance', undefined, { signal: AbortSignal.timeout(3000) })
    if (out.status !== 200) throw new Error('Appearance settings are not available on the host yet.')
    data = { values: appearanceValues(JSON.parse(out.body), themes), available: true, themes }
  } catch {
    data = { values: appearanceValues(null, themes), available: false, themes }
  }
  data.sendKey = await sendKey
  const slots = await faceSlots
  return `globalThis.LOAM_FACE_SLOTS = ${JSON.stringify(slots).replace(/</g, '\\u003c')};\nglobalThis.__loamAppearance = ${JSON.stringify(data).replace(/</g, '\\u003c')};\n`
}
