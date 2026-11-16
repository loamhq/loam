// Adapt the full reply record to the same transcript and step UI as Classic.
// The record owns order and parentage; did labels are only an older-record view.
import { createTranscript, apply, foldsAsStep, blockView } from './transcript.js'

const views = new WeakMap()
export function fullStepSegments(steps, recordedModel = null) {
  if (views.has(steps)) return views.get(steps)
  const transcript = createTranscript()
  let seq = 0, model = recordedModel
  // Put closed cards where they were asked, then settle their answers in place,
  // exactly as a replay after Classic's session snapshot does.
  apply(transcript, { type: 'broker.session', pending_permissions: [], record_seq: steps.length * 3 + 1 })
  steps.forEach((step, index) => {
    if (!step || typeof step !== 'object') return
    const parent_tool_use_id = step.parent ?? step.event?.parent_tool_use_id ?? null
    const send = (event) => apply(transcript, { ...event, seq: ++seq })
    if (['text', 'thinking', 'tool'].includes(step.type)) {
      const block = step.type === 'tool' ? { type: 'tool_use', id: step.id, name: step.name, input: step.input }
        : step.type === 'thinking' ? { type: 'thinking', thinking: step.thinking } : { type: 'text', text: step.text }
      send({ type: 'assistant', parent_tool_use_id, message: { ...step.message, id: `full:${index}`, content: [block] } })
      if (!parent_tool_use_id && step.message?.model) model = step.message.model
      if (step.type === 'tool' && transcript._tools.has(step.id)) transcript._tools.get(step.id).isError = Boolean(step.is_error)
      if (step.type === 'tool' && step.output != null) send({ type: 'user', parent_tool_use_id,
        message: { content: [{ type: 'tool_result', tool_use_id: step.id, content: step.output, is_error: step.is_error }] } })
    } else if (step.event) {
      send({ ...step.event, parent_tool_use_id })
      if (step.type === 'end' && step.event.type === 'result' && !parent_tool_use_id && transcript.timeline.at(-1)?.kind === 'result') transcript.timeline.at(-1).model = step.event.model ?? model
    }
  })
  // A call without a result in a complete record ended with that reply.
  for (const tool of transcript._tools.values()) if (tool.result === null) tool.ended = true
  const segments = []
  const pushStep = (step) => {
    if (segments.at(-1)?.steps) segments.at(-1).steps.push(step)
    else segments.push({ steps: [step] })
  }
  for (const item of transcript.timeline) {
    if (item.kind === 'assistant') for (const block of item.blocks) {
      if (block.type === 'text' && block.text?.trim()) segments.push({ text: block.text })
      else if (block.type === 'tool' && blockView(block).view === 'plan') segments.push({ plan: block })
      else if (block.type === 'tool' || block.type === 'thinking' && block.text?.trim()) pushStep(block)
    }
    else if (foldsAsStep(item)) pushStep(item)
    else if (['answer', 'permission', 'result'].includes(item.kind)) segments.push({ item })
  }
  views.set(steps, segments)
  return segments
}

// Observe after paint, fetch only replies near the viewport, and cache per open
// thread. No bulk full-record read, polling, browser storage or read-mark GET.
export function createReplySteps({ read, changed, Observer = globalThis.IntersectionObserver }) {
  const entries = new Map(), nodes = new Map(), queue = []
  let observer, pane, active = 0, stopped = false
  function pump() {
    while (!stopped && active < 2 && queue.length) {
      const { id, state } = queue.shift(); active++
      Promise.resolve().then(() => read(id)).then((body) => {
        const entry = body?.steps?.find((entry) => entry.message === id)
        if (!entry || !Array.isArray(entry.steps)) throw new Error('The reply’s steps could not be read.')
        state.entry = entry; state.status = 'done'
      }).catch((error) => {
        state.status = [404, 405].includes(error.status) ? 'missing' : 'error'
        state.error = error.message
      }).finally(() => { active--; if (!stopped) { changed(id, state); pump() } })
    }
  }
  function ensure(id, retry = false) {
    if (stopped || entries.has(id) && !(retry && entries.get(id).status === 'error')) return
    const state = { status: 'loading' }; entries.set(id, state); queue.push({ id, state }); pump()
  }
  function watch(nextPane) {
    if (stopped) return
    if (pane !== nextPane) { observer?.disconnect(); nodes.clear(); pane = nextPane; observer = null }
    if (!pane || !Observer) return // Opening a fold also calls ensure().
    observer ??= new Observer((items) => {
      for (const item of items) if (item.isIntersecting) ensure(item.target.getAttribute('data-message-id'))
    }, { root: pane, rootMargin: '120px 0px' })
    const current = new Set(pane.querySelectorAll('[data-full-steps]'))
    for (const node of nodes.keys()) if (!current.has(node)) { observer.unobserve(node); nodes.delete(node) }
    for (const node of current) if (!nodes.has(node)) { nodes.set(node, true); observer.observe(node) }
  }
  return { entries, ensure, watch, stop() { stopped = true; queue.length = 0; observer?.disconnect(); nodes.clear() } }
}

// Warm residency is independent of whether a member is taking a turn.
export function memberTakingTurn(thread, member, open, resident) {
  if (typeof member.working === 'boolean') return member.working
  if (resident) return ['working', 'waiting'].includes(resident.state)
  if (open?.thread.id === thread.id && (open.typing.has(member.persona)
    || [...open.permissions.values()].some((p) => p.persona === member.persona))) return true
  // These summary fields are aliases for a turn in flight, per the thread
  // contract. They are unrelated to the live-session row's residency.
  return Boolean(member.busy ?? member.running)
}
