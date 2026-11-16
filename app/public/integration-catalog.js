import { orgApps } from './integrations.js'

// One adapter per working service. Account identifiers are presentation keys;
// mutations keep using the service's existing, explicitly scoped routes.
export function integrationCatalog({ github }) {
  const gh = github.data
  const githubAccounts = Array.isArray(github.accounts) ? github.accounts.map(record => ({ id: record.account, name: record.account, record,
    org: record.kind === 'org' ? record.account : null, scope: record.installation ? record.installation.repository_selection === 'all' ? 'all repositories' : 'selected repositories' : null })) : [
    ...(gh?.installed || gh?.user ? [{ id: 'personal', name: gh.installed?.account || gh.user?.login, record: gh, org: null }] : []),
    ...orgApps(gh).map(record => ({ id: record.org, name: record.installed?.account || record.org, record, org: record.org })),
  ].map(account => ({ ...account, scope: !account.record.installed ? null : account.record.installed.repository_selection === 'all' ? 'all repositories' : 'selected repositories' }))
  const services = [
    { id: 'github', name: 'GitHub', state: github, accounts: githubAccounts },
  ]
  return services.filter(service => !service.state.missing).map(service => {
    const connected = Array.isArray(github.accounts) ? service.accounts.length > 0 : service.accounts.some(a => ['authorized', 'unhealthy'].includes(a.record.status))
    const count = connected ? service.accounts.length : 0
    return { ...service, connected, summary: service.state.error && !service.state.data ? 'Connection unavailable'
      : !service.state.loaded && !service.state.data ? 'Loading…'
        : count ? `${count} ${count === 1 ? 'account' : 'accounts'}` : 'Not connected' }
  })
}
