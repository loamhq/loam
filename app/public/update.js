import { h } from './ui.js'

export const updateInProgress = run => ['starting', 'running'].includes(run?.outcome)
const outcomes = { starting: 'Getting ready…', running: 'Updating Loam…', applied: 'Update finished', 'rolled-back': 'Previous version restored', refused: 'Nothing changed', failed: 'The update couldn’t finish', interrupted: 'The update was interrupted' }
const changeKeys = check => ['added', 'changed', 'removed'].filter(key => check?.changes?.[key]?.length)

export function createUpdate({ api, changed, reload = () => location.reload(), later = setTimeout, cancel = clearTimeout }) {
  const st = { data: null, busy: false, operation: null, error: '', refusal: null, review: null, reconnecting: false, confirmRollback: false }
  let timer = null, disposed = false, reloaded = false
  function stop() { disposed = true; cancel(timer); timer = null }
  function follow() {
    cancel(timer)
    if (!disposed && updateInProgress(st.data?.run)) timer = later(poll, 2000)
  }
  function settle(run) {
    st.data = { ...st.data, run, running: updateInProgress(run) }
    if (run.outcome === 'refused') { st.refusal = run.refusal; st.review = run.refusal?.code === 'library-edits' ? run.refusal.library : null }
    if (run.outcome === 'applied' && !reloaded) { reloaded = true; changed(); reload(); return }
    if (!updateInProgress(run)) refresh().catch(() => {})
    follow()
  }
  async function poll() {
    if (disposed) return
    try {
      const run = await api('GET', '/update/progress')
      if (disposed) return
      st.reconnecting = false; st.error = ''; settle(run)
    } catch {
      if (disposed) return
      // The broker and then the web half can restart during an update. Its
      // durable progress is authoritative; a dropped connection is not failure.
      st.reconnecting = true; follow()
    }
    changed()
  }
  async function refresh() {
    const data = await api('GET', '/update')
    if (disposed) return
    st.data = data; follow(); changed()
  }
  async function check() {
    if (st.busy || updateInProgress(st.data?.run)) return
    disposed = false; st.operation = 'check'; st.busy = true; st.error = ''; st.refusal = null; st.review = null; changed()
    try {
      await refresh()
      if (!disposed && !updateInProgress(st.data?.run)) st.data = await api('POST', '/update/check', {})
      if (!disposed) follow()
    } catch (e) {
      if (!disposed) st.error = [404, 405].includes(e.status) ? 'Updates will be available when Loam’s update service is ready.' : e.message
    } finally { st.busy = false; changed() }
  }
  async function begin(action, accept = false) {
    if (st.busy || updateInProgress(st.data?.run)) return
    st.operation = action; st.busy = true; st.error = ''; st.refusal = null; st.confirmRollback = false; changed()
    try {
      const body = action === 'apply' ? { ...(st.data?.check?.latest?.version ? { to: st.data.check.latest.version } : {}), ...(accept ? { accept_library_edits: true } : {}) } : {}
      const answer = await api('POST', `/update/${action}`, body)
      if (disposed) return
      st.review = null; settle(answer.run)
    } catch (e) {
      st.error = e.message; st.refusal = e.body?.refusal ?? null
      st.review = st.refusal?.code === 'library-edits' ? st.refusal.library : null
    } finally { st.busy = false; changed() }
  }
  const button = (text, onclick, primary = false, disabled = false) => h('button', { class: primary ? 'primary' : 'pillbtn', type: 'button', text, disabled, onclick })
  function view() {
    const data = st.data, check = data?.check, run = data?.run, active = updateInProgress(run), locked = active || st.busy
    const library = st.review ?? (check?.library?.any ? check.library : null)
    const noVersion = check?.error?.code === 'no-version' || (data && !data.current?.version)
    const development = data?.current?.kind === 'development'
    const localCommits = data?.current?.local_commits
    const localDetails = [Number.isInteger(localCommits) ? `${localCommits} local commit${localCommits === 1 ? '' : 's'}` : null, data?.current?.branch || (!development || data?.current?.version ? data?.current?.kind : null)].filter(Boolean).join(' · ')
    const refusal = st.refusal ?? (run?.outcome === 'refused' ? run.refusal : null) ?? check?.blocked
    const problem = st.error || refusal?.message || (check?.error?.code !== 'no-version' ? check?.error?.message : '')
    return h('div', { class: 'update-control' },
      h('section', { class: 'card update-version' }, h('p', { class: 'kicker', text: 'Your Loam' }),
        h('div', { class: 'display update-number', text: data?.current?.version ? `Version ${data.current.version}` : development ? 'Development build' : data ? 'Growing towards our first release' : st.busy ? 'Checking your version…' : 'Version unavailable' }),
        h('p', { class: 'note', text: noVersion ? 'No numbered release is installed yet. You can keep using Loam as usual.' : 'Updates happen only when you choose. Nothing installs automatically.' }),
        data?.current?.kind && data.current.kind !== 'stock' && localDetails ? h('p', { class: 'note', text: localDetails }) : null,
        button(st.busy && st.operation === 'check' ? 'Checking…' : 'Check for updates', checkUpdate, false, locked)),
      st.busy && st.operation !== 'check' ? h('p', { role: 'status', text: st.operation === 'rollback' ? 'Starting the rollback…' : 'Starting the update…' }) : null,
      check?.latest && !active ? h('section', { class: 'card update-release' },
        h('h3', { text: check.available ? `Loam ${check.latest.version} is ready` : 'You’re up to date' }),
        // A release that changes nothing in System/Loam drew the heading and
        // "Files included in this release:" over an empty list (the first-day
        // walk, 2026-11-16). The release notes go to the person's agent.
        check.available ? [h('p', { class: 'note', text: 'After the update, your agent will tell you what’s new.' }),
          changeKeys(check).length ? [h('h4', { class: 'kicker', text: 'Files in System/Loam this release replaces' }),
            ...changeKeys(check).map(key => h('div', {}, h('p', { class: 'kicker', text: key }), h('ul', {}, check.changes[key].map(file => h('li', { text: file })))))] : null,
          library && !st.review ? libraryView(library) : null,
          !st.review ? button('Update Loam', () => { if (library) { st.review = library; changed() } else runAction() }, true, locked || Boolean(check.blocked && check.blocked.code !== 'library-edits')) : null] : null) : null,
      st.review ? h('section', { class: 'card update-review', 'aria-label': 'Review library changes' },
        h('h3', { text: 'Keep a copy, then update?' }),
        h('p', { text: 'You’ve changed files in System/Loam. Updating replaces them with the release’s copies. Your versions are backed up so your agent can help recover anything you want to keep.' }),
        libraryView(st.review),
        h('div', { class: 'update-actions' }, button('Cancel', () => { st.review = null; changed() }, false, locked), button('Back up and update', () => begin('apply', true), true, locked))) : null,
      problem ? h('div', { class: 'card update-problem', role: 'status' }, h('p', { text: problem }), refusal ? h('p', { class: 'note', text: 'Your agent can help you with this.' }) : null,
        refusal?.files?.length ? h('ul', {}, refusal.files.map(file => h('li', { text: file }))) : null) : null,
      run ? h('section', { class: 'card update-progress', 'aria-label': 'Update progress' },
        h('h3', { role: 'status', text: outcomes[run.outcome] ?? run.outcome }),
        run.to_version ? h('p', { class: 'note', text: `${run.from_version || 'Earlier version'} → ${run.to_version}` }) : null,
        h('ol', { class: 'update-steps' }, (run.steps ?? []).map(step => h('li', { class: `update-step ${step.status}` }, h('span', { class: 'update-step-state', text: step.status === 'done' ? '✓' : step.status === 'running' ? '…' : '·', 'aria-hidden': 'true' }),
          h('div', {}, h('div', { text: step.words }), h('span', { class: 'note', text: step.status }), step.detail ? h('p', { class: 'note', text: step.detail }) : null)))),
        st.reconnecting ? h('p', { class: 'note', role: 'status', text: 'Loam is restarting. We’ll pick up progress as soon as it returns.' }) : null,
        run.message ? h('p', { text: run.message }) : null,
        run.outcome === 'failed' ? h('p', { class: 'note', text: run.rolled_back ? 'Your previous version was restored. Your agent can help with the failed step.' : 'Your agent can help you finish or restore the previous version.' }) : null,
        run.outcome === 'interrupted' ? h('p', { class: 'note', text: 'You can restore the previous version below.' }) : null) : null,
      data?.rollback && !active ? h('section', { class: 'card update-rollback' }, h('h3', { text: 'Go back a version' }),
        h('p', { class: 'note', text: `Restore Loam ${data.rollback.to_version || 'to its previous version'}.` }),
        st.confirmRollback ? h('div', {}, h('p', { text: 'Restore the previous release now?' }), h('div', { class: 'update-actions' }, button('Cancel', () => { st.confirmRollback = false; changed() }, false, locked), button('Restore previous version', () => runAction('rollback'), true, locked)))
          : button('Roll back…', () => { st.confirmRollback = true; changed() }, false, locked)) : null)
  }
  const checkUpdate = () => check()
  const runAction = (action = 'apply') => begin(action)
  return { st, check, run: begin, poll, stop, view }
}
function libraryView(library) {
  return h('details', { class: 'update-library', open: true }, h('summary', { text: 'Your changes in System/Loam' }),
    ...['edited', 'added', 'deleted'].filter(key => library[key]?.length).map(key => h('p', { class: 'note', text: `${key}: ${library[key].join(', ')}` })),
    h('pre', { tabindex: '0', 'aria-label': 'Library changes', text: library.diff || 'No text diff is available for these files.' }))
}
