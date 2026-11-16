// Stable destinations. Legacy settings links resolve to their current home.
export const SETTINGS_SECTIONS = [
  { id: 'appearance', label: 'Appearance', icon: 'sun' },
  { id: 'agents', label: 'Agents', icon: 'users' },
  { id: 'providers', label: 'Providers', icon: 'code' },
  { id: 'integrations', label: 'Integrations', icon: 'link' },
  { id: 'voice', label: 'Voice', icon: 'mic' },
  { id: 'files', label: 'Files', icon: 'folder' },
  { id: 'plugins', label: 'Plugins', icon: 'puzzle' },
  { id: 'signin', label: 'Sign-in', icon: 'lock' },
  { id: 'update', label: 'Update', icon: 'refresh' },
]
export function settingsHome(page) {
  return ({ accounts: 'integrations', dictation: 'voice', 'read-aloud': 'voice' })[page] ?? page
}
