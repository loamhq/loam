// GitHub's state, in the broker's own shapes — quoted from the runtime's
// `tests/test_github_app.py --shapes`. Shared by the tests that draw the card.

const USES = ['provisioning', 'engineer pushes']
export const APP = { id: 424242, slug: 'loam', url: 'https://github.com/apps/loam', registered_at: '2026-09-30T21:37:31Z' }
export const INSTALLED = { installation_id: 99, account: 'octo-owner', repository_selection: 'all', installed_at: '2026-09-30T21:37:31Z' }
export const USER = {
  login: 'octo-owner',
  token_expires_at: '2026-10-01T05:37:31Z',
  refresh_expires_at: '2027-04-02T21:37:31Z',
  authorized_at: '2026-09-30T21:37:31Z',
}
export const SHAPES = {
  unregistered: { status: 'unregistered', registered: false, app: null, installed: null, user: null, health: { last_ok_at: null, last_error: null }, uses: USES },
  registered: { status: 'registered', registered: true, app: APP, installed: null, user: null, health: { last_ok_at: null, last_error: null }, uses: USES },
  installed: { status: 'installed', registered: true, app: APP, installed: INSTALLED, user: null, health: { last_ok_at: '2026-09-30T21:37:31Z', last_error: null }, uses: USES },
  authorized: { status: 'authorized', registered: true, app: APP, installed: INSTALLED, user: USER, health: { last_ok_at: '2026-09-30T21:37:31Z', last_error: null }, uses: USES },
  unhealthy: {
    status: 'unhealthy',
    registered: true,
    app: APP,
    installed: INSTALLED,
    user: USER,
    health: { last_ok_at: '2026-09-30T21:37:31Z', last_error: 'GitHub says the Loam app’s installation is gone; install it again from the app' },
    uses: USES,
  },
}


// An app owned by an organization (2026-10-06), in the broker's own shape —
// `_org_view()` in the runtime's `lib/loam_github_app.py` at `1d1bbee`, the
// code the broker runs after the restart that brings it. `SHAPES` above are a
// broker from before: no `org_apps` key at all. `WITH_ORGS` is one from after,
// whose answer also carries `installations` and always carries `org_apps`.
export const ORG_APP = { id: 515151, slug: 'loam-for-octo-org', url: 'https://github.com/apps/loam-for-octo-org', registered_at: '2026-10-06T20:00:00Z' }
export const ORG_INSTALLED = { installation_id: 888, account: 'octo-org', repository_selection: 'selected', installed_at: '2026-10-06T20:05:00Z' }
export const ORG_USER = { ...USER, authorized_at: '2026-10-06T20:05:00Z' }
const NO_HEALTH = { last_ok_at: null, last_error: null }
const OK = { last_ok_at: '2026-10-06T20:05:00Z', last_error: null }
export const ORG_SHAPES = {
  registered: { org: 'octo-org', status: 'registered', app: ORG_APP, installed: null, user: null, health: NO_HEALTH },
  installed: { org: 'octo-org', status: 'installed', app: ORG_APP, installed: ORG_INSTALLED, user: null, health: OK },
  authorized: { org: 'octo-org', status: 'authorized', app: ORG_APP, installed: ORG_INSTALLED, user: ORG_USER, health: OK },
  unhealthy: {
    org: 'octo-org',
    status: 'unhealthy',
    app: ORG_APP,
    installed: ORG_INSTALLED,
    user: ORG_USER,
    health: { last_ok_at: '2026-10-06T20:05:00Z', last_error: 'GitHub says octo-org’s app’s installation is gone; install it again from the app' },
  },
}
export const withOrgs = (shape, ...orgs) => ({
  ...shape,
  installations: shape.installed ? [shape.installed] : [],
  org_apps: orgs,
})
