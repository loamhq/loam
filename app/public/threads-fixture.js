import { installation } from './installation.js'
import { owner, setOwner } from './owner.js'
// Explicit preview only. Synthetic personas and vault paths; in-memory data is
// discarded on reload and never sent to the runtime or stored in the browser.
export function createThreadFixture({ modern = true } = {}) {
  if (!owner().id) setOwner({ id: 'preview-owner', name: 'You', accepts: ['preview-owner'] })
  const agents = [ { id: 'sprout', name: 'Sprout' }, { id: 'quill', name: 'Quill' }, { id: 'scout', name: 'Scout' }, { id: 'ledger', name: 'Ledger' }, { id: 'pip', name: 'Pip' } ]
  const providers = [ { id: 'claude-code', name: 'Claude Code', connected: true, models: [{ id: 'swift', name: 'Swift' }, { id: 'deep', name: 'Deep' }] }, { id: 'codex', name: 'Codex', connected: true }, { id: 'antigravity', name: 'Antigravity', connected: true, usage: null } ]
  const now = () => new Date().toISOString()
  let counter = 10
  const members = agents.map((a) => ({ persona: a.id, engine: 'claude-code', ...(modern ? { model: null, last_spoke_at: a.id === 'sprout' || a.id === 'quill' ? now() : null, busy: false } : {}) }))
  const thread = (id, title, persona, extra = {}) => ({ id, title, default: persona, members: persona ? [{ persona, engine: 'claude-code', ...(modern ? { model: null, last_spoke_at: null, busy: false } : {}) }] : [], last: { author: persona, text: 'A little progress, and a clear next step.', at: now() }, unread: 0, board_new: 0, pinned: false, ...(modern ? { icon: null, archived: false } : {}), board: id === 'general' ? installation().generalBoard : `${installation().boards}/${title} — board.md`, created: now(), ...extra })
  const general = thread('general', 'General', 'sprout', { pinned: true, members: structuredClone(members), board_new: 2, unread: 2 })
  const plan = thread('a-quieter-week', 'A little room for the week', 'sprout', { members: members.slice(0, 3), board_new: 2, unread: 2 })
  const records = new Map([general, plan, thread('small-details', 'The small details that make a difference', 'quill'), thread('weekend', 'Somewhere new for the weekend', 'scout')].map((t) => [t.id, { thread: t,
    has_more: false, turn: null, messages: t.id === 'general' ? [
      { id: 'm1', author: 'sprout', at: now(), kind: 'bulletin', text: 'Good morning. The week’s notes are gathered, and there are two small things on the board when you have a moment.', did: [] },
      { id: 'm2', author: 'quill', at: now(), kind: 'bulletin', text: 'The first draft is ready to read. I left a link and the one decision it needs on our board.', did: [] },
    ] : [
      { id: 'm1', author: owner().id, at: now(), kind: 'message', text: 'Let’s make a little room for the week. @quill could you help with the words?', did: [] },
      { id: 'm2', kind: 'system', text: 'You brought in Quill', at: now() },
      { id: 'm3', author: t.default, at: now(), kind: 'message', text: 'A calmer week starts with a short list. I’ve gathered the loose ends and put the things worth keeping on the board.\n\n**One useful next step:** choose the one thing you want to give your best attention to. Everything else can wait its turn.', did: [{ label: 'Read the week’s notes and gathered the open questions' }, { label: 'Said: Let’s leave a little room for the unexpected.' }, { label: '↳ Gathered the open questions' }, { label: 'Grouped the loose ends into a short, workable list' }, { label: 'Added two reminders to this thread’s board' }] },
      { id: 'm4', author: 'quill', at: now(), kind: 'message', text: 'I’m here. Let’s keep the plan light enough to use. A little clarity goes a long way.', did: [{ label: 'Reviewed the plan and simplified the next steps' }] },
    ], board: { path: t.id === 'general' ? installation().generalBoard : `${installation().boards}/${t.title} — board.md`, markdown: t.id === 'general' ? '# Inbox\n\n## Worth a moment\n\n- [ ] Read the first draft <!-- Quill · new -->\n- [ ] Choose a day for the weekly review <!-- Sprout · new -->\n\n## Keeping close\n\n- A little space for the unexpected.\n' : '# A little room for the week\n\n## Next, gently\n\n- [ ] Pick the week’s one important thing <!-- Sprout · new -->\n- [ ] Read the short first draft <!-- Quill · new -->\n\n## Keep in mind\n\n- Leave Friday afternoon open.\n- [x] Gather the loose ends\n' } }]))
  const listeners = new Map(), listListeners = new Set()
  const emitRow = (id) => { const row = records.get(id)?.thread; if (row) for (const fn of listListeners) fn({ type: 'thread.changed', thread: id, summary: structuredClone(row) }) }
  const emit = (id, e) => { for (const fn of listeners.get(id) ?? []) fn(e); if (modern) emitRow(id) }
  const copy = (o) => structuredClone(o)
  const api = async (method, path, body) => {
    await new Promise((r) => setTimeout(r, 140))
    if (path.split('?')[0] === '/threads') {
      if (method === 'GET') return { threads: [...records.values()].map((r) => copy(r.thread)).filter((t) => !t.archived || path.includes('archived=1')), agents: copy(agents) }
      const t = thread(`new-${++counter}`, 'New thread', body.persona ?? null, { last: null })
      records.set(t.id, { thread: t, messages: [], has_more: false, turn: null, board: { path: `${installation().boards}/New thread — board.md`, markdown: '' } })
      emitRow(t.id); return copy(t)
    }
    // Sample replies keep no full steps; a 404 is how a runtime says so, and the page shows each reply's `did` instead.
    if (/^\/threads\/[^/?]+\/steps\b/.test(path)) throw Object.assign(new Error('Sample threads keep no steps.'), { status: 404 })
    const match = /^\/threads\/([^/?]+)(?:\/(messages|board|attachments))?/.exec(path)
    if (!match) return { accepted: true }
    const id = decodeURIComponent(match[1]), action = match[2], r = records.get(id)
    if (!r) throw new Error('This sample thread does not exist.')
    if (method === 'DELETE') {
      if (r.turn) throw Object.assign(new Error('a turn is running in this thread; delete it when the turn has finished'), { status: 409 })
      records.delete(id); for (const fn of listListeners) fn({ type: 'thread.removed', thread: id }); return { deleted: true }
    }
    if (action === 'attachments') return { attachments: [{ path: `${installation().attachments}/preview/${body.name}`, name: body.name, size: body.size, type: body.type }] }
    r.board.revision ??= 'fixture-0'
    if (action === 'board') {
      if (method === 'GET') return { ...copy(r.board), board_new: r.thread.board_new }
      if (body.base_revision != null && body.base_revision !== r.board.revision) throw Object.assign(new Error('The board changed'), { status: 409 })
      r.board.revision = 'fixture-' + ++counter
      r.board.markdown = body.markdown
      r.thread.board_new = (body.markdown.match(/· new/g) ?? []).length
      emit(id, { type: 'board.changed', board_new: r.thread.board_new }); return { ...copy(r.board), board_new: r.thread.board_new }
    }
    if (action === 'messages') {
      if (body.reply_to && !r.messages.some((m) => m.id === body.reply_to)) throw Object.assign(new Error('This earlier message is no longer available.'), { status: 404 })
      const message = { id: `m${++counter}`, author: owner().id, at: now(), kind: 'message', text: body.text, did: [], ...(body.reply_to ? { reply_to: body.reply_to } : {}), ...(body.attachments?.length ? { attachments: body.attachments.map((path) => ({ path, name: path.split('/').at(-1) })) } : {}) }
      r.messages.push(message); emit(id, { type: 'message.done', message: copy(message) })
      const tagged = agents.filter((a) => body.text.toLowerCase().includes(`@${a.id}`))
      const targets = tagged.length ? tagged.map((a) => a.id) : [r.thread.default].filter(Boolean)
      for (const persona of targets) {
        if (!r.thread.members.some((m) => m.persona === persona)) {
          r.thread.members.push({ persona, engine: 'claude-code', ...(modern ? { model: null, last_spoke_at: null, busy: false } : {}) })
          const joined = { id: `m${++counter}`, author: 'system', kind: 'system', text: `You brought in ${agents.find((a) => a.id === persona).name}` }
          r.messages.push(joined); emit(id, { type: 'member.joined', persona, engine: 'claude-code', by: owner().id }); emit(id, { type: 'message.done', message: copy(joined) })
        }
        const replyId = `m${++counter}`
        r.turn = { persona, engine: 'claude-code' }; emit(id, { type: 'turn.started', ...r.turn })
        setTimeout(() => emit(id, { type: 'turn.step', persona, label: 'Read the thread and gathered the open questions', steps: 1 }), 200)
        const text = 'Let’s give that a little room. I’ll keep the next step small, and put anything worth keeping on our board.'
        const words = text.match(/\S+\s*/g)
        words.forEach((delta, i) => setTimeout(() => {
          emit(id, { type: 'message.delta', persona, text: delta, draft: words.slice(0, i + 1).join('') })
          if (i === words.length - 1) {
            const reply = { id: replyId, author: persona, at: now(), kind: 'message', text, did: [{ label: 'Read the thread and considered the next step' }] }
            r.messages.push(reply); r.thread.last = { author: persona, text, at: now() }
            r.turn = null; emit(id, { type: 'message.done', message: copy(reply) })
            emit(id, { type: 'thread.changed', summary: copy(r.thread) }); emit(id, { type: 'turn.finished', persona })
          }
        }, 600 + i * 45))
      }
      if (r.thread.title === 'New thread') r.thread.title = body.text.slice(0, 60)
      return { accepted: true, message: copy(message) }
    }
    if (method === 'PATCH') {
      if (body.seen) r.thread.unread = 0
      if (Object.hasOwn(body, 'default')) r.thread.default = body.default
      for (const field of ['icon', 'pinned', 'archived']) if (Object.hasOwn(body, field)) r.thread[field] = body[field]
      for (const [persona, model] of Object.entries(body.model ?? {})) { const member = r.thread.members.find((m) => m.persona === persona); if (member) member.model = model }
      if (body.title) r.thread.title = body.title
      for (const persona of body.add ?? []) if (!r.thread.members.some((m) => m.persona === persona)) { r.thread.members.push({ persona, engine: 'claude-code', ...(modern ? { model: null, last_spoke_at: null, busy: false } : {}) }); emit(id, { type: 'member.joined', persona, engine: 'claude-code' }) }
      if (body.remove) r.thread.members = r.thread.members.filter((m) => !body.remove.includes(m.persona))
      for (const [persona, engine] of Object.entries(body.engine ?? body.engines ?? {})) { const member = r.thread.members.find((m) => m.persona === persona); if (member) { member.engine = engine; member.model = null } }
      emit(id, { type: 'thread.changed', summary: copy(r.thread) })
      return copy(r.thread)
    }
    const params = new URL(path, 'http://fixture').searchParams
    const before = params.get('before'), limit = Number(params.get('limit') ?? 50)
    const end = before ? r.messages.findIndex((m) => m.id === before) : r.messages.length
    if (!before) r.thread.unread = 0
    return { ...copy(r), messages: copy(r.messages.slice(Math.max(0, end - limit), end)), has_more: end > limit }
  }
  return { api, agents, providers, subscribeList: modern ? (fn) => {
    fn({ type: 'threads.snapshot', threads: [...records.values()].map((r) => copy(r.thread)), agents: copy(agents) }); listListeners.add(fn); return () => listListeners.delete(fn)
  } : null, subscribe: (id, fn, after = null) => {
    const r = records.get(id)
    fn({ type: 'thread.snapshot', thread: id, turn: copy(r?.turn ?? null) })
    if (after) { const at = r.messages.findIndex((m) => m.id === after); for (const message of r.messages.slice(at + 1)) fn({ type: 'message.done', message: copy(message) }) }
    if (!listeners.has(id)) listeners.set(id, new Set())
    listeners.get(id).add(fn)
    return () => listeners.get(id).delete(fn)
  } }
}
