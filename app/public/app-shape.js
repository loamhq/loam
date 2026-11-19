// Stable destinations. Legacy settings links resolve to their current home.
// Seven since 2026-11-18 (the owner: "love it", to this structure). The word
// for API keys is "API keys", never "Accounts": that word is kept for the
// Loam account, which will live under Account.
export const SETTINGS_SECTIONS = [
  { id: 'account', label: 'Account', icon: 'person' },
  { id: 'appearance', label: 'Appearance', icon: 'sun' },
  { id: 'agents', label: 'Agents', icon: 'users' },
  { id: 'connections', label: 'Connections', icon: 'link' },
  { id: 'plugins', label: 'Plugins', icon: 'puzzle' },
  { id: 'files', label: 'Files', icon: 'folder' },
  { id: 'about', label: 'About Loam', icon: 'info' },
]
// Every id a link, a plugin or an agent may still hold, and where it lives
// now. `accounts` with a provider is that provider's API key page.
const MOVED = { voice: 'plugins', dictation: 'plugins', 'read-aloud': 'plugins',
  providers: 'connections', integrations: 'connections', accounts: 'connections', keys: 'connections', 'api-keys': 'connections',
  signin: 'account', 'sign-in': 'account', update: 'about' }
// The group of Connections an old id named, so the page can open there.
export const CONNECTIONS_GROUP = { providers: 'providers', integrations: 'services', accounts: 'api-keys', keys: 'api-keys', 'api-keys': 'api-keys' }
export function settingsHome(page, provider = null) {
  if (provider && ['accounts', 'keys', 'api-keys'].includes(page)) return 'api-key'
  return MOVED[page] ?? page
}
// The section a page belongs to, for the nav's highlight.
export function settingsSection(page) {
  return ({ provider: 'connections', integration: 'connections', 'api-key': 'connections' })[page] ?? settingsHome(page)
}
