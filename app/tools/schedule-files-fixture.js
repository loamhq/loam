// Files responses paired with the supplied file-first schedule rows. No disk,
// scheduler or YAML parser: saves select the contract states. The development
// demo also accepts the same simple, flat headers it originally generated.
import { row, readAnswer, listAnswer, FOLDER_RO, USER } from '../test/fixtures/files-contract.js'
import { fileFirstSchedules, goodScheduleText, brokenScheduleText } from '../test/fixtures/schedules-file-first.js'

export function scheduleFilesFixture(fake) {
  const puts = [], texts = new Map(), revisions = new Map()
  let conflicts = 0
  const record = (path) => [...fake.rows.values()].find((r) => r.file?.path === path)
  const resource = (r) => row('spaces', r.file.path, { authority: 'schedule', capabilities: USER })
  const stored = (r) => texts.get(r.file.path) ?? (r.state === 'broken' ? brokenScheduleText : `---\nschedule: ${r.schedule}\nenabled: ${r.enabled}\n---\n${r.body}\n`)
  function handle(method, url, body) {
    if (!url.pathname.startsWith('/v1/files/')) return null
    const path = body?.path ?? url.searchParams.get('path') ?? ''
    const root = body?.root ?? url.searchParams.get('root')
    if (root !== 'spaces') return null
    const r = record(path)
    const ok = (body, status = 200) => ({ status, body: structuredClone(body) })
    const revision = () => revisions.get(path) ?? 'sha256:initial'
    if (method === 'GET' && url.pathname === '/v1/files/list') {
      const entries = [...fake.rows.values()].filter((r) => r.file.path.startsWith(`${path}/`)).map(resource)
      return ok(listAnswer(row(root, path, { kind: 'folder', authority: 'persona', capabilities: FOLDER_RO }), entries))
    }
    if (!r) return null
    if (method === 'GET' && url.pathname === '/v1/files/stat') return ok(resource(r))
    if (method === 'GET' && url.pathname === '/v1/files/read') return ok(readAnswer(resource(r), stored(r), revision()))
    if (method === 'PUT' && url.pathname === '/v1/files/text') {
      puts.push(structuredClone(body))
      if (body.base_revision !== revision() || conflicts) {
        conflicts = Math.max(0, conflicts - 1)
        return ok({ error: 'The file changed since that revision.', current_revision: revision() }, 409)
      }
      texts.set(path, body.text)
      revisions.set(path, `sha256:save-${puts.length}`)
      const broken = body.text === brokenScheduleText
      if (broken) {
        const example = fileFirstSchedules(r.agent).schedules[0]
        const { state, running, state_words, problem, enabled, schedule, words, error, last_edit } = example
        Object.assign(r, { state, running, state_words, problem, enabled, schedule, words, error, last_edit,
          changed_since_seen: false, unseen: [] })
      } else {
        const schedule = /^schedule: (.+)$/m.exec(body.text)?.[1] ?? /^at: (.+)$/m.exec(body.text)?.[1] ?? r.schedule
        const enabled = !/^enabled: false$/m.test(body.text)
        Object.assign(r, { state: enabled ? 'live' : 'off', running: enabled ? 'this' : 'none',
          state_words: enabled ? 'Live.' : 'Off: the file says it is not enabled.', problem: null,
          enabled, schedule, words: r.kind === 'one-off' ? `once, ${schedule}` : schedule, error: null, refused: null,
          changed_since_seen: false, unseen: [], last_edit: { id: `save-${puts.length}`, ts: new Date().toISOString(), actor: 'owner', by: 'owner', lane: 'app', taken: true } })
      }
      return ok({ ...readAnswer(resource(r), body.text, revision()), provenance: false, schedule: r })
    }
    return null
  }
  return { handle, puts, texts, conflict: () => { conflicts++ }, goodScheduleText, brokenScheduleText }
}
