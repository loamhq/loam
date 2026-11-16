// Legacy pipeline tests inject a synthetic credential explicitly. Production
// readConfig never reads a Gemini key from the web process environment.
import { readConfig as runtimeConfig } from '../src/config.js'
import { createBroker } from '../src/broker.js'
export const readConfig = (env = {}) => ({ ...runtimeConfig(env), geminiApiKey: env.GEMINI_API_KEY || '' })
export function fixtureBroker(config) {
  const broker = createBroker(config), json = broker.json
  return { ...broker, async json(method, path, ...args) {
    if (/^\/v1\/plugins\/(dictation|read-aloud)\/secrets\/GEMINI_API_KEY\/value$/.test(path)) return { status: 200, body: JSON.stringify({ name: 'GEMINI_API_KEY', value: config.geminiApiKey, source: 'box' }) }
    return json(method, path, ...args)
  } }
}
