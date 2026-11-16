import { formatDate } from '/time.js'
const DAY = 86400000
const dateWords = iso => formatDate(iso) || null
// ------------------------------------------------------------------ Acme
//
// The second integration: device-code sign-in first, with a personal access
// token as an alternative. The host keeps the credential and hands it to the
// agents' Acme servers. This page reads only the connection state.
//
// **Everything here is read off `GET /x/acme/`**, whose
// `status` is one of `unconfigured`, `connected` and `unhealthy`. `expires_at`
// may be `null`: Acme does not always put an expiry on a personal token.

// A token only they can replace. The notice starts two weeks out, as GitHub's.
export const ACME_WARN_DAYS = 14

// The one line of what Loam uses it for.
export const ACME_USES = 'The agents read and write Acme as you.'

// Where their tokens live in Acme: where one is made, and where one is
// revoked — the page the plugin names on a disconnect.
export const ACME_TOKENS_URL = 'https://app.acme.example/my/access_tokens'

// The card, as a list of what to draw. `null` for no reading at all.
//
// - `lines`: one — "Not connected", or "Connected as ‹user› on ‹account›" with
//   the account as `{ text, href }` to link.
// - `quiet`: how the connection renews, or the host's health error.
// - `actions`: `signin`, `refresh`, `disconnect`, in order. Sign in is also
//   offered for an unhealthy connection or a token near its expiry.
export function acmeView(bc, { now = Date.now() } = {}) {
  if (!bc || typeof bc !== 'object') return null
  const status = typeof bc.status === 'string' ? bc.status : 'unconfigured'
  const connected = status === 'connected' || status === 'unhealthy'
  const account = bc.account && typeof bc.account === 'object' ? bc.account : null
  const user = bc.user && typeof bc.user === 'object' ? bc.user : null
  const error = typeof bc.health?.last_error === 'string' && bc.health.last_error !== '' ? bc.health.last_error : null

  const line = connected
    ? {
        key: 'who',
        text: `Connected as ${user?.name ?? 'someone'} on `,
        account: { text: account?.name ?? 'an account', href: typeof account?.url === 'string' ? account.url : null },
      }
    : { key: 'who', text: 'Not connected' }

  const until = dateWords(bc.expires_at)
  const soon = expiresSoon(bc, now)
  const quiet = error
    ? { tone: 'warn', text: error }
    : !connected
      ? null
      : bc.source === 'login'
        ? { tone: 'quiet', text: 'signed in · renews itself' }
        : until
          ? { tone: soon ? 'warn' : 'quiet', text: `token good until ${until}` }
          : { tone: 'quiet', text: 'no expiry on this token' }

  const actions = []
  if (!connected || status === 'unhealthy' || soon) actions.push('signin')
  if (connected) actions.push('refresh', 'disconnect')

  return { status, connected, lines: [line], quiet, uses: ACME_USES, actions }
}

// Whether Acme needs a person: the host's `unhealthy`, or a token within
// two weeks of its expiry (or past it). The words are the notice row's.
export function acmeNotice(bc, { now = Date.now() } = {}) {
  if (!bc || typeof bc !== 'object') return null
  if (bc.status === 'unhealthy' || (bc.status === 'connected' && expiresSoon(bc, now))) return { text: bc.source === 'login' ? 'Acme needs a new sign-in' : 'Acme needs a new token' }
  return null
}

// The accounts a several-accounts refusal names, to choose from — or `null`
// when the refusal is any other.
export function acmeAccounts(body) {
  if (!Array.isArray(body?.accounts)) return null
  const accounts = body.accounts.filter((a) => a && typeof a === 'object' && (typeof a.id === 'number' || typeof a.id === 'string'))
  return accounts.length ? accounts : null
}

function expiresSoon(bc, now) {
  const t = Date.parse(bc?.expires_at ?? '')
  if (Number.isNaN(t)) return false
  return t - now <= ACME_WARN_DAYS * DAY
}
