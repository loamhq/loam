import { readAvatarColours } from '../public/avatar-colours.js'
import { readFaceVariants } from '../public/face-identity.js'
import { readFileWebUrls } from './file-links.js'
import { INSTALLATION_DEFAULTS } from '../public/installation.js'
import { readPreviewRoute } from './app-preview-route.js'
// Every knob this app has. All of it arrives in the environment; none of it is
// a literal anywhere else, and no default is a secret.

export function readConfig(env = process.env) {
  const previewRoute = readPreviewRoute({ base: env.APPS_PREVIEW_BASE, addr: env.APPS_PREVIEW_ADDR })
  const installation = { ...INSTALLATION_DEFAULTS }
  for (const [key, variable] of Object.entries({ appsDomain: 'APPS_DOMAIN', timeZone: 'APP_TIME_ZONE', inbox: 'VAULT_INBOX_FOLDER', boards: 'VAULT_BOARDS_FOLDER', generalBoard: 'VAULT_GENERAL_BOARD', attachments: 'VAULT_ATTACHMENTS_FOLDER' })) {
    installation[key] = (env[variable] ?? '').trim() || installation[key]
  }
  installation.appsDomain = installation.appsDomain.toLowerCase()
  if (!/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(installation.appsDomain)) throw new Error('APPS_DOMAIN must be a domain without a scheme, port or path.')
  try { new Intl.DateTimeFormat('en', { timeZone: installation.timeZone }) } catch { throw new Error('APP_TIME_ZONE must be an IANA time zone.') }
  for (const key of ['inbox', 'boards', 'generalBoard', 'attachments']) {
    if (installation[key].split('/').some(part => !part || part === '.' || part === '..') || /[\\\u0000-\u001f\u007f]/.test(installation[key])) throw new Error(`Invalid vault-relative path: ${key}`)
  }
  const port = Number.parseInt(env.APP_PORT ?? '8080', 10)
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new Error(`APP_PORT is not a port number: ${env.APP_PORT}`)
  }
  const brokerUrl = (env.BROKER_URL ?? '').trim().replace(/\/+$/, '')
  const brokerToken = (env.BROKER_TOKEN ?? '').trim()
  // Credentials belong to the broker's plugin secret store. This URL lets
  // tests point synthesis and transcription at a loopback provider.
  const geminiUrl = (env.GEMINI_API_URL ?? 'https://generativelanguage.googleapis.com').trim().replace(/\/+$/, '')
  // The text model that polishes a take (`src/polish.js`), on the same key.
  const polishModel = (env.DICTATION_POLISH_MODEL ?? '').trim() || 'gemini-3.5-flash-lite'
  // How much the app says on stdout: `debug` adds a line per static file,
  // `info` (the default) is every other request and every transcription,
  // `off` is silence. Anything else reads as `info`.
  const logLevel = (env.LOG_LEVEL ?? '').trim().toLowerCase() || 'info'
  // The biggest file a chat will carry is deliberately *not* here. It is the
  // host's number, the broker answers it on `GET /v1/limits`, and this app
  // relays that route rather than keeping a copy of it in an environment
  // variable that could disagree with the cap actually enforced.
  //
  // Speech — a reply read aloud, or a spoken brief of it — on the same key.
  // Every number has its default here and nowhere else; see `src/speech.js`.
  const speechTtsModelSet = (env.SPEECH_TTS_MODEL ?? '').trim()
  const speech = {
    ttsModel: speechTtsModelSet || 'gemini-3.8-flash-tts',
    // Whether the TTS model was named, or is the default: a tracking alias
    // found in the models list is used only when it was not named.
    ttsModelNamed: speechTtsModelSet !== '',
    briefModel: (env.SPEECH_BRIEF_MODEL ?? '').trim() || 'gemini-flash-latest',
    dir: (env.SPEECH_DIR ?? '').trim() || '/storage/speech',
    ttlHours: positive(env.SPEECH_TTL_HOURS, 48),
    maxChars: positiveInt(env.SPEECH_MAX_CHARS, 60_000),
    documentMaxChars: positiveInt(env.SPEECH_DOCUMENT_MAX_CHARS, 250_000),
    chunkChars: positiveInt(env.SPEECH_CHUNK_CHARS, 1_000),
    concurrency: positiveInt(env.SPEECH_CONCURRENCY, 4),
    briefMaxWords: positiveInt(env.SPEECH_BRIEF_MAX_WORDS, 300),
    ffmpeg: (env.SPEECH_FFMPEG ?? '').trim() || 'ffmpeg',
    // Which voice each agent speaks in. Agents are this installation's, so
    // their names are configuration and never a literal in the repo:
    // `agent=Voice:character;agent=Voice:character`. Unlisted agents get the
    // default in `src/speech.js`.
    voices: (env.SPEECH_VOICES ?? '').trim(),
    paces: (env.SPEECH_PACES ?? '').trim(),
  }
  // Published vault URLs (`https://…`, comma-separated) whose links open in
  // Files instead of leaving the app. This installation's hostnames are
  // configuration and never a literal in the repo.
  const filesVaultUrls = (env.FILES_VAULT_URLS ?? '')
    .split(',')
    .map((u) => u.trim().replace(/\/+$/, ''))
    .filter((u) => /^https:\/\/[^/?#\s]+(\/[^?#\s]*)?$/i.test(u))
  const filesContentOrigin = (env.FILES_CONTENT_ORIGIN ?? '').trim().replace(/\/+$/, '')
  if (filesContentOrigin) {
    const u = new URL(filesContentOrigin)
    if (u.origin !== filesContentOrigin || u.username || u.password || !(u.protocol === 'https:' || (u.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(u.hostname)))) {
      throw new Error('FILES_CONTENT_ORIGIN must be a separate HTTPS origin (HTTP loopback is allowed for local checks).')
    }
  }
  return {
    installation,
    port,
    avatarColours: readAvatarColours(env.AVATAR_COLOURS ?? {}),
    avatarVariants: readFaceVariants(env.AVATAR_VARIANTS ?? {}),
    filesVaultUrls,
    filesWebUrls: readFileWebUrls(env.FILES_WEB_URLS ?? ''),
    filesContentOrigin,
    brokerUrl,
    brokerToken,
    geminiUrl,
    polishModel,
    logLevel,
    speech,
    appMedia: {
      dir: (env.APP_MEDIA_DIR ?? '').trim() || '/storage/app-media',
      chrome: (env.APP_PREVIEW_CHROME ?? '').trim() || 'chromium',
      ttlHours: positive(env.APP_PREVIEW_TTL_HOURS, 6),
      appsDomain: installation.appsDomain,
      previewBase: previewRoute.base,
      previewAddr: previewRoute.addr,
    },
    // What the app can say about itself without leaking the token.
    get brokerConfigured() {
      return brokerUrl !== ''
    },
  }
}

// A number setting that is not a positive number is its default: a typo in
// the environment makes the app behave as documented, not as zero.
function positive(value, fallback) {
  const n = Number(String(value ?? '').trim())
  return String(value ?? '').trim() !== '' && Number.isFinite(n) && n > 0 ? n : fallback
}

function positiveInt(value, fallback) {
  return Math.max(1, Math.floor(positive(value, fallback)))
}

// Said at startup, once, when the app can serve but cannot reach an agent.
// Not fatal: GET /up reports that *this* app can serve, not that the broker can.
export function configComplaints(config) {
  const out = []
  if (!config.brokerUrl) {
    out.push('BROKER_URL is unset: the page will load but no session can be started.')
  }
  if (!config.brokerToken) {
    out.push('BROKER_TOKEN is unset: the broker will answer 401 to everything but its health check.')
  }
  return out
}
