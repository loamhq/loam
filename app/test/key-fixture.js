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

// The two audio plugins' own servers (Dictation and Read aloud carry their
// server halves since 1.1.0), configured from the same kind of environment,
// with the synthetic key the pipeline tests inject.
import { readConfig as dictationRuntime } from '../../plugins/_listed/dictation/server/config.js'
import { readConfig as readAloudRuntime } from '../../plugins/_listed/read-aloud/server/config.js'
export const dictationConfig = (env = {}) => ({ ...dictationRuntime(env), geminiApiKey: env.GEMINI_API_KEY || '' })
export const readAloudConfig = (env = {}) => ({ ...readAloudRuntime({ SPEECH_PACES: 'Aoede=147', ...env }), geminiApiKey: env.GEMINI_API_KEY || '' })
