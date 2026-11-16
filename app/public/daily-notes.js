// The daily-note action is a view of the host plugin's switch and folder.
// There is no installation default or device override.
export function createDailyNotes({ api, redraw }) {
  const st = { on: false, folder: null, error: '' }
  let generation = 0
  async function refresh() {
    const ticket = ++generation
    try {
      const { plugin } = await api('GET', '/plugins/daily-notes')
      const settings = plugin?.on ? await api('GET', '/plugins/daily-notes/settings') : null
      if (ticket !== generation) return
      st.on = plugin?.on === true; st.folder = settings?.values?.folder ?? null; st.error = ''
    } catch (e) { if (ticket === generation) { st.on = false; st.folder = null; st.error = [404, 405].includes(e.status) ? '' : e.message } }
    finally { if (ticket === generation) redraw() }
  }
  return { st, refresh }
}
