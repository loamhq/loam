// Quick actions on the Files home are a view of the host `quick-actions`
// plugin's switch and settings. There is no device-local override.
export function createQuickActions({ api, redraw }) {
  const st = { on: false, actions: 'both', inboxFolder: 'Inbox', dailyFolder: 'Daily', error: '' }
  let generation = 0
  async function refresh() {
    const ticket = ++generation
    try {
      const { plugin } = await api('GET', '/plugins/quick-actions')
      const settings = plugin?.on ? await api('GET', '/plugins/quick-actions/settings') : null
      if (ticket !== generation) return
      st.on = plugin?.on === true
      st.actions = settings?.values?.actions || 'both'
      st.inboxFolder = settings?.values?.inbox_folder || 'Inbox'
      st.dailyFolder = settings?.values?.daily_folder || 'Daily'
      st.error = ''
    } catch (e) {
      if (ticket === generation) {
        st.on = false
        st.error = [404, 405].includes(e.status) ? '' : e.message
      }
    } finally {
      if (ticket === generation) redraw()
    }
  }
  return { st, refresh }
}
