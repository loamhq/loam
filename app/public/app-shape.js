// Stable destinations. Legacy settings links resolve to their current home.
export const SETTINGS_SECTIONS = [
  { id: 'appearance', label: 'Appearance', icon: 'sun' },
  { id: 'agents', label: 'Agents', icon: 'users' },
  { id: 'providers', label: 'Providers', icon: 'code' },
  { id: 'integrations', label: 'Integrations', icon: 'link' },
  { id: 'accounts', label: 'Accounts', icon: 'key' },
  { id: 'files', label: 'Files', icon: 'folder' },
  { id: 'plugins', label: 'Plugins', icon: 'puzzle' },
  { id: 'signin', label: 'Sign-in', icon: 'lock' },
  { id: 'update', label: 'Update', icon: 'refresh' },
]
export function settingsHome(page) {
  return ({ voice: 'plugins', dictation: 'plugins', 'read-aloud': 'plugins' })[page] ?? page
}
