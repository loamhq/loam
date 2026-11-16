// Acme's state, in the broker's own shapes — quoted from the runtime's
// `tests/test_acme.py --shapes`. Shared by the tests that draw the card.

const USES = ['the personas, as Sam']
export const ACCOUNT = { id: 5550002, name: 'Account 5550002', url: 'https://app.acme.example/5550002' }
export const USER = { id: 4242, name: 'Sam H' }
export const REFUSED = 'Acme refused the token (revoked, expired or mistyped); make a new one and paste it on the Integrations screen'
export const SEVERAL = {
  error: 'the token reaches more than one Acme account; choose one and send its id as account_id',
  accounts: [
    { id: 5550001, name: 'Account 5550001', url: 'https://app.acme.example/5550001' },
    { id: 5550002, name: 'Account 5550002', url: 'https://app.acme.example/5550002' },
  ],
}
export const REVOKE_URL = 'https://app.acme.example/my/access_tokens'

const unconfigured = {
  status: 'unconfigured',
  source: null,
  login: null,
  account: null,
  user: null,
  connected_at: null,
  expires_at: null,
  health: { last_ok_at: null, last_error: null },
  uses: USES,
}
const connected = {
  status: 'connected',
  source: 'token',
  login: null,
  account: ACCOUNT,
  user: USER,
  connected_at: '2026-10-07T18:47:00Z',
  expires_at: '2027-10-07T00:00:00.000Z',
  health: { last_ok_at: '2026-10-07T18:47:00Z', last_error: null },
  uses: USES,
}
export const SHAPES = {
  unconfigured,
  connected,
  noExpiry: { ...connected, expires_at: null },
  unhealthy: { ...connected, status: 'unhealthy', health: { last_ok_at: '2026-10-07T18:47:00Z', last_error: REFUSED } },
}

// A connected token `days` from its expiry, as of `now`.
export const expiringIn = (days, now = Date.now()) => ({ ...connected, expires_at: new Date(now + days * 86400000).toISOString() })

// Device-code sign-in contract, supplied with the integration's login routes.
export const PENDING = {
  pending: true,
  verification_url: 'https://login.acme.example/authorization/device',
  user_code: 'WDJB-MJHT',
  expires_at: '2026-10-07T21:56:50Z',
}
export const LOGIN_SEVERAL = { ...SEVERAL, error: 'the sign-in reaches more than one Acme account; choose one and send its id as account_id' }
export const LOGIN_REFUSED = "Acme signed the personas' login out; sign in again on the Integrations screen"
export const EXPIRED = 'the Acme sign-in did not finish (device code expired before authorization completed); sign in again on the Integrations screen'
export const DENIED = 'the Acme sign-in did not finish (the authorization request was denied); sign in again on the Integrations screen'
Object.assign(SHAPES, {
  pending: { ...unconfigured, login: PENDING },
  login: { ...connected, source: 'login', expires_at: null },
  loginUnhealthy: { ...connected, source: 'login', expires_at: null, status: 'unhealthy', health: { last_ok_at: connected.health.last_ok_at, last_error: LOGIN_REFUSED } },
  expired: { ...unconfigured, health: { last_ok_at: null, last_error: EXPIRED } },
  denied: { ...unconfigured, health: { last_ok_at: null, last_error: DENIED } },
  accounts: { ...unconfigured, login: { pending: false, verification_url: null, user_code: null, expires_at: null, accounts: SEVERAL.accounts } },
})
