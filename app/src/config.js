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
  // The address the server listens on. Loopback by default: the host's proxy
  // is the one public listener and reaches the app here. A container or a
  // remote development box sets 0.0.0.0 on purpose.
  const host = (env.APP_HOST ?? '').trim() || '127.0.0.1'
  const brokerUrl = (env.BROKER_URL ?? '').trim().replace(/\/+$/, '')
  const brokerToken = (env.BROKER_TOKEN ?? '').trim()
  // Credentials belong to the broker's plugin secret store. This URL lets
  // tests point the thread-branch drafter at a loopback provider.
  const geminiUrl = (env.GEMINI_API_URL ?? 'https://generativelanguage.googleapis.com').trim().replace(/\/+$/, '')
  // The text model that drafts a branched thread's opening (`src/thread-branch.js`).
  const branchModel = (env.BRANCH_MODEL ?? '').trim() || 'gemini-flash-latest'
  // How much the app says on stdout: `debug` adds a line per static file,
  // `info` (the default) is every other request,
  // `off` is silence. Anything else reads as `info`.
  const logLevel = (env.LOG_LEVEL ?? '').trim().toLowerCase() || 'info'
  // The biggest file a chat will carry is deliberately *not* here. It is the
  // host's number, the broker answers it on `GET /v1/limits`, and this app
  // relays that route rather than keeping a copy of it in an environment
  // variable that could disagree with the cap actually enforced.

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
    host,
    avatarColours: readAvatarColours(env.AVATAR_COLOURS ?? {}),
    avatarVariants: readFaceVariants(env.AVATAR_VARIANTS ?? {}),
    filesVaultUrls,
    filesWebUrls: readFileWebUrls(env.FILES_WEB_URLS ?? ''),
    filesContentOrigin,
    brokerUrl,
    brokerToken,
    geminiUrl,
    branchModel,
    logLevel,
    appMedia: {
      dir: (env.APP_MEDIA_DIR ?? '').trim() || '/storage/app-media',
      chrome: (env.APP_PREVIEW_CHROME ?? '').trim() || 'chromium',
      // The kernel's OOM score for the preview browser (-1000..1000), above
      // the server's own, so a page that balloons is what goes first. Empty
      // leaves the browser at the server's score.
      oomScoreAdj: oomScore(env.APP_PREVIEW_OOM_SCORE_ADJ),
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

// An OOM score adjustment, or null for anything that is not one.
function oomScore(value) {
  const text = String(value ?? '').trim()
  if (!/^-?\d+$/.test(text)) return null
  const n = Number(text)
  return n >= -1000 && n <= 1000 ? n : null
}
