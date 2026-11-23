import { firstThreadLesson } from './first-use.js'
import { sendRequest, threadOutbox } from './thread-outbox.js'
import { createAgentDrafts } from './agent-drafts.js'
import { owner, isOwner } from './owner.js'
import { createCoreRegistry } from './core-registrations.js'
import { formatDateTime, formatCalendarDate } from './time.js'
import { menuSurface } from './menu-surface.js'
import { threadSelection } from './thread-selection.js'
import { threadIcons, threadGlyph, threadPersona, threadSubtitle, threadActivity, threadIconState, listeningPulse, listeningRecipients, paintMention, LISTENING_INSET, listeningInset } from './thread-identity.js'
import { threadWaiting, replyChip, replyTimestamp, composerModel, mentionChoices as choicesForMention, backgroundCount, threadBackground, memberGenerating, replyHasContent, showReply, taskTitle, taskElapsed, jobStatus, jobActivity, jobElapsed, failedTurnInfo, formatSystemLine } from './thread-experience.js'
import { createReplySteps, memberTakingTurn } from './thread-steps.js'
import { authorMarkers } from './thread-authors.js'
import { canReply, quoteLine, replyQuotes, replyGesture } from './thread-replies.js'
import { OPENING_MESSAGES, HISTORY_MESSAGES, openingContext, readingAnchor, restoreAnchor, mergeHistory, progressiveProse } from './thread-opening.js'
import { follower } from './follow.js'
import { layoutPixels } from './scale.js'
import { modelPill, settingsRow, listHeading, personaGrid, rowTop, chatHeader, chatFrame, chatDock, agentProse, userBubble, messageFiles, replyImages, stepFold, thinkingLine, emptyConversation, panelHeader, settingsNav, selectionMark, createMessageState, sheetHandle } from './chat-ui.js'
import { authenticatedFetch as fetch, authExpired, failureSentence } from './auth.js'
import { createPermissionCards } from './permission-cards.js'
import { h, kept, blob, avatarLook, icon, dress } from './ui.js'
import { markdown } from './markdown.js'
import { splitCards, plainWords } from './market-card.js'
import { copyText } from './clipboard.js'
import { emptyState } from './empty-state.js'
import { agentLook, createAgents, when, providerGlyph } from './sessions.js'
import { recordFaceSlots } from './face-identity.js'
import { modelsOf, modelLabel, modelTooltip } from './providers.js'
import { providerMark } from './marks.js'
import { readEvents } from './sse.js'
import { createLink } from './link.js'
import { readField, caretPoint, setMarkdown } from './compose.js'
import { applyThreadEvent, threadTurns, readThreadRecord, readThreadSummary, readThreadList, readAcceptedMessage, compareThreads, messageSegments, firstMessageTitle, groupJoinNotices, withPending, withMarks, createdAgentId } from './thread-record.js'

const button = (text, onclick, props = {}) => h('button', { class: 'pillbtn', type: 'button', onclick, ...props }, text)
export function createThreads({ registry = createCoreRegistry(), linePending = () => false, headerActionPending = () => false, api, redraw, agents, changedAgent = () => {}, agentsReady = () => false, loadAgents = () => {}, providers = () => [], rail, nav, enter, leave, compose, openProfile, openSettings = null, openRef, address = () => {}, viewAddress = () => {}, archiveAddress = () => false, historyButton = () => null, visitId = () => null, messageState = createMessageState(), sidebar = null, renderText = markdown, marketCards = null, fixture = false, running = null, stepUI = null, dom = document, navigate = null, side = () => null, sidePanel = false, visible = () => true, listIsMain = () => true, missing = () => {} }) {

  const follow = follower()
  const elementId = id => sidePanel ? `side-${id}` : id
  const authors = authorMarkers()
  const selection = threadSelection({ dom })
  const gesture = replyGesture({ reply: startReply, reduced: () => globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches })
  const longMessages = messageState.long, openMessages = messageState.open
  const longSystemLines = new Set()
  const st = { available: false, checked: false, threads: [], open: null, error: '', loading: false, creating: false, query: '', fixture, manage: false,
    drafts: new Map(), refreshed: 0 }
  let transport = api, fixtureStream = null, fixtureAgents = null, fixtureProviders = null, runtimeAgents = null
  let outbox
  function connectOutbox() {
    if (outbox || st.fixture) return
    outbox = threadOutbox(api)
    outbox.listen((message, id) => {
      if (st.fixture) return
      if (message && st.open?.thread.id === id) {
        applyThreadEvent(st.open, { type: 'message.done', message })
        receivedNote(st.open, message)
      }
      if (message) refreshList()
      redraw()
    })
  }
  if (!fixture && globalThis.indexedDB) connectOutbox()
  function pendingRows(o) {
    if (!outbox || st.fixture) return []
    return outbox.rows(o.thread.id).filter(item =>
      !o.messages.some(m => m.submission_id === item.body.submission_id))
  }
  const agentDraftController = createAgentDrafts({
    api: (...args) => transport(...args),
    redraw,
    made: agent => { changedAgent(agent); load({ quiet: true }) },
    askChanges: revision => {
      const o = st.open
      if (!o) return
      o.changingDraft = revision
      const field = compose.field()
      field.placeholder = changeDraftPrompt(o)
      field.focus({ preventScroll: true })
      o.focusReplyDraft = true
      redraw()
    },
    renderText,
  })
  const agentDrafts = agentDraftController.st
  const loadAgentDrafts = () => agentDraftController.load(st.open?.thread.id)
  // The list stream only sends its agent catalog at connection time.
  function refreshCreationCards(messages) {
    if (!st.fixture && messages?.some(m => m.agent_created)) loadAgents()
  }
  let controller = null, generation = 0, retry = null, listingRequest = 0
  let awaitTimer = null, awaitDue = Infinity
  let returnTimer = null
  function clearReturn() { clearTimeout(returnTimer); returnTimer = null; st.returning = null }
  const awaitingSince = new Map()
  const visits = new Map()
  const readingPlaces = new Map()
  let transferred = null
  let proseSerial = 0
  let listLink = null, fixtureListStream = null, stopFixtureList = null
  const removals = new Map()
  function cancelRemoval(id) {
    clearTimeout(removals.get(id)?.timer)
    removals.delete(id)
  }
  function confirmRemoval(id) {
    if (removals.has(id) || authExpired()) return
    const check = { absent: 0, timer: null }
    removals.set(id, check)
    const verify = async () => {
      try {
        // A removed event can reflect a transient host read. Confirm against
        // fresh, complete listings, including archives, without marking any
        // messages read (the newest-page thread GET would do that).
        const body = readThreadList(await transport('GET', '/threads?archived=1'))
        if (removals.get(id) !== check) return
        const row = body.threads.find((t) => t.id === id)
        if (row) { updateRow(row); redraw(); return }
        if (++check.absent === 2) {
          cancelRemoval(id)
          visits.delete(id)
          st.threads = st.threads.filter((t) => t.id !== id)
          listingRequest++
          if (st.open?.thread.id === id) {
            generation++; controller?.abort(); clearTimeout(retry)
            Object.assign(st.open, { loading: false, refreshing: false, readFailed: true, connection: '', error: 'This thread was deleted.' })
            missing(id)
          }
          redraw(); return
        }
      } catch {
        // Failed or malformed reads say nothing about whether a thread exists.
        check.absent = 0
      }
      if (removals.get(id) !== check) return
      if (authExpired()) { cancelRemoval(id); return }
      check.timer = setTimeout(verify, 2500)
    }
    verify()
  }
  const archivePage = 50
  let archiveLimit = archivePage, archiveQuery = '', archiveObserver = null, archiveSentinel = null
  function showArchived(value) {
    st.archived = value; st.listMenu = false; archiveLimit = archivePage
    st.resetListScroll = true; st.focusList = true
    redraw()
  }
  function moreArchived() { archiveLimit += archivePage; redraw() }
  // The shared field and composer, supplied by the app.
  const draft = {
    get value() { return compose.field().value }, set value(text) { compose.field().value = text },
    get selectionStart() { return readField(compose.field(), caretPoint(compose.field())).caret },
    focus: () => compose.field().focus(),
    setSelectionRange: (at) => setMarkdown(compose.field(), draft.value, at),
  }
  function mentionKey(e) {
    if (!st.mention) return false
    if (['ArrowDown', 'ArrowUp', 'Enter'].includes(e.key)) {
      const choices = mentionChoices(); e.preventDefault()
      if (e.key === 'Enter') { if (choices.length) insertMention(choices[st.mention.index ?? 0]) }
      else st.mention.index = ((st.mention.index ?? 0) + (e.key === 'ArrowDown' ? 1 : -1) + choices.length) % (choices.length || 1)
      redraw(); return true
    }
    if (e.key === 'Escape') { e.preventDefault(); st.mention = null; redraw(); return true }
    return false
  }
  document.addEventListener?.('focusout', (e) => {
    if (st.mention && e.target === compose.field() && !e.relatedTarget?.closest?.('.tt-mentions, .tt-mention-button')) { st.mention = null; redraw() }
  })
  document.addEventListener?.('selectionchange', () => { if (st.open && document.activeElement === compose.field()) leanFaces() })
  const title = h('input', { class: 'tt-rename', 'aria-label': 'Thread title', onblur: () => finishRename(), onkeydown: (e) => {
    if (e.key === 'Escape') { e.preventDefault(); st.open.renaming = false; st.open.focusRename = false; redraw() }
    if (e.key === 'Enter') { e.preventDefault(); finishRename() }
  } })
  function beginRename() {
    if (!st.open || st.open.patching || st.open.thread.id === 'general' || st.open.thread.general) return
    title.value = st.open.thread.title ?? ''; st.open.renaming = true; st.open.focusRename = true; redraw()
  }
  async function finishRename() {
    const o = st.open
    if (!o?.renaming || o.patching) return
    const value = title.value.trim()
    o.renaming = false
    if (value && value !== o.thread.title) await patch({ title: value })
    redraw()
  }
  const catalog = () => (fixtureAgents ?? runtimeAgents ?? agents()).filter(a => !a.archived)
  const look = (id) => avatarLook(agentLook(id, createAgents(catalog())))
  const name = (id) => isOwner(id) ? owner().name : catalog().find((a) => a.id === id)?.name ?? (id ? id[0].toUpperCase() + id.slice(1) : 'Nobody')
  const systemText = (text) => failureSentence(String(text).replace(/\[\[([^\]]+)\]\]/g, '$1').replace(/\b([\w-]+) brought in ([\w-]+)\b/g, (_, by, who) => `${name(by)} brought in ${name(who)}`))
  const avatar = (id, size = 36) => blob(look(id), { size, font: size * .4 })
  const collapsed = () => sidebar ? sidebar.collapsed() : Boolean(st.collapsed)
  const collapse = (value) => { if (sidebar) sidebar.set(value); else { st.collapsed = value; redraw() } }
  const isGeneral = (thread) => thread.general || thread.id === 'general'
  const activityOptions = (thread) => ({ pending: st.open?.thread.id === thread.id ? [...st.open.permissions.values()] : [], resident: (id) => running?.forMember(thread.id, id), isAgent: (id) => catalog().some((a) => a.id === id) })
  const threadFace = (thread, size = 40) => {
    // The open thread may receive a member update before the list stream does.
    if (st.open?.thread.id === thread.id) thread = st.open.thread
    const persona = threadPersona(thread)
    const state = threadIconState(thread, activityOptions(thread))
    const face = persona ? avatar(persona, size) : h('span', { class: 'thread-tile' }, threadGlyph(isGeneral(thread) ? null : thread.icon, Math.round(size * .5)))
    face.className += ' tt-thread-icon'
    face.setAttribute('data-icon-state', state.kind)
    face.setAttribute('role', 'img')
    face.setAttribute('aria-label', `${thread.title || 'New thread'} · ${threadSubtitle(thread, name, activityOptions(thread))}`)
    dress(face, { width: `${size}px`, height: `${size}px`, fontSize: `${size * (persona ? .4 : .45)}px`, '--tt-icon-tone': state.persona ? look(state.persona).color : 'var(--paper-ink-3)' })
    if (state.kind !== 'rest') face.append(h('span', { class: `tt-icon-layer tt-icon-${state.kind}`, 'aria-hidden': 'true' }))
    return face
  }
  const round = (label, glyph, action, props = {}) => button(glyph, action, { class: 'iconbtn', title: label, 'aria-label': label, ...props })
  const backgroundBadge = (record) => {
    const n = backgroundCount(record), label = `${n} background ${n === 1 ? 'task' : 'tasks'}`
    return n ? h('span', { class: 'srow-bg', role: 'img', title: label, 'aria-label': label, text: String(n) }) : null
  }
  const path = (id) => `/threads/${encodeURIComponent(id)}`
  const identityOf = (row) => JSON.stringify([row.title, row.default, row.icon, row.pinned, row.archived,
    row.members.map((m) => [m.persona, m.engine, m.model]).sort((a, b) => a[0].localeCompare(b[0]))])
  function updateRow(row) {
    readThreadSummary(row)
    cancelRemoval(row.id)
    const o = st.open?.thread.id === row.id ? st.open : null
    if (o?.patching) { o.streamSummary = row; o.streamIdentities?.add(identityOf(row)); return }
    // HTTP may settle before an older SSE row is delivered. Wait for this
    // connection's ordered acknowledgement before accepting another identity.
    if (o?.summaryBarrier && identityOf(row) !== o.summaryBarrier) return
    if (o) o.summaryBarrier = null
    const at = st.threads.findIndex((t) => t.id === row.id)
    if (at < 0) st.threads.push(row); else st.threads[at] = row
    st.threads.sort(compareThreads)
    if (st.open?.thread.id === row.id) {
      if (st.open.loading) st.open.latestSummary = row
      else st.open.thread = row
    }
  }
  function listEvent(e) {
    if (e.type === 'threads.snapshot') {
      const body = readThreadList(e)
      for (const row of body.threads) cancelRemoval(row.id)
      st.threads = body.threads.sort(compareThreads); runtimeAgents = body.agents
      recordFaceSlots(body.agents)
      st.listRetryAt = 0; st.listSnapshot = true; st.listLive = true; st.available = true; st.checked = true
      const current = st.open && body.threads.find((t) => t.id === st.open.thread.id)
      if (current) { st.open.summaryBarrier = null; updateRow(current) }
    } else if (e.type === 'thread.changed' && e.summary) updateRow(e.summary)
    else if (e.type === 'thread.removed') {
      if (typeof e.thread === 'string') confirmRemoval(e.thread)
    } else return
    listingRequest++; redraw()
  }
  function stopList() {
    for (const id of removals.keys()) cancelRemoval(id)
    listLink?.stop(); listLink = null; stopFixtureList?.(); stopFixtureList = null
    st.listLive = false; st.listSnapshot = false
  }
  function ensureList() {
    if (authExpired() || !st.available || Date.now() < (st.listRetryAt ?? 0)) return
    if (fixtureListStream) { if (!stopFixtureList) stopFixtureList = fixtureListStream(listEvent); return }
    if (listLink && listLink.phase !== 'stopped') return
    listLink = createLink({ href: () => '/api/threads/events?archived=1',
      fetchImpl: async (...args) => {
        const res = await fetch(...args)
        if ([404, 405].includes(res.status)) { listLink?.stop(); listLink = null; st.listLive = false; st.listRetryAt = Date.now() + 15000 }
        return res
      },
      onEvent: listEvent,
      onPhase: (phase) => { if (phase !== 'open') { st.listLive = false; st.listSnapshot = false }; redraw() },
    })
    listLink.start().catch(() => {})
  }
  function refreshList() { if (!st.listLive) return load({ quiet: true }) }
  async function load({ quiet = false } = {}) {
    const ticket = ++listingRequest
    st.loading = true
    try {
      const body = readThreadList(await transport('GET', '/threads?archived=1'))
      if (ticket !== listingRequest) return
      for (const row of body.threads) cancelRemoval(row.id)
      if (Array.isArray(body.agents)) { runtimeAgents = body.agents; recordFaceSlots(body.agents) }
      st.threads = body.threads.sort(compareThreads); st.available = true; st.error = ''; ensureList()
      // A list request can predate a PATCH; it never overwrites the open record.
    } catch (error) {
      if (ticket !== listingRequest) return
      if (!quiet) st.error = error.message
    } finally { st.checked = true; st.loading = false; st.refreshed = Date.now(); redraw() }
  }
  async function preview() {
    close(); stopList()
    const { createThreadFixture } = await import('./threads-fixture.js')
    const f = createThreadFixture()
    transport = f.api; fixtureStream = f.subscribe; fixtureListStream = f.subscribeList; fixtureAgents = f.agents; fixtureProviders = f.providers
    st.fixture = true; st.available = false; st.threads = []; st.error = ''; st.drafts.clear()
    visits.clear()
    enter(); await load()
  }
  function exitPreview() {
    close(); stopList(); transport = api; fixtureStream = null; fixtureListStream = null; fixtureAgents = null; fixtureProviders = null
    st.fixture = false; st.available = false; st.threads = []; st.drafts.clear()
    visits.clear(); readingPlaces.clear()
    leave(); load()
  }
  function close({ navigate = true } = {}) {
    st.personaMenu = null
    if (st.open) st.open.branch = null
    gesture.reset()
    const pane = dom.querySelector?.('.tt-stream')
    authors.disconnect()
    clearTimeout(awaitTimer); awaitTimer = null; awaitingSince.clear()
    clearTaskTimers(st.open)
    clearTimeout(st.open?.replyHighlightTimer)
    clearTimeout(st.open?.copyMessageTimer)
    clearListeningTimers(st.open)
    follow.watch(null)
    if (!st.open) return
    const o = st.open, id = o.thread.id
    const drop = unsentHere(o) && transferred !== id
    if (!o.readFailed && o.messages.length) {
      const place = { anchor: readingAnchor(pane) ?? o.readingPosition, atEnd: o.atEnd !== false }
      if (o.visitId != null) readingPlaces.set(o.visitId, { id, ...place })
      visits.delete(id)
      visits.set(id, { messages: o.messages.filter((m) => !m.streaming).map(({ steps, ...message }) => message), has_more: o.has_more, thread: o.thread,
        ...place, unread: o.openingUnread, firstUnseen: o.firstUnseen, proseCache: o.prose?.cache })
    } else if (!o.readFailed && !o.loading) visits.delete(id)
    o.replySteps?.stop()
    o.readingObserver?.disconnect()
    o.prose?.stop()
    saveCompose()
    if (transferred === id) {
      for (const drafts of [st.drafts, visits]) drafts.delete(id)
      transferred = null
    }
    compose.field().__paintMention = null
    compose.leave()
    generation++; controller?.abort(); controller = null; clearTimeout(retry)
    cards.questionDrafts.clear()
    st.open = null; st.manage = false; st.mention = null; draft.value = ''
    clearReturn()
    if (!st.wide) {
      st.returning = id
      returnTimer = setTimeout(() => { clearReturn(); redraw() }, 1000)
    }
    if (navigate && !st.fixture) address(null)
    if (drop) dropUnsent(id)
  }
  // On leaving a new thread, discard it only if its messages, draft, attachments and extension drafts are empty.
  function unsentHere(o) {
    return Boolean(o?.madeHere && !o.creating && !o.createFailed && !o.sending && !o.readFailed && !pendingRows(o).length
      && !o.messages.some((m) => m.kind !== 'system') && !draft.value.trim() && !compose.files().length && !dirty())
  }
  function dropUnsent(id) {
    st.threads = st.threads.filter((t) => t.id !== id); listingRequest++
    for (const kept of [st.drafts, visits]) kept.delete(id)
    transport('DELETE', path(id) + '?empty=1').catch(() => refreshList())
  }
  if (!fixture && !sidePanel) globalThis.addEventListener?.('pagehide', () => {
    const o = st.open
    if (unsentHere(o)) globalThis.fetch?.(`/api${path(o.thread.id)}?empty=1`, { method: 'DELETE', keepalive: true, credentials: 'same-origin' })?.catch?.(() => {})
  })
  async function open(id, { push = true, listing = null, routed = false, message = null, focusMessage = false } = {}) {
    if (navigate && !routed) return navigate({ kind: 'thread', id, push, listing, message, focusMessage })
    close({ navigate: false }); enter({ kind: 'thread', id })
    clearReturn()
    const ticket = generation
    const savedVisit = visits.get(id), summary = st.threads.find((t) => t.id === id)
    st.open = { thread: summary ?? savedVisit?.thread ?? { id, title: 'Opening…', members: [] }, messages: savedVisit?.messages.slice() ?? [], loading: !savedVisit,
      openingUnread: summary?.unread ?? savedVisit?.unread ?? 0, firstUnseen: summary ? null : savedVisit?.firstUnseen ?? null,
      savedAnchor: savedVisit?.atEnd === false ? savedVisit.anchor : null, landing: !savedVisit,
      atEnd: savedVisit ? savedVisit.atEnd : !(summary?.unread > 0), refreshing: true,
      typing: new Set(), permissions: new Map(), after: null, steps: new Map(), has_more: savedVisit?.has_more ?? false,
    }
    const o = st.open
    if (message) Object.assign(o, { replyJump: message, replyJumpFocus: focusMessage, jumpKind: 'quote', savedAnchor: null, landing: false, atEnd: false })
    if (push && !st.fixture) address(id)
    attachReplySteps(st.open)
    attachProse(o, savedVisit?.proseCache)
    attachQuotes(o)
    o.replyTo = st.drafts.get(id)?.replyTo ?? null
    draft.value = st.drafts.get(id)?.text ?? summary?.draft ?? ''
    compose.restore(st.drafts.get(id)?.attachments ?? [])
    redraw()
    if (!st.fixture) agentDraftController.load(id)
    await readOpening(id, ticket, o, { savedVisit, summary, listing })
  }
  async function readOpening(id, ticket, o, { savedVisit, summary, listing }) {
    try {
      // A cold/deep-linked open needs the same pre-read count and agent catalog
      // as a row tap. The newest-page GET clears the host's count.
      if (!summary && (listing || !st.checked)) {
        await (listing ?? load({ quiet: true }))
        if (ticket !== generation) return
        const listed = st.threads.find((t) => t.id === id)
        if (listed) { o.thread = listed; o.openingUnread = listed.unread ?? 0; if (!savedVisit && !o.replyJump) o.atEnd = !(o.openingUnread > 0) }
        redraw()
      }
      const body = readThreadRecord(await transport('GET', `${path(id)}?limit=${OPENING_MESSAGES}`), id)
      if (ticket !== generation) return
      cancelRemoval(id)
      const previous = o.messages, known = new Map(previous.map((m) => [m.id, m])), first = body.messages.findIndex((m) => known.has(m.id))
      // A revisit can span more than one latest page. Fill a gap in the
      // background before joining the saved history to this fresh tail.
      o.gapBefore = previous.length && body.has_more && first < 0 ? body.messages[0]?.id : null
      // Live replies keep their start positions, so completion IDs may be
      // interleaved in a cached visit. Retain only IDs outside the fresh tail
      // before adopting the host's durable order; slicing at an overlap can
      // otherwise keep a second copy of a reply that completed later.
      const freshIds = new Set(body.messages.map((m) => m.id))
      const retained = previous.filter((m) => !freshIds.has(m.id))
      const messages = body.messages.length ? [...retained, ...body.messages.map((fresh) => {
        const m = known.get(fresh.id)
        // A visible cached reply can finish its lazy read while this tail GET
        // is in flight. Keep that enrichment when the durable reply agrees.
        return { ...fresh, ...(m?.viewKey ? { viewKey: m.viewKey } : {}),
          ...(fresh.steps == null && m?.steps && fresh.text === m.text && fresh.author === m.author ? { steps: m.steps } : {}) }
      })] : []
      Object.assign(o, body, { messages, has_more: retained.length && messages.length ? savedVisit.has_more : body.has_more,
        loading: false, refreshing: false, connection: '', after: body.messages.at(-1)?.id ?? null })
      refreshCreationCards(messages)
      const latestOwner = messages.findLast(m => isOwner(m.author))
      if (!o.thread.default && latestOwner?.unaddressed && !messages.some(m => m.answer_for === latestOwner.id)) {
        o.note ??= { id: latestOwner.id, teach: false }
      }
      readJobs(o, body.thread)
      // A broker-created opening survives reloads and other devices. An edited
      // page-local draft, including an intentionally empty one, takes priority.
      if (!st.drafts.has(id)) {
        draft.value = typeof body.thread.draft === 'string' ? body.thread.draft : ''
        o.replyTo = body.draft_reply ?? null
      }
      o.firstUnseen = body.first_unseen_message_id ?? o.firstUnseen
      updateOpening(o)
      if (st.open.latestSummary) { st.open.thread = st.open.latestSummary; delete st.open.latestSummary }
      applyThreadEvent(o, { type: 'thread.snapshot', turns: threadTurns(body) })
      for (const p of body.pending_permissions ?? []) applyThreadEvent(st.open, { ...p, type: 'broker.permission_request' })
      // The broker replays completions after this ID, including a reply that
      // arrived between the page read and stream attachment.
      connect(id, ticket, (e) => event(e, ticket))
    } catch (error) {
      if (ticket === generation && !authExpired()) {
        if (!error.status || [404, 408, 429].includes(error.status) || error.status >= 500) {
          if (error.status === 404) confirmRemoval(id)
          o.connection = 'Reconnecting…'
          retry = setTimeout(() => { if (ticket === generation) readOpening(id, ticket, o, { savedVisit, summary, listing }) }, 2500)
        } else {
          o.loading = false; o.refreshing = false; o.readFailed = true; o.error = error.message
        }
      }
    }
    redraw()
  }
  function attachProse(o, cache) {
    o.prose = progressiveProse({
      cache,
      // A newly formatted node must replace its plain node, rather than lend
      // its children to it: this cache keeps the actual node across redraws.
      plain: (text, key, pictures) => h('div', { key: `prose:${++proseSerial}`, class: 'tt-prose-content tt-prose-plain', 'data-prose-pending': key, text: pictures ? pictures.words(text) : text }),
      build: (text, pictures) => h('div', { key: `prose:${++proseSerial}`, class: 'tt-prose-content' }, ...mentionMarkdown(text, pictures)),
      changed: (format) => { if (st.open !== o) return; rememberReading(o); format(); redraw() },
    })
  }
  function attachQuotes(o) {
    o.quotes = replyQuotes({ read: (before) => transportHistory(o, before),
      active: () => st.open === o, changed: () => { if (st.open === o) { rememberReading(o); redraw() } } })
  }
  async function transportHistory(o, before) {
    return readThreadRecord(await transport('GET', `${path(o.thread.id)}?before=${encodeURIComponent(before)}&limit=${HISTORY_MESSAGES}`), o.thread.id)
  }
  function updateOpening(o) {
    const knownAgents = [...catalog(), ...o.thread.members.map((m) => ({ id: m.persona }))]
    const context = openingContext(o.messages, knownAgents, o.openingUnread, o.firstUnseen)
    if (!o.firstUnseen && context.unseen) o.firstUnseen = context.unseen
    o.needsContext = o.has_more && context.needsEarlier
    if (!o.firstUnseen && !o.has_more && o.openingUnread > 0) o.firstUnseen = o.messages.find((m) => m.kind !== 'system' && knownAgents.some((a) => a.id === m.author))?.id
    if (o.landing && !o.savedAnchor && o.firstUnseen) { o.landAt = o.firstUnseen; o.atEnd = false }
  }
  function attachReplySteps(o) {
    const id = o.thread.id
    o.replySteps = createReplySteps({ read: (message) => transport('GET', `${path(id)}/steps?message=${encodeURIComponent(message)}`),
      changed: (message, state) => {
        if (st.open !== o) return
        const row = o.messages.find((m) => m.id === message)
        if (row && state.entry) {
          rememberReading(o)
          row.steps = state.entry.steps
          row.model ??= state.entry.model
          messageSegments(row).forEach((part, index) => { if (part.steps && o.didOpen?.has(`${row.viewKey ?? row.id}:${index}`)) stepUI?.reveal(part.steps) })
        }
        redraw()
      } })
  }
  function connect(id, ticket, deliver) {
    controller = new AbortController()
    const abort = controller
    const after = st.open?.after ?? 'm0'
    if (fixtureStream) { const stop = fixtureStream(id, deliver, after); abort.signal.addEventListener('abort', stop); return }
    ;(async () => {
      try {
        const res = await fetch(`/api${path(id)}/events?after=${encodeURIComponent(after)}`, { signal: abort.signal })
        if (!res.ok) {
          if (res.status === 404) confirmRemoval(id)
          const body = await res.json().catch(() => null)
          throw new Error(body?.error ?? `Live updates paused (${res.status}).`)
        }
        for await (const e of readEvents(res)) {
          if (ticket !== generation) return
          if (st.open) st.open.connection = ''
          deliver(e)
        }
        if (!abort.signal.aborted) throw new Error('Live updates paused. Reconnecting…')
      } catch (error) {
        if (abort.signal.aborted || ticket !== generation || authExpired()) return
        st.open.connection = 'Reconnecting…'; redraw()
        // Reconnect only from completed message IDs; a GET here would mark
        // unseen replies read even while the person is browsing earlier pages.
        retry = setTimeout(() => {
          if (ticket === generation) connect(id, ticket, deliver)
        }, 2500)
      }
    })()
  }
  function event(e, ticket) {
    const o = st.open
    if (!o || ticket !== generation) return
    if (['thread.snapshot', 'members', 'turn.started', 'turn.step', 'turn.waiting', 'turn.mark', 'turn.finished', 'message.delta', 'message.done', 'broker.user_message'].includes(e.type)) cancelRemoval(o.thread.id)
    if (e.type === 'turn.started') awaitingSince.delete(e.persona)
    if (['thread.snapshot', 'members', 'turn.started', 'turn.step', 'turn.waiting', 'turn.mark', 'turn.finished', 'message.delta', 'message.done'].includes(e.type)) o.eventVersion = (o.eventVersion ?? 0) + 1
    if (e.type !== 'thread.changed') applyThreadEvent(o, e)
    else if (!st.listLive && e.summary?.id === o.thread.id) updateRow(e.summary)
    registry.emit('thread.event', { thread: o.thread, event: e })
    if (e.type === 'message.done' || e.type === 'broker.user_message') registry.emit('message.arrived', { thread: o.thread, event: e })
    if (e.type === 'turn.finished') { registry.emit('turn.ended', { thread: o.thread, event: e }); if (!st.fixture) agentDraftController.load(o.thread.id) }
    if (e.type === 'turn.started') registry.emit('turn.started', { thread: o.thread, event: e })
    if (e.type === 'broker.permission_resolved') forgetAnswers(o, e.request_id)
    if (e.type === 'turn.finished' && o.deleteBlocked === o.error) { o.error = ''; o.deleteBlocked = null }
    if (e.type === 'message.done') {
      receivedNote(o, e.message)
      refreshList(); if (o.atEnd !== false) markRead(o)
      if (!st.fixture && createdAgentId(e.message)) loadAgents()
      if (!st.fixture && e.message?.kind === 'system') agentDraftController.load(o.thread.id)
      if (!st.listLive && e.message?.kind === 'system' && o.typing.size) refreshTurn(o, e.message.id, o.eventVersion)
    }
    redraw()
  }
  async function refreshTurn(o, before, version) {
    // Failed turns end with a system message, with no turn.finished event.
    // Read the host's active turn without triggering the GET read mark.
    try {
      const body = readThreadRecord(await transport('GET', `${path(o.thread.id)}?before=${encodeURIComponent(before)}&limit=1`), o.thread.id)
      if (st.open !== o || o.eventVersion !== version) return
      applyThreadEvent(o, { type: 'thread.snapshot', turns: threadTurns(body), members: body.thread.members })
      redraw()
    } catch { /* Keep the known turn until a later snapshot can settle it. */ }
  }
  function saveCompose() {
    if (st.open) st.drafts.set(st.open.thread.id, { text: draft.value, attachments: compose.files(), replyTo: st.open.replyTo })
  }
  function transferTo(target, id) {
    const o = st.open
    if (o?.thread.id === id) {
      saveCompose()
      if (!o.readFailed && o.messages.length) target.receiveVisit(id, {
        messages: o.messages.filter(message => !message.streaming).map(({ steps, ...message }) => message),
        has_more: o.has_more, thread: o.thread, anchor: readingAnchor(dom.querySelector('.tt-stream')) ?? o.readingPosition,
        atEnd: o.atEnd !== false, unread: o.openingUnread, firstUnseen: o.firstUnseen,
      })
      transferred = id
    }
    if (st.drafts.has(id)) target.st.drafts.set(id, st.drafts.get(id))
  }
  function beginBranch(message) {
    if (!st.open || !canReply(message) || st.open.branch) return
    st.open.branch = message
    redraw()
  }
  function closeBranch() {
    if (st.open) st.open.branch = null
    redraw()
  }
  function branchPanel() {
    const o = st.open, message = o?.branch
    if (!message) return null
    const active = new Set(catalog().map((agent) => agent.id))
    const people = [...new Set([o.thread.default, ...o.thread.members.map((m) => m.persona), ...active])].filter((id) => active.has(id))
    const source = { ...message, thread: o.thread.id, title: o.thread.title }
    return menuSurface(h('section', { key: `branch:${message.id}`, class: 'tt-branch-panel', role: 'dialog', 'aria-modal': 'true' },
      people.length ? h('div', { class: 'tt-branch-people', role: 'group', 'aria-label': 'Choose an agent for the new thread' },
        ...people.map((id) => button([avatar(id, 42), h('span', { text: name(id) })], () => create(id, { source }),
          { class: 'tt-branch-person', 'aria-label': name(id) })))
        : h('p', { class: 'muted', text: 'No agents are available.' })),
    { title: 'New thread with', width: 420, anchor: `[data-message-id=${JSON.stringify(message.id)}] .tt-footer-branch`, close: closeBranch })
  }
  async function followThreadLink(e, link) {
    if (e.altKey && side()?.available()) {
      e.preventDefault(); side().openSide({ kind: 'thread', id: link.thread, message: link.message, focusMessage: true }); return
    }
    if (e.button > 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    await open(link.thread, { message: link.message, focusMessage: true })
  }
  function create(persona = null, options = {}) {
    if (st.creating) return
    close({ navigate: false }); enter({ kind: 'thread', id: 'new-thread' }); st.creating = true; st.error = ''
    const ticket = generation
    const o = st.open = { thread: { id: 'new-thread', title: 'New thread', default: persona, members: persona ? [{ persona, engine: null }] : [] },
      messages: [], loading: false, creating: true, typing: new Set(), permissions: new Map(), steps: new Map(),
      replyTo: options.source ?? null,
      promptTitle: options.promptTitle ?? null, promptBody: options.promptBody ?? null, promptPlaceholder: options.placeholder ?? null }
    // Pre-fill the composer and leave sending to the person.
    if (typeof options.draft === 'string' && options.draft) draft.value = options.draft
    redraw(); setTimeout(() => { draft.focus(); if (options.draft) draft.setSelectionRange(draft.value.length) }, 0)
    const source = options.source
    return transport('POST', source ? `${path(source.thread)}/branch` : '/threads',
      source ? { persona, message: source.id } : persona ? { persona } : {}).then((body) => {
      const thread = readThreadSummary(body)
      // Left before it was made: nothing can have been sent, so it goes.
      if (ticket !== generation) { transport('DELETE', path(thread.id)).catch(() => {}).finally(() => refreshList()); return }
      if (source && !body.draft_reply) throw new Error('The runtime hasn’t opened a branch composer. Try again after it updates.')
      if (source && o.replyTo) o.replyTo = body.draft_reply
      o.thread = thread; o.creating = false; o.madeHere = true; o.ordered_submissions = thread.ordered_submissions
      attachReplySteps(o)
      attachProse(o); if (!st.fixture) address(thread.id)
      attachQuotes(o)
      st.threads = [thread, ...st.threads.filter((t) => t.id !== thread.id)]
      connect(thread.id, ticket, (e) => event(e, ticket))
    }).catch((e) => {
      if (ticket !== generation) return
      o.error = !persona && e.status === 400 ? 'Starting a thread without a default needs a runtime update. You can still start one with an agent.' : failureSentence(e.message); o.createFailed = true; o.creating = false
    }).finally(() => { st.creating = false; redraw() })
  }
  function patch(body) {
    const o = st.open
    if (!o || o.creating || o.createFailed) return Promise.resolve(false)
    const pending = (o.patchTail ?? Promise.resolve()).then(() => writePatch(o, body))
    o.patchTail = pending.catch(() => false)
    return pending
  }
  async function writePatch(o, body) {
    o.patching = true; o.streamIdentities = new Set(); o.error = ''; redraw()
    try {
      const answer = await transport('PATCH', path(o.thread.id), body)
      const record = readThreadSummary(answer)
      if (record.id !== o.thread.id) throw new Error('The runtime answered for a different thread.')
      const acknowledged = o.streamIdentities.has(identityOf(record))
      o.thread = acknowledged ? o.streamSummary ?? record : record
      o.summaryBarrier = acknowledged ? null : identityOf(record)
      delete o.streamSummary; delete o.streamIdentities
      const unsupported = ['title', 'default', 'icon', 'pinned', 'archived'].some((key) => Object.hasOwn(body, key) && (record[key] ?? null) !== body[key])
        || ['model', 'engine'].some((field) => Object.entries(body[field] ?? {}).some(([persona, value]) => (field === 'engine' ? engineOverride(record.members.find((m) => m.persona === persona))
          : record.members.find((m) => m.persona === persona)?.[field]) !== value))
      if (unsupported) throw new Error('This runtime hasn’t applied that change. It may need an update; your thread is still here.')
      listingRequest++
      st.threads = st.threads.map((t) => t.id === o.thread.id ? o.thread : t); return true
    } catch (e) { o.error = e.message; return false } finally {
      o.patching = false
      if (o.streamSummary && !o.readFailed) {
        if (o.streamIdentities?.has(o.summaryBarrier)) o.summaryBarrier = null
        updateRow(o.streamSummary)
      }
      delete o.streamSummary; delete o.streamIdentities
      redraw()
    }
  }
  async function send() {
    const o = st.open, text = draft.value.trim(), attachments = compose.files()
    if (!o || (!text && (o.replyTo?.thread || !attachments.length)) || o.sending || o.loading || o.refreshing || o.readFailed || o.creating || o.createFailed || compose.busy()) return
    const cleanTitle = o.thread.title === 'New thread' && !o.messages.some((m) => m.kind !== 'system') && /^\s*@/u.test(text) ? firstMessageTitle(text) : null
    o.listenerSendingUntil = Date.now() + 260
    clearTimeout(o.listenerSendTimer)
    o.listenerSendTimer = setTimeout(() => { if (st.open === o) redraw() }, 260)
    const reply_to = o.replyTo?.id, reply_thread = o.replyTo?.thread
    const reference = reply_to ? { reply_to, ...(reply_thread ? { reply_thread } : {}) } : {}
    if (o.ordered_submissions && !st.fixture) {
      o.sending = true
      o.error = ''
      try {
        connectOutbox()
        const body = { submission_id: crypto.randomUUID(), text,
          ...reference,
          ...(attachments.length ? { attachments: attachments.map(f => f.path) } : {}) }
        await outbox.add(o.thread.id, body, attachments, o.replyTo ?? null)
        st.drafts.delete(o.thread.id)
        if (st.open === o) {
          o.replyTo = null; o.changingDraft = null; draft.value = ''; compose.clear(); st.mention = null
          o.atEnd = true; o.readingPosition = null; o.openingBoundary = null; o.replyAnchor = null; o.landAt = null; o.landing = false
        }
      } catch (error) { o.error = `Your message is still here. ${error.message}` }
      finally { o.sending = false; redraw() }
      return
    }
    o.sending = true; o.pending = { id: 'pending-send', author: owner().id, kind: 'message', text, attachments, reply_to, reply_source: reply_thread ? o.replyTo : null }; o.atEnd = true; o.error = ''; st.mention = null; redraw()
    o.readingPosition = null; o.openingBoundary = null; o.replyAnchor = null; o.landAt = null; o.landing = false
    let accepted = false
    // Keep the draft until acceptance. A lost response never auto-resends a
    // message: Retry is an explicit act and the durable record is readable.
    try {
      const answer = await sendRequest(transport, 'POST', `${path(o.thread.id)}/messages`, { text, ...reference, ...(attachments.length ? { attachments: attachments.map((f) => f.path) } : {}) })
      accepted = true
      const message = readAcceptedMessage(answer)
      if (cleanTitle) {
        if (st.open === o) patch({ title: cleanTitle })
        else transport('PATCH', path(o.thread.id), { title: cleanTitle }).catch(() => {})
      }
      if (st.drafts.get(o.thread.id)?.text.trim() === text) st.drafts.delete(o.thread.id)
      if (st.open !== o) return
      o.replyTo = null; o.changingDraft = null; draft.value = ''; compose.clear()
      applyThreadEvent(o, { type: 'message.done', message })
      receivedNote(o, message)
      refreshList()
    } catch (e) { o.error = accepted ? `Message sent. ${e.message} Refresh to read the latest reply.` : e.status
      ? `${e.message} Your message is still here.`
      : 'Couldn’t confirm the send. Your message is still here. Refresh the thread to check before sending again.' }
    finally { o.sending = false; o.pending = null; redraw() }
  }
  function mention() {
    if (!st.open) return
    if (draft.value.trim()) listeningPulse(st.open, Date.now(), globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches)
    const at = draft.selectionStart ?? draft.value.length
    const match = /(?:^|\s)@([^\s@]*)$/.exec(draft.value.slice(0, at))
    st.mention = match ? { query: match[1], at, start: at - match[1].length - 1 } : null
  }
  function mentionChoices() {
    return choicesForMention(catalog(), st.mention?.query ?? '')
  }
  function toggleMention() {
    const m = st.mention
    let at
    if (m) {
      draft.value = draft.value.slice(0, m.start) + draft.value.slice(m.at)
      at = m.start
      st.mention = null
    } else {
      at = draft.selectionStart ?? draft.value.length
      const tag = at > 0 && !/\s$/.test(draft.value.slice(0, at)) ? ' @' : '@'
      draft.value = draft.value.slice(0, at) + tag + draft.value.slice(at)
      at += tag.length
      st.mention = { query: '', at, start: at - 1 }
    }
    draft.focus(); draft.setSelectionRange(at)
    compose.changed(); saveCompose(); redraw()
  }
  function insertMention(a) {
    const m = st.mention
    if (!m) return
    const prefix = m.start > 0 && !/\s$/.test(draft.value.slice(0, m.start)) ? ' ' : ''
    const tag = `${prefix}@${a.id} `
    draft.value = draft.value.slice(0, m.start) + tag + draft.value.slice(m.at)
    // The shared field is kept across redraws. Restore its caret now: a
    // deferred rewrite can race the next keystrokes and discard/reorder them.
    draft.focus(); draft.setSelectionRange(m.start + tag.length)
    st.mention = null; compose.changed(); saveCompose(); redraw()
  }
  const answering = new Set()
  const cards = createPermissionCards({ schedule: redraw, answering, answerPermission: async (requestId, behavior, extra, { onCard = false } = {}) => {
    const o = st.open, p = o?.permissions.get(requestId)
    if (!o || !p || answering.has(requestId)) return false
    answering.add(requestId); o.error = ''; redraw()
    try {
      if (!p.session_id) throw new Error('The runtime did not identify the session for this request.')
      await transport('POST', `/sessions/${encodeURIComponent(p.session_id)}/permissions/${encodeURIComponent(requestId)}`, { behavior, ...extra })
      o.permissions.delete(requestId); forgetAnswers(o, requestId); return true
    } catch (e) {
      if (e.status === 404) { o.permissions.delete(requestId); forgetAnswers(o, requestId); return true }
      if (onCard && e.status === 400) return e.message
      o.error = e.message; return false
    } finally { answering.delete(requestId); redraw() }
  } })
  function forgetAnswers(o, requestId) { cards.questionDrafts.delete(requestId) }
  function permission(p) {
    return h('div', { class: 'approve tt-approval', key: `permission:${p.request_id}` },
      h('div', { class: 'kicker', text: name(p.persona) }),
      p.kind === 'question' ? h('div', { class: 'question', role: 'form', 'aria-label': 'A question from the agent' }, ...cards.questionBody(p))
        : cards.askRow(p, { agentName: name(p.persona) }, ''))
  }
  async function markRead(o) {
    if (!visible()) { o.readOnShow = true; return }
    if (o.marking || o.readFailed || o.loading || o.creating || o.createFailed) return
    o.marking = true
    try { await transport('PATCH', path(o.thread.id), { seen: true }); refreshList() }
    catch { /* Reading must never block the conversation; retry on reaching its end. */ }
    finally { o.marking = false }
  }
  async function earlier({ context = false } = {}) {
    const o = st.open
    if (!o || (!o.has_more && !o.gapBefore) || o.moreBusy || o.refreshing || o.loading) return
    if (!context && o.atEnd !== false) { o.atEnd = false; o.readingPosition = readingAnchor(dom.querySelector('.tt-stream')) }
    o.moreBusy = true; o.error = ''; o.historyError = ''; redraw()
    try {
      const before = o.gapBefore ?? o.messages.find((m) => !m.streaming)?.id
      const body = await (o.quotes?.page(before) ?? transportHistory(o, before ?? ''))
      if (st.open !== o) return
      rememberReading(o)
      const existing = new Set(o.messages.map((m) => m.id))
      const extra = body.messages.filter((m) => !existing.has(m.id))
      const joined = mergeHistory(o.messages, body.messages, o.gapBefore)
      o.messages = joined.messages
      refreshCreationCards(extra)
      if (o.gapBefore) {
        o.gapBefore = body.has_more && !joined.connected && extra.length ? body.messages[0].id : null
        if (joined.extendsStart) o.has_more = body.has_more
      } else {
        o.has_more = body.has_more && extra.length > 0
      }
      updateOpening(o)
      if (!context) o.needsContext = false
    } catch (e) { o.historyError = e.message; o.needsContext = false; o.gapBefore = null; o.landing = false }
    finally { o.moreBusy = false; redraw() }
  }
  const pinRequests = new Set()
  let press, ignoreRowClick
  function endRowPress() { clearTimeout(press?.timer); press = null }
  async function pinRow(t) {
    if (isGeneral(t) || pinRequests.has(t.id)) return
    const selector = `[data-pin-row="${CSS.escape(t.id)}"]`
    const before = dom.querySelector(selector)?.getBoundingClientRect()
    const focusPin = dom.activeElement?.classList?.contains('tt-row-pin')
    pinRequests.add(t.id); st.rowMenu = null; redraw()
    try {
      if (st.open?.thread.id === t.id) await patch({ pinned: !t.pinned })
      else {
        const row = readThreadSummary(await transport('PATCH', path(t.id), { pinned: !t.pinned }))
        if (row.id !== t.id || row.pinned === t.pinned) throw new Error('The pin could not be changed.')
        listingRequest++; updateRow(row)
      }
      requestAnimationFrame(() => {
        const el = dom.querySelector(selector)
        if (focusPin) el?.querySelector('.tt-row-pin')?.focus({ preventScroll: true })
        if (el && before && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
          const offset = Math.max(-32, Math.min(32, before.top - el.getBoundingClientRect().top))
          el.animate([{ transform: `translateY(${offset}px)`, opacity: .7 }, { transform: 'none', opacity: 1 }], { duration: 170, easing: 'ease-out' })
        }
      })
    } catch (error) { st.error = error.message }
    finally { pinRequests.delete(t.id); redraw() }
  }
  function threadRow(t, { unread = true } = {}) {
    if (!unread) t = { ...t, unread: 0 }
    const options = activityOptions(t), activity = threadActivity(t, options)
    const subtitle = threadSubtitle(t, name, options)
    const live = activity.asking.length ? null : activity.working.length ? 'working' : activity.waiting.length ? 'waiting' : null
    const quiet = subtitle === 'Quiet'
    const current = st.wide && st.open?.thread.id === t.id
    const selected = current || (!st.wide && st.returning === t.id)
    const pinLabel = t.pinned ? 'Unpin thread' : 'Pin thread'
    const actions = () => { endRowPress(); st.rowMenu = t.id; redraw() }
    const row = h('button', { class: `srow tt-row${quiet ? ' tt-quiet' : ''}${selected ? ' on' : ''}`, 'data-activity': live, key: t.id, title: t.title || 'New thread', 'aria-current': current ? 'page' : null, 'aria-description': subtitle,
      oncontextmenu: e => { if (!isGeneral(t)) { e.preventDefault(); actions() } },
      onpointerdown: e => {
        endRowPress(); ignoreRowClick = null
        if (e.pointerType !== 'touch' || isGeneral(t)) return
        press = { x: e.clientX, y: e.clientY, timer: setTimeout(() => { ignoreRowClick = t.id; actions() }, 500) }
      },
      onpointermove: e => { if (press && Math.hypot(e.clientX - press.x, e.clientY - press.y) > 8) endRowPress() },
      onpointerup: endRowPress, onpointercancel: endRowPress,
      onclick: event => {
        if (ignoreRowClick === t.id || st.rowMenu === t.id) { ignoreRowClick = null; return }
        if (event.altKey && side()?.available()) side().openSide({ kind: 'thread', id: t.id })
        else open(t.id)
      } },
      rowTop({ face: h('div', { class: 'avatar-wrap' }, threadFace(t, 52)), title: t.title || 'New thread', subtitle: quiet ? null : subtitle,
        meta: [h('span', { class: 'srow-time' },
          (t.last?.at ?? t.created) ? h('time', { datetime: t.last?.at ?? t.created, title: formatDateTime(t.last?.at ?? t.created), text: when(t.last?.at ?? t.created) }) : null),
          h('span', { class: 'tt-row-notification' }, unread && t.unread ? h('span', { class: 'tt-unread', role: 'img', 'aria-label': 'Unread' }) : null)] }),
      side()?.available() ? h('span', { class: 'row-side-action', role: 'button', tabindex: '0', title: 'Open to the side', 'aria-label': `Open ${t.title || 'thread'} to the side`,
        onclick: event => { event.stopPropagation(); side().openSide({ kind: 'thread', id: t.id }) },
        onkeydown: event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); event.stopPropagation(); side().openSide({ kind: 'thread', id: t.id }) } },
      }, icon.panelRight(16)) : null,
      side()?.visible() && side().st.side?.kind === 'thread' && side().st.side.id === t.id ? h('span', { class: 'row-side-mark', title: 'Open in side panel', 'aria-label': 'Open in side panel' }, icon.panelRight(13)) : null,
      registry.draw('thread.list.decoration', { thread: t, open: () => open(t.id) }),
      t.default ? h('span', { class: 'sr-only', text: `Default: ${name(t.default)}` }) : null)
    if (isGeneral(t)) return row
    return h('div', { key: t.id, class: 'tt-row-wrap', 'data-pin-row': t.id }, row,
      round(pinLabel, icon.pin(16), () => pinRow(t), { class: 'iconbtn tt-row-pin', disabled: pinRequests.has(t.id), 'aria-label': `${pinLabel}: ${t.title}` }),
      st.rowMenu === t.id ? menuSurface(h('div', { class: 'popover', role: 'menu' },
        settingsRow({ title: pinLabel, lead: icon.pin(18), onclick: () => pinRow(t), props: { role: 'menuitem' } })),
      { title: t.title, anchor: `[data-pin-row="${CSS.escape(t.id)}"] .tt-row`, close: () => { st.rowMenu = null; redraw() } }) : null)
  }
  function rememberReading(o) {
    if (o.atEnd !== false) return
    const pane = dom.querySelector?.('.tt-stream')
    if (!pane) return
    if (pane.closest && pane.closest('.tt-thread')?.__key !== `thread:${o.thread.id}`) return
    o.replyAnchor ??= readingAnchor(pane) ?? o.readingPosition
  }
  function activityRows(agent) {
    const rows = st.threads.filter((t) => !t.archived && (!agent || t.members.some((m) => m.persona === agent)))
    return { pinned: rows.filter((t) => t.pinned), recent: rows.filter((t) => !t.pinned) }
  }
  function personaMenu() {
    const agent = catalog().find((a) => a.id === st.personaMenu)
    if (!agent) return null
    const close = () => { st.personaMenu = null; redraw() }
    const rows = st.threads.map((t) => st.open?.thread.id === t.id ? st.open.thread : t)
      .sort((a, b) => String(b.last?.at ?? b.created ?? '').localeCompare(String(a.last?.at ?? a.created ?? '')))
    const working = rows.find((t) => threadActivity(t, activityOptions(t)).working.some((m) => m.persona === agent.id))
    const replied = rows.flatMap((t) => [t.members.find((m) => m.persona === agent.id)?.last_spoke_at,
      t.last?.author === agent.id ? t.last.at : null]).filter(Boolean).sort().at(-1)
    const description = [agent.description, agent.role].find((text) => typeof text === 'string' && text.trim())?.trim()
    const current = () => button([
      h('span', { class: 'tt-persona-working-mark', 'aria-hidden': 'true' }),
      h('span', { class: 'tt-persona-current-label', text: `Working in ${working.title || 'New thread'}` }),
    ], () => { close(); open(working.id) }, { class: 'quietlink tt-persona-current' })
    return menuSurface(h('section', { class: 'sheet tt-persona-card', key: `persona-menu:${agent.id}`, id: 'thread-persona-menu',
      role: 'dialog', 'aria-describedby': 'agent-card-description' },
      h('div', { class: 'sheet-who tt-persona-hero' }, avatar(agent.id, 56),
        h('div', { class: 'tt-persona-about' }, h('h2', { class: 'display', text: agent.name }),
          h('div', { id: 'agent-card-description', class: 'tt-persona-description' }, !description && working ? current()
            : h('p', { text: description || (replied ? `Last reply · ${when(replied)}` : 'Start a thread together') })))),
      h('div', { class: 'tt-persona-actions' },
        button([icon.pencil(16), 'New thread'], () => { close(); create(agent.id) }, { class: 'pillbtn primary' }),
        button('Profile', () => { close(); openProfile(agent.id) }, { class: 'pillbtn content-mark' })),
      description && working ? current() : null),
    { title: agent.name, heading: false, width: 340, anchor: `[data-agent-menu=${JSON.stringify(agent.id)}]`, close })
  }
  function list(...args) { return registry.component('core.thread-list', { args, state: st, defaultView: () => builtinList(...args) }) }
  const lineKey = 'loam.chats.lineHeight.'
  function readLineHeight(layout) {
    try {
      const height = Number(localStorage.getItem(lineKey + layout))
      if (Number.isFinite(height) && height > 0) return height
    } catch {}
    return 0
  }
  const lineHeights = { compact: readLineHeight('compact'), wide: readLineHeight('wide') }
  function rememberLineHeight(layout, height) {
    if (lineHeights[layout] === height) return
    lineHeights[layout] = height
    try {
      if (height) localStorage.setItem(lineKey + layout, String(height))
      else localStorage.removeItem(lineKey + layout)
    } catch {}
  }
  let observedLine, lineObserver
  function observeLine(slot) {
    if (observedLine === slot) return
    lineObserver?.disconnect()
    observedLine = slot
    if (!slot || !globalThis.ResizeObserver) return
    lineObserver ??= new ResizeObserver(([{ target }]) => {
      const layout = target.dataset.lineLayout
      if (!target.isConnected || !layout) return
      const height = layoutPixels(target.getBoundingClientRect().height)
      // Keep enough precision to recover the same edge after CSS zoom.
      if (height > 0) rememberLineHeight(layout, Math.ceil(height * 1000) / 1000)
    })
    lineObserver.observe(slot)
  }
  const headerActionsKey = 'loam.thread.headerActions.'
  const HEADER_ACTIONS_LIMIT = 200
  function readHeaderActionsWidths(layout) {
    const out = new Map()
    try {
      const raw = JSON.parse(localStorage.getItem(headerActionsKey + layout) || '{}')
      if (raw && typeof raw === 'object') {
        for (const [id, val] of Object.entries(raw)) {
          const width = Number(val)
          if (id && Number.isFinite(width) && width > 0) out.set(id, width)
        }
      }
    } catch {}
    return out
  }
  const headerActionsWidths = {
    compact: readHeaderActionsWidths('compact'),
    wide: readHeaderActionsWidths('wide'),
  }
  function rememberHeaderActionsWidth(layout, threadId, width) {
    if (!threadId) return
    const map = headerActionsWidths[layout]
    const clean = width > 0 ? Math.ceil(width * 1000) / 1000 : 0
    if (clean > 0) {
      if (map.get(threadId) === clean) return
      map.delete(threadId)
      map.set(threadId, clean)
      while (map.size > HEADER_ACTIONS_LIMIT) map.delete(map.keys().next().value)
    } else {
      if (!map.has(threadId)) return
      map.delete(threadId)
    }
    try {
      if (map.size) localStorage.setItem(headerActionsKey + layout, JSON.stringify(Object.fromEntries(map)))
      else localStorage.removeItem(headerActionsKey + layout)
    } catch {}
  }
  let observedHeaderActions, observedHeaderActionsKey, headerActionsObserver
  function observeHeaderActions(slot) {
    const key = slot ? `${slot.dataset.headerActionsLayout ?? ''}:${slot.dataset.headerActionsThread ?? ''}` : ''
    if (observedHeaderActions === slot && observedHeaderActionsKey === key) return
    headerActionsObserver?.disconnect()
    observedHeaderActions = slot
    observedHeaderActionsKey = key
    if (!slot || !globalThis.ResizeObserver) return
    headerActionsObserver ??= new ResizeObserver(([{ target }]) => {
      const layout = target.dataset.headerActionsLayout
      const threadId = target.dataset.headerActionsThread
      if (!target.isConnected || !layout || !threadId) return
      const width = layoutPixels(target.getBoundingClientRect().width)
      if (width > 0) rememberHeaderActionsWidth(layout, threadId, width)
    })
    headerActionsObserver.observe(slot)
  }
  function headerActionsSlot(o, controls) {
    const layout = st.wide ? 'wide' : 'compact'
    const threadId = o.thread.id
    const pending = headerActionPending()
    const reserved = headerActionsWidths[layout].get(threadId) ?? 0
    const items = pending ? [] : controls('header').filter(Boolean)
    if (!pending && !items.length) {
      rememberHeaderActionsWidth(layout, threadId, 0)
      return null
    }
    if (!items.length) {
      if (!reserved) return null
      return h('div', { class: 'tt-header-actions', key: 'header-actions', 'aria-hidden': 'true',
        'data-header-actions-pending': 'true', style: { width: `${reserved}px` } })
    }
    return h('div', { class: 'tt-header-actions', key: 'header-actions',
      'data-header-actions-layout': layout, 'data-header-actions-thread': threadId }, ...items)
  }

  function listLine() {
    const layout = st.wide ? 'wide' : 'compact'
    const pending = linePending()
    const line = pending ? null : registry.draw('thread.list.line')[0]
    if (!pending && !line) rememberLineHeight(layout, 0)
    // The rows can paint while the provider and its initial CSS settle. Only
    // an empty slot needs a fixed height. A live one can size to its contents.
    return h('div', { class: 'tt-chats-line', key: 'thread-list-line', 'data-line-layout': line ? layout : null,
      'aria-hidden': line ? null : 'true', style: line ? null : { height: `${lineHeights[layout]}px` } }, line)
  }
  function builtinList(wide = st.wide) {
    st.wide = wide

    const rows = [...st.threads].sort((a, b) => compareThreads(a, b, { pinned: !st.archived })).filter((t) => Boolean(t.archived) === Boolean(st.archived)).filter((t) => `${t.title} ${t.last?.text ?? ''}`.toLowerCase().includes(st.query.toLowerCase()))
    if (archiveQuery !== st.query) { archiveQuery = st.query; archiveLimit = archivePage; st.resetListScroll = true }
    const visible = st.archived ? rows.slice(0, archiveLimit) : rows
    return h('section', { class: `${st.wide ? 'listcol' : 'screen home'} tt-list${st.wide && collapsed() ? ' away' : ''}`, inert: st.wide && collapsed() ? true : null, 'aria-hidden': st.wide && collapsed() ? 'true' : null, 'aria-label': 'Threads' },
      listHeading({ wide: st.wide, title: st.archived ? 'Archived' : 'Chats',
        leading: st.archived ? historyButton('Back to Chats', 'list') ?? round('Back to Chats', icon.chevronLeft(18), () => { if (!archiveAddress(false)) showArchived(false) }) : null,
        props: { class: `${st.wide ? 'list-head' : 'home-head'}${st.archived ? ' tt-archived-head' : ''}` }, actions: [
        !st.archived ? round('New thread', icon.pencil(18), () => create(), { class: `iconbtn${listIsMain() && !st.open && !st.personaMenu ? ' secondary' : ''}`, disabled: st.creating }) : null,
        round('Search threads', icon.search(18), () => { st.searchOpen = !st.searchOpen; st.focusSearch = st.searchOpen; redraw() }, { 'aria-expanded': String(Boolean(st.searchOpen)) }),
        ...(!st.archived ? registry.draw('thread.list.action', { wide: st.wide, archived: false, state: st }) : []),
        !st.archived ? round('Thread list actions', icon.dots(18), () => { st.personaMenu = null; st.listMenu = !st.listMenu; redraw() }, { 'aria-haspopup': 'menu', 'aria-expanded': String(Boolean(st.listMenu)) }) : null,
        st.wide && !st.archived ? round('Hide sidebar for a focused view', icon.panelLeft(18), () => collapse(true)) : null] }),

      st.listMenu ? menuSurface(h('div', { class: 'tt-list-menu popover', key: 'thread-list-menu', role: 'menu' }, settingsRow({ title: st.personasHidden ? 'Show agents' : 'Hide agents', lead: icon.users(18), onclick: () => { st.personasHidden = !st.personasHidden; st.listMenu = false; redraw() } }), settingsRow({ title: 'New thread', lead: icon.pencil(18), onclick: () => { st.listMenu = false; create() } }),
        settingsRow({ title: 'Archived threads', lead: icon.folder(18), right: h('span', { class: 'muted', text: String(st.threads.filter(t => t.archived).length) }), onclick: () => { if (!archiveAddress(true)) showArchived(true) } })), { title: 'Thread list actions', anchor: '[aria-label="Thread list actions"]', close: () => { st.listMenu = false; redraw() } }) : null,
      st.fixture ? h('div', { class: 'tt-fixture' }, 'Design preview · sample threads', button('Exit', exitPreview, { class: 'quietlink tt-preview-exit' })) : null,
      !st.wide ? registry.draw('app.overlay', { slot: 'player' }) : null,
      h('div', { class: 'scroll', key: 'thread-list-scroll' },
        st.archived ? null : listLine(),
        !st.archived && !st.personasHidden ? personaGrid({ agents: catalog(), loading: fixtureAgents === null && runtimeAgents === null && !agentsReady(), wide: st.wide, look, disabled: st.creating,
          onclick: (a) => { st.listMenu = false; st.personaMenu = st.personaMenu === a.id ? null : a.id; redraw() }, label: (a) => `${a.name} menu`,
          buttonProps: (a) => ({ type: 'button', 'data-agent-menu': a.id, 'aria-haspopup': 'dialog', 'aria-expanded': String(st.personaMenu === a.id), 'aria-controls': st.personaMenu === a.id ? 'thread-persona-menu' : null }),
          props: { class: 'strip tt-personas' } }) : null,
        !st.archived ? personaMenu() : null,

        st.searchOpen ? h('div', { class: 'search tt-search' }, icon.search(16), h('input', { type: 'search', placeholder: 'Find a thread', 'aria-label': 'Find a thread', value: st.query, oninput: (e) => { st.query = e.target.value; redraw() } }), round('Close search', icon.close(12), () => { st.searchOpen = false; st.query = ''; redraw() })) : null,
        st.error ? h('div', { class: 'banner', role: 'alert', text: st.error }) : null,
        h('div', { class: 'tt-rows', key: 'thread-rows' }, selection.view(),
          !st.archived && visible.some((t) => t.pinned) ? [h('h2', { class: 'group-kicker kicker tt-section-heading', text: 'Pinned' }), h('div', { class: 'tt-section', key: 'pinned' }, ...visible.filter((t) => t.pinned).map(threadRow))] : null,
          visible.some((t) => st.archived || !t.pinned) ? [h('h2', { class: 'group-kicker kicker tt-section-heading', text: st.archived ? 'Archived threads' : 'Recent' }), h('div', { class: 'tt-section', key: 'recent' }, ...visible.filter((t) => st.archived || !t.pinned).map(threadRow))] : null),
        // With no threads at all, the wide layout's pane already says so; the
        // list column does not say it twice.
        !rows.length && !(st.wide && !st.query && !st.archived && !st.loading) ? (st.loading ? emptyConversation({ title: 'Opening chats…', body: null })
          : emptyState({ object: st.query ? 'search' : st.archived ? 'archive' : 'speech', title: st.query ? 'No threads match' : st.archived ? 'No archived threads' : 'Room for a new thought', body: st.query ? 'Try another word.' : st.archived ? 'Threads you archive will appear here.' : 'Pick an agent above or start a thread.' })) : null,
        st.archived && visible.length < rows.length ? button('Load more archived threads', moreArchived, { class: 'quietlink tt-archive-more' }) : null))
  }
  function waitingLine(id, props = {}) {
    const now = Date.now()
    if (!awaitingSince.has(id)) awaitingSince.set(id, now)
    const wait = threadWaiting(st.open, id, name(id), now - awaitingSince.get(id))
    if (!wait) { awaitingSince.delete(id); return null }
    if (wait.nextMs !== null && now + wait.nextMs < awaitDue) {
      clearTimeout(awaitTimer); awaitDue = now + wait.nextMs
      awaitTimer = setTimeout(() => { awaitTimer = null; redraw() }, Math.max(16, wait.nextMs))
    }
    return thinkingLine({ key: `awaiting:${id}`, ...wait, props })
  }
  function clearTaskTimers(o) {
    if (!o) return
    for (const key of ['taskClock', 'taskFade', 'taskEmpty']) { clearTimeout(o[key]); o[key] = null }
  }
  function closeTasks() {
    const o = st.open
    if (!o) return
    o.tasksOpen = false; o.selectedTask = null; o.focusTasksReturn = true
    clearTimeout(o.taskClock); o.taskClock = null
    clearTimeout(o.taskEmpty); o.taskEmpty = null
    redraw()
  }
  function showTaskReply(id, kind = 'task') {
    const o = st.open
    closeTasks()
    o.focusTasksReturn = false
    clearTimeout(o.replyHighlightTimer)
    Object.assign(o, { replyJump: id, replyJumpFocus: true, jumpKind: kind, highlightedReply: null, savedAnchor: null, landAt: null,
      openingBoundary: null, replyAnchor: null, taskReplyAnchor: null, landing: false, atEnd: false, needsContext: false, historyError: '', error: '' })
    follow.release()
    redraw()
  }
  const replyAuthor = (message) => isOwner(message.author) ? 'You' : name(message.author)
  const replyTone = (message) => isOwner(message.author) ? 'var(--latte-espresso)' : look(message.author).color
  function replyFace(message) {
    return isOwner(message.author) ? h('span', { class: 'tt-you-face', 'aria-hidden': 'true', text: 'Y' }) : avatar(message.author, 22)
  }
  function startReply(message) {
    const o = st.open
    if (!o || o.sending || !canReply(message)) return
    rememberReading(o)
    o.replyTo = message; o.focusReplyDraft = true
    saveCompose(); redraw()
  }
  function cancelReply() {
    const o = st.open
    if (!o || o.sending || o.creating) return
    rememberReading(o)
    const source = o.replyTo?.thread
    o.replyTo = null; o.focusReplyDraft = true
    saveCompose(); redraw()
    if (source) patch({ draft_reply: null })
  }
  const quoteThread = (source) => st.threads.find((row) => row.id === source.thread)?.title ?? source.title
  function quoteHeading(message, { composing = false, source = null } = {}) {
    return h('strong', {}, composing ? `Replying to ${replyAuthor(message)}` : replyAuthor(message),
      source ? h('small', { class: 'tt-quote-thread', text: ` · ${quoteThread(source)}` }) : null)
  }
  function composingReply() {
    const o = st.open, m = o.replyTo
    return m ? h('div', { class: 'tt-reply-chip', key: 'reply-chip', role: 'group',
      'aria-label': `Replying to ${replyAuthor(m)}`, style: { '--quote-tone': replyTone(m) } },
      replyFace(m), h('div', { class: 'tt-quote-copy' }, quoteHeading(m, { composing: true, source: m.thread ? m : null }), h('span', { text: quoteLine(m) })),
      round(m.thread ? 'Remove quote' : 'Cancel reply', icon.close(12), cancelReply, { onpointerdown: (e) => e.preventDefault(), disabled: o.sending || o.creating })) : null
  }
  function messageQuote(message) {
    if (!message.reply_to) return null
    const o = st.open, source = message.reply_source
    const original = source ?? o.messages.find((m) => m.id === message.reply_to) ?? o.quotes?.get(message.reply_to)
    if (!canReply(original)) return h('div', { class: 'tt-quote tt-quote-missing', text: 'Earlier message' })
    const contents = [quoteHeading(original, { source }), h('span', { text: quoteLine(original) })]
    const props = { class: 'tt-quote', 'aria-label': `Jump to ${replyAuthor(original)}${source ? ` in ${quoteThread(source)}` : ''}: ${quoteLine(original)}`, style: { '--quote-tone': replyTone(original) } }
    return source
      ? h('a', { ...props, href: `${path(source.thread)}?message=${encodeURIComponent(original.id)}`, onclick: (e) => followThreadLink(e, { thread: source.thread, message: original.id }) }, contents)
      : button(contents, () => showTaskReply(original.id, 'quote'), props)
  }
  function branchLinks(message) {
    const links = st.open.branches.filter((row) => row.branched_from.message === message.id)
    return links.length ? h('div', { class: 'tt-branch-links' }, ...links.map((row) =>
      h('a', { key: row.id, class: 'tt-branch-link', href: path(row.id), onclick: (e) => followThreadLink(e, { thread: row.id }), text: `branched → ${row.title}` }))) : null
  }
  async function copyMessage(message) {
    const o = st.open
    // An agent's pictures are not words: Copy takes the words around them.
    const pictures = isOwner(message.author) ? null : replyImages(message)
    const copied = await copyText(messageSegments(message).filter((p) => p.text).map((p) => pictures ? pictures.words(p.text) : p.text).filter((t) => t?.trim()).join('\n\n') || quoteLine(message))
    if (!copied || st.open !== o) return
    clearTimeout(o.copyMessageTimer)
    o.copiedMessage = message.id; redraw()
    o.copyMessageTimer = setTimeout(() => {
      o.copiedMessage = null
      if (st.open === o) redraw()
    }, 1000)
  }
  function syncTasks(o) {
    const { count, tasks } = workFor(o)
    if (count || tasks.length) {
      o.taskCount = count
      o.taskVisible = true
      clearTimeout(o.taskFade); o.taskFade = null
      clearTimeout(o.taskEmpty); o.taskEmpty = null
    } else {
      if ((o.taskVisible || o.taskCount) && !o.taskFade) o.taskFade = setTimeout(() => {
        o.taskVisible = false; o.taskCount = 0; o.taskFade = null; if (st.open === o) redraw()
      }, 240)
      if (o.tasksOpen && !o.taskEmpty) o.taskEmpty = setTimeout(() => {
        o.taskEmpty = null; if (st.open === o) closeTasks()
      }, 1400)
    }
  }
  function taskIndicator() {
    const o = st.open, { count, tasks } = workFor(o), visible = count || tasks.length
    if (!o.taskVisible && !o.taskCount) return null
    const label = count ? `${count}${tasks.some((task) => task.kind === 'job') ? '' : ' background'} ${count === 1 ? 'task' : 'tasks'}` : 'Tasks · recently finished'
    return round(label, [icon.play(16), o.taskCount ? h('span', { class: 'tt-task-count', text: o.taskCount }) : null], (e) => {
      o.tasksOpen = true; o.selectedTask = null; o.focusTasks = true; o.headerMenu = false; redraw()
    }, { key: 'task-indicator', class: `iconbtn ghost tt-tasks-button${visible ? '' : ' leaving'}`, disabled: !visible,
      'aria-haspopup': 'dialog', 'aria-expanded': String(Boolean(o.tasksOpen)), 'aria-controls': elementId('thread-background-tasks') })
  }
  function taskPanel() {
    const o = st.open, { count, tasks } = workFor(o), visible = count || tasks.length
    const selected = tasks.find((task) => task.kind !== 'job' && task.id === o.selectedTask?.id && task.persona === o.selectedTask.persona)
    const back = selected ? () => { o.selectedTask = null; o.focusTaskRow = `${selected.persona}:${selected.id}`; redraw() } : closeTasks
    const date = (value) => taskElapsed(value) === null ? null : formatDateTime(value)
    if (visible && tasks.some((task) => task.kind === 'job' || date(task.started_at)) && !o.taskClock) {
      o.taskClock = setTimeout(() => { o.taskClock = null; if (st.open === o && o.tasksOpen) redraw() }, 60000)
    }
    return [h('button', { key: 'tasks-scrim', class: `tt-tasks-scrim ${st.wide ? 'popscrim' : 'scrim'}`, 'aria-label': 'Close tasks', tabindex: '-1', onclick: closeTasks }),
      menuSurface(h('section', { key: 'tasks-panel', id: elementId('thread-background-tasks'), class: `${st.wide ? 'popover' : 'sheet'} tt-tasks-panel`, role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Tasks',
        onkeydown: (e) => {
          if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeTasks() }
          if (e.key === 'Tab') {
            const controls = [...e.currentTarget.querySelectorAll('button:not(:disabled)')], at = controls.indexOf(document.activeElement)
            if (e.shiftKey && at <= 0) { e.preventDefault(); controls.at(-1)?.focus() }
            else if (!e.shiftKey && at === controls.length - 1) { e.preventDefault(); controls[0]?.focus() }
          }
        } }, !st.wide ? sheetHandle(closeTasks) : null,
        h('div', { class: 'tt-tasks-head' }, selected ? round('Back to task list', icon.chevronLeft(18), back) : null,
          h('h2', { class: 'display', tabindex: '-1', text: selected ? 'Task details' : 'Tasks' }),
          st.wide ? round('Close tasks', icon.close(14), closeTasks) : null),
        h('div', { class: 'tt-tasks-body' }, !visible ? h('p', { class: 'tt-tasks-empty', role: 'status', text: 'Nothing running' }) : selected ?
          h('div', { class: 'tt-task-details' }, h('div', { class: 'tt-task-who' }, avatar(selected.persona, 24), h('strong', { text: name(selected.persona) })),
            h('p', { class: 'tt-task-full', text: selected.description || 'Background work' }),
            h('p', { class: 'muted', text: date(selected.started_at) ? `Started ${date(selected.started_at)}` : 'Start time unavailable' }),
            selected.message ? button('Started in this reply', () => showTaskReply(selected.message), { class: 'quietlink tt-task-reply' }) : null) : [
          ...[...new Set(tasks.filter((task) => task.kind !== 'job').map((task) => task.persona))].map((persona) => h('section', { class: 'tt-task-group', key: persona, 'aria-label': name(persona) },
            h('h3', { class: 'tt-task-group-name' }, avatar(persona, 24), name(persona)),
            ...tasks.filter((task) => task.kind !== 'job' && task.persona === persona).map((task) => button([
              h('span', { class: 'tt-task-copy' }, h('span', { class: 'tt-task-title', text: taskTitle(task.description) })),
              taskElapsed(task.started_at) !== null ? h('time', { datetime: task.started_at, title: `Started ${date(task.started_at)}`, text: taskElapsed(task.started_at) }) : null,
            ], () => { o.selectedTask = { id: task.id, persona: task.persona }; o.focusTasks = true; redraw() }, { class: 'tt-task-row', key: `${task.persona}:${task.id}`, 'data-task': `${task.persona}:${task.id}` })))),
          ...tasks.filter((task) => task.kind === 'job').map(jobRow),
          count > tasks.filter((task) => task.state !== 'finished').length ? h('p', { class: 'tt-tasks-note', role: 'status', text: tasks.length ? 'Waiting for the remaining results…' : 'Waiting for results…' }) : null,
        ])), { title: 'Tasks', width: 420, heading: false, anchor: '.tt-tasks-button', close: closeTasks, back })]
  }
  function jobRow(job) {
    const elapsed = jobElapsed(job), link = job.state === 'finished' && job.receipt && openRef
    return h(link ? 'button' : 'div', { class: 'tt-task-row tt-job-row', key: `job:${job.job}`, 'data-job': job.job,
      ...(link ? { type: 'button', 'aria-label': `${job.title} · ${jobStatus(job)} · Open receipt`, onclick: () => { closeTasks(); openRef(job.receipt) } } : {}) },
      h('span', { class: 'tt-job-face', role: 'img', 'aria-label': name(job.persona) }, avatar(job.persona, 28)),
      h('span', { class: 'tt-task-copy' }, h('span', { class: 'tt-task-title', text: job.title }),
        h('span', { class: 'tt-task-name', text: job.scope ? `${job.project} ${job.scope}` : job.project }),
        h('span', { class: `tt-job-status${jobActivity(job)?.stuck ? ' tt-job-quiet' : ''}`, title: job.outcome_detail ?? (job.last_activity ? `Last engine event ${formatDateTime(job.last_activity)}` : undefined), text: jobStatus(job) })),
      elapsed !== null ? h('span', { class: 'tt-job-elapsed', 'aria-label': `Elapsed ${elapsed}`, text: elapsed }) : null,
      link ? icon.chevronRight(14) : null)
  }

  // Only the single-thread read carries jobs. Keep that reading independent
  // of list/member snapshots, which deliberately carry no job information.
  const workFor = (o) => threadBackground({ ...o.thread, ...o.jobReading })
  function readJobs(o, thread) {
    o.jobReading = { jobs: thread.jobs ?? [], jobs_error: thread.jobs_error ?? null }
    o.jobsReadAt = Date.now()
  }
  async function refreshJobs(o) {
    if (o.jobsReading || o.loading || o.refreshing || o.readFailed || o.creating || !transport) return
    o.jobsReading = true; o.jobsReadAt = Date.now()
    try {
      const body = await transport('GET', `${path(o.thread.id)}?limit=1&seen=0`)
      if (st.open !== o || body?.thread?.id !== o.thread.id) return
      readJobs(o, body.thread)
      redraw()
    } catch { /* Jobs are optional; a failed read must not disturb the thread. */ }
    finally { o.jobsReading = false }
  }
  function messageFooter(m, segments, mine) {
    if (m.queued) {
      const item = m.queued
      if (!item.error) {
        const active = item.leaseUntil > Date.now()
        const tried = item.attempts && (!active || item.attempts > 1)
        return h('div', { class: 'tt-send-status' },
          h('span', { role: 'status', text: 'Not sent yet' }),
          tried ? button('Retry', () => outbox.retry(item.order).catch(() => {
            if (st.open?.thread.id !== item.thread) return
            st.open.error = 'Couldn’t retry this message. It is still saved in this browser.'
            redraw()
          }), { class: 'quietlink', disabled: active }) : null)
      }
      return h('div', { class: 'tt-send-status' }, h('span', { role: 'alert', text: `Not sent yet. ${item.error}` }), button('Edit message', async () => {
        if (draft.value.trim() || compose.files().length) return
        draft.value = item.body.text; compose.restore(item.attachments); st.open.replyTo = item.reply
        saveCompose()
        await outbox.remove(item.order)
        draft.focus(); redraw()
      }, { class: 'quietlink', disabled: Boolean(draft.value.trim() || compose.files().length) }))
    }
    if (m.streaming || m.id === 'pending-send') return null
    const stamp = replyTimestamp(m.at)
    const turns = segments.filter((part) => part.item?.kind === 'result').map((part) => part.item)
    const facts = turns.map((item) => stepUI?.facts?.(item, m.engine) ?? []).filter((bits) => bits.length)
    const failed = turns.some((item) => item.isError)
    const opened = st.open.turnDetails?.has(m.id)
    const time = stamp ? h('time', { class: 'tt-message-time', datetime: stamp.dateTime, title: stamp.title, text: stamp.text }) : null
    const trailing = facts.length ? button(time ?? 'Details', () => {
      st.open.turnDetails ??= new Set()
      if (st.open.turnDetails.has(m.id)) st.open.turnDetails.delete(m.id); else st.open.turnDetails.add(m.id)
      redraw()
    }, { class: `tt-time-button${failed ? ' err' : ''}`, title: stamp?.title ?? 'Recorded turn details', 'aria-label': `${failed ? 'Failed turn. ' : ''}${stamp ? `${stamp.title}. ` : ''}${opened ? 'Hide' : 'Show'} turn details`, 'aria-expanded': String(Boolean(opened)) }) : time
    const action = (label, glyph, onclick, props = {}) => {
      glyph.setAttribute('aria-hidden', 'true')
      return button(glyph, onclick, { class: `tt-footer-action tt-footer-${label.toLowerCase()}`, 'aria-label': label,
        title: (globalThis.matchMedia?.('(hover:hover)').matches ?? (globalThis.innerWidth ?? 0) >= 900) ? label : null, ...props })
    }
    const leading = canReply(m) ? h('span', { class: 'tt-footer-leading' },
      registry.draw('message.footer.action', { slot: 'reply', message: m, thread: st.open.thread, defaultView: () => action('Reply', icon.reply(15), () => startReply(m), { disabled: st.open.sending, onpointerdown: (e) => e.preventDefault() }) }),
      registry.draw('message.footer.action', { slot: 'branch', message: m, thread: st.open.thread, defaultView: () => action('Branch into a new thread', icon.branch(15), () => beginBranch(m), { class: 'tt-footer-action tt-footer-branch', 'aria-haspopup': 'dialog', 'aria-expanded': String(st.open.branch?.id === m.id) }) }),
      registry.draw('message.footer.action', { slot: 'copy', message: m, thread: st.open.thread, defaultView: () => action('Copy', st.open.copiedMessage === m.id ? icon.check(15) : icon.copy(15), () => copyMessage(m), { 'data-copied': st.open.copiedMessage === m.id ? '' : null }) }),
      registry.draw('message.footer.action', { slot: 'leading', message: m, thread: st.open.thread })) : null
    const rows = registry.reduce('message.footer.action', { slot: 'row', message: m, mine, leading, trailing, thread: st.open.thread, agent: catalog().find(a => a.id === m.author), agentName: name(m.author), defaultView: () => leading || trailing ? [h('div', { class: 'turnrow' }, leading, trailing)] : [] }, [])
    if (!rows.length) return null
    return h('div', { class: 'tt-reply-footer' }, rows,
      opened ? h('div', { class: 'tt-turn-facts', role: 'region', 'aria-label': 'Turn details' }, ...facts.map((bits) => h('div', { text: bits.join(' · ') }))) : null)
  }
  function creationCard(m) {
    const id = m.agent_created
    const roster = createAgents((fixtureAgents ?? agents()).filter(agent => !agent.archived))
    const current = roster.byId.get(id)
    return h('div', { class: 'card tt-agent-created', key: m.id, 'data-message-id': m.id },
      current ? blob(agentLook(id, roster), { size: 40, font: 18, decorative: true }) : null,
      h('div', { class: 'tt-agent-created-copy' },
        h('span', { class: 'tt-agent-created-label', text: 'Agent created' }),
        current || agentsReady() ? h('strong', { text: current?.name ?? 'No longer here' }) : null),
      current ? button('Start a chat', () => create(id), { class: 'pillbtn secondary', 'aria-label': `Start a chat with ${current.name}` }) : null)
  }
  function message(...args) { return registry.component('core.message', { args, message: args[0], thread: st.open?.thread, state: st, defaultView: () => builtinMessage(...args) }) }
  function builtinMessage(m) {
    const context = { message: m, thread: st.open.thread, state: st }
    const renderer = registry.entries('message.renderer', context).findLast(entry => !entry.component)
    if (renderer) return registry.invoke(renderer, 'render', context)
    if (m.agent_created) return creationCard(m)
    if (m.kind === 'system') {
      const names = m.joins?.names.map(name), joined = names?.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : null
      const key = `system:${st.open.thread.id}:${m.id}`, shown = openMessages.has(key)
      const id = `system-${encodeURIComponent(st.open.thread.id)}-${encodeURIComponent(m.id)}`
      const words = (raw) => formatSystemLine(systemText(raw)).map(part => part.text).join('')
      const content = typeof m.link?.thread === 'string'
        ? h('a', { class: 'tt-branch-link', href: `${path(m.link.thread)}${m.link.message ? `#${encodeURIComponent(m.link.message)}` : ''}`, onclick: (e) => followThreadLink(e, m.link), text: words(m.text) })
        : joined ? `${name(m.joins.actor)} brought in ${joined}.` : words(m.text ?? `${m.actor ? name(m.actor) : 'You'} brought in ${name(m.member_joined)}`)
      return h('div', { class: `exitline tt-system${shown ? ' expanded' : ''}`, key: m.id, 'data-message-id': m.id, 'data-join-count': names?.length },
        h('div', { class: 'tt-system-text', id, 'data-system-key': key }, content),
        longSystemLines.has(key) ? button(shown ? 'Show less' : 'Show more', () => { if (shown) openMessages.delete(key); else openMessages.add(key); redraw() },
          { class: 'content-mark tt-system-toggle', 'aria-expanded': String(shown), 'aria-controls': id }) : null)
    }
    const mine = isOwner(m.author) || m === st.open.pending
    const permissions = [...st.open.permissions.values()].filter((p) => p.message_id === m.id)
    const viewKey = `${st.open.thread.id}:${m.viewKey ?? m.id}`
    // An agent's images sit where it wrote them (replyImages); what has no
    // place in its words, and everything the owner attached, is drawn as files.
    const pictures = mine ? null : replyImages(m, viewKey, { streaming: Boolean(m.streaming) })
    const segments = messageSegments(m), lastText = segments.findLastIndex((part) => part.text?.trim())
    const files = messageFiles(pictures ? pictures.rest(segments.filter((part) => part.text).map((part) => part.text)) : m.attachments, viewKey)
    const footer = messageFooter(m, segments, mine)
    const key = `thread:${st.open.thread.id}:${m.id}`, long = longMessages.has(key), shown = openMessages.has(key)
    // Live prose is already formatted. Completion must not send it through
    // the plain-text opening placeholder and replace it a second time.
    const prose = (text, slot) => st.open.prose && !m.streaming && !m.viewKey ? kept(st.open.prose.node(text, `${m.id}:${slot}`, pictures)) : mentionMarkdown(pictures ? pictures.live(text) : text, pictures)
    // Render the listing card separately so installation changes can update it while the reply stays cached.
    const said = (text, slot) => {
      const pieces = splitCards(text)
      if (pieces.length === 1 && pieces[0].text !== undefined) return prose(text, slot)
      const from = st.open.thread.title || 'the thread'
      return pieces.map((piece, j) => piece.cards
        ? marketCards?.(piece.cards, { from, key: `${m.id}:${slot}:${j}` }) ?? h('p', { class: 'market-card-plain', key: `cards:${m.id}:${slot}:${j}`, text: piece.cards.map(plainWords).join(', ') })
        : prose(piece.text, `${slot}:${j}`))
    }
    const body = mine ? h('div', { class: 'line right' }, h('div', { class: 'mine' }, userBubble({ class: `${(m.queued || m.id === 'pending-send') ? 'pending ' : ''}tt-bubble${files ? ' with-files' : ''}${long ? ' foldable' : ''}${long && !shown ? ' clamped' : ''}`, 'data-mine': key }, files, messageQuote(m), h('div', { class: 'user-message-body' }, prose(m.text, 'person')),
      long ? button(shown ? 'Show less' : 'Show more', () => { if (shown) openMessages.delete(key); else openMessages.add(key); redraw() }, { class: 'morebtn', 'aria-expanded': String(shown) }) : null), footer))
      : segments.map((part, index) => {
        if (part.text !== undefined) return h('div', { class: 'line left', key: `text:${index}` }, agentProse([
          said(part.text, index),
          // The files with no place in its words sit under its last words,
          // above the time and the actions, as the owner's sit in the bubble.
          index === lastText ? files : null,
          index === lastText ? footer : null,
        ], { class: `say agent md${m.streaming ? ' streaming' : ''}` }))
        if (part.steps) {
          const node = stepUI?.fold(part.steps, { agentName: name(m.author) })
          if (node) node.__key = `steps:${index}`
          return node
        }
        if (part.plan) return stepUI?.tool(part.plan)
        if (part.item) return part.item.kind === 'result' ? null : stepUI?.item(part.item)
        const id = `${m.viewKey ?? m.id}:${index}`, isOpen = st.open.didOpen?.has(id)
        const step = m.streaming && (!m.text || part.concurrent) && index === segments.length - 1 ? st.open.steps?.get(m.author) : null
        const live = step && !step.waiting && st.open.thread.members.find((member) => member.persona === m.author)?.working !== false
        return stepFold({ key: `steps:${index}`, open: Boolean(isOpen), facts: { count: part.did.length, running: Boolean(live), failed: 0 }, live: live ? step.label : null, props: { class: 'line left' },
          toggle: () => { st.open.didOpen ??= new Set(); if (st.open.didOpen.has(id)) st.open.didOpen.delete(id); else { st.open.didOpen.add(id); if (!m.streaming) st.open.replySteps?.ensure(m.id) }; redraw() },
          body: part.did.map((step) => h('div', { class: 'thinking', text: systemText(step.label).replace(/\s*·\s*$/, '') })) })
      })
    const full = !m.preset && !mine && !m.streaming && !Array.isArray(m.steps), load = st.open.replySteps?.entries.get(m.id)
    const generating = m.streaming && memberGenerating(st.open, m.author)
    return h('article', { class: `tt-message${mine ? ' mine' : ''}${st.open.highlightedReply === m.id ? ' tt-reply-highlight' : ''}${canReply(m) ? ' tt-replyable' : ''}`, tabindex: '-1', key: m.viewKey ?? m.id, 'data-message-id': m.id, 'data-full-steps': full ? '' : null, 'data-live-member': generating ? m.author : null,
      onpointerdown: (e) => gesture.down(e, m), onpointermove: (e) => gesture.move(e), onpointerup: () => gesture.up(), onpointercancel: () => gesture.reset(), onclick: (e) => gesture.click(e) },
      canReply(m) ? h('span', { class: 'tt-swipe-reply', 'aria-hidden': 'true', text: '↩' }) : null,
      !mine ? messageQuote(m) : null,
      generating && !replyHasContent(m) ? waitingLine(m.author) : body,
      generating && replyHasContent(m) && (!m.text || st.open.waiting?.has(m.author) || st.open.steps?.get(m.author)?.waiting) ? waitingLine(m.author) : null,
      !mine && lastText < 0 ? files : null, !mine && lastText < 0 ? footer : null,
      load?.status === 'error' ? h('div', { class: 'tt-steps-retry muted', role: 'status' }, 'Steps couldn’t be loaded. ', button('Try again', () => st.open.replySteps.ensure(m.id, true), { class: 'quietlink' })) : null,
      ...permissions.map(permission), branchLinks(m))
  }
  // A turn that failed with the provider's error is a system line, not the
  // agent speaking it: what happened, where to fix it, the raw words behind
  // Details (failedTurnInfo).
  function failedTurnLine(m, info) {
    return h('div', { class: 'exitline tt-system tt-system-failed', key: m.id, 'data-message-id': m.id, 'data-failure': info.kind },
      h('p', { class: 'tt-failed-summary', text: info.summary }),
      h('div', { class: 'tt-failed-actions' },
        info.action === 'providers' && openSettings ? button('Go to Connections', () => openSettings('providers'), { class: 'pillbtn tt-failed-go' }) : null,
        info.kind !== 'setup' ? h('details', { class: 'tt-failed-details', open: openMessages.has(`failed:${st.open.thread.id}:${m.id}`), ontoggle: (event) => { if (event.currentTarget.isConnected) { if (event.currentTarget.open) openMessages.add(`failed:${st.open.thread.id}:${m.id}`); else openMessages.delete(`failed:${st.open.thread.id}:${m.id}`) } } },
          h('summary', { text: 'Details' }),
          h('pre', { class: 'tt-failed-raw', text: info.raw })) : null))
  }
  const engineOf = (display) => {
    const id = catalog().find((a) => a.name === display)?.id ?? String(display).toLowerCase()
    return st.open?.thread.members.find((member) => member.persona === id)?.engine ?? null
  }
  function receivedNote(o, message) {
    if (message?.unaddressed && !o.thread.default && o.note?.id !== message.id) {
      const note = o.note = { id: message.id, teach: false }
      firstThreadLesson().then(first => {
        if (first && o.note === note) { note.teach = true; redraw() }
      })
    }
    if (message?.first_tag) o.firstTag = message.id
  }
  function noteActions(m) {
    const o = st.open
    if (o.firstTag === m.id && m.first_tag && m.first_tag === o.thread.default) {
      return h('div', { class: 'tt-agent-note', role: 'status' },
        `${name(m.first_tag)} now answers this thread. `,
        button('Undo', () => patch({ default: null, if_default: m.first_tag }), { class: 'quietlink', disabled: o.patching }))
    }
    if (m.id !== o.note?.id || o.thread.default) return null
    async function choose(persona) {
      if (o.noteBusy) return
      o.noteBusy = true; o.error = ''; redraw()
      try {
        const row = await transport('POST', `${path(o.thread.id)}/answer`, { message: m.id, persona })
        updateRow(readThreadSummary(row)); o.note = null
      } catch (e) { o.error = e.message }
      finally { o.noteBusy = false; redraw() }
    }
    return h('div', { class: 'tt-agent-note', key: 'choose:' + m.id },
      o.note.teach ? h('p', { text: 'A thread can have an agent who answers by default. Tag anyone with @, or keep a thread for notes and conversations with several agents.' }) : null,
      o.note.teach || o.note.expanded ? [
        h('p', { text: 'Who should answer this message?' }),
        h('div', { class: 'profile-actions' }, ...catalog().map(a => button([avatar(a.id, 20), name(a.id)], () => choose(a.id), { disabled: o.noteBusy, 'aria-label': name(a.id) })),
          button('Keep as a note', () => { o.note = null; redraw() }, { class: 'quietlink' }))] :
        button('Ask an agent to answer', () => { o.note.expanded = true; redraw() }, { class: 'quietlink' }))
  }
  function messageRuns(messages) {
    const out = []
    let run = null
    for (const m of messages) {
      if (m.id === st.open.firstUnseen && st.open.openingUnread > 0) {
        run = null
        out.push(h('div', { class: 'tt-seen-strip tt-unread-strip', key: 'unread-boundary', 'data-unread-boundary': m.id },
          h('span', { class: 'tt-seen-count', text: `${st.open.openingUnread} new from the agents` })))
      }
      const failed = failedTurnInfo(m, { agentName: name, engineOf, providers: providerCatalog() })
      if (failed) { run = null; out.push(failedTurnLine(m, failed)); continue }
      const agent = m.kind !== 'system' && (st.open.thread.members.some((a) => a.persona === m.author) || catalog().some((a) => a.id === m.author))
      if (agent && !showReply(st.open, m)) continue
      if (!agent) { run = null; out.push(message(m), noteActions(m)); continue }
      const labels = replyChip(m, fixtureProviders ?? providers())
      const tooltip = [labels.provider, modelTooltip((fixtureProviders ?? providers()).find((p) => p.id === m.engine), m.model)].filter(Boolean).join(' · ')
      const identity = JSON.stringify([m.author, m.engine, m.model, m.kind === 'bulletin', m.viewKey])
      if (run?.identity !== identity) {
        const node = h('section', { class: 'tt-author-run', key: `author:${m.viewKey ?? m.id}`, 'data-author': m.author, 'aria-label': name(m.author), style: { '--tt-author-tone': look(m.author).color } },
          h('span', { class: 'tt-author-anchor', 'aria-hidden': 'true' }),
          h('div', { class: 'tt-speaker' }, h('div', { class: 'tt-author-pill' }, avatar(m.author, 24), h('span', { class: 'tt-author-name', text: name(m.author) }),
            labels.provider || labels.model ? h('span', { class: 'tt-author-meta', tabindex: '0', role: 'img', title: tooltip, 'aria-label': tooltip },
              labels.provider ? h('span', { class: 'tt-author-provider' }, providerMark(m.engine, 12, providerGlyph(m.engine))) : null,
              labels.model ? h('span', { class: 'tt-author-model', text: labels.model }) : null) : null,
            m.kind === 'bulletin' ? h('span', { class: 'tag', text: 'woke' }) : null)))
        run = { identity, node }; out.push(node)
      }
      run.node.appendChild(message(m))
    }
    return out
  }
  function decorateMention(node, token) {
    const agent = catalog().find((a) => a.id.toLowerCase() === token.slice(1).toLowerCase())
    paintMention(node, agent ? look(agent.id) : null)
  }
  function clearListeningTimers(o) {
    for (const key of ['listenersTimer', 'listenerPress', 'listenerHintTimer', 'listenerSendTimer', 'listenerPulseTimer']) clearTimeout(o?.[key])
  }
  function pressListener(o, id, e) {
    if (e.pointerType !== 'touch') return
    clearTimeout(o.listenerPress); clearTimeout(o.listenerHintTimer)
    o.listenerPress = setTimeout(() => {
      if (st.open !== o || !o.listeners.has(id) || o.listeners.get(id).leaving) return
      o.listenerHint = id; redraw()
      o.listenerHintTimer = setTimeout(() => { o.listenerHint = null; if (st.open === o) redraw() }, 1800)
    }, 450)
  }
  function listeningFaces() {
    const o = st.open, now = Date.now(), reduced = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const sending = o.sending || now < (o.listenerSendingUntil ?? 0)
    const text = draft.value, dictating = compose.dictating?.()
    const resolved = listeningRecipients(o.thread, text, dictating, catalog())
    // Editing a mention can briefly resolve nobody, especially without a
    // default. Hold the last faces for this draft instead of treating that
    // redraw as its end. A new resolved set replaces them; clearing resets it.
    if (!text.trim() && !dictating) o.listeningIds = []
    else if (resolved.length) o.listeningIds = resolved
    const ids = sending ? [] : o.listeningIds ?? []
    if (compose.dictating?.() && !sending) listeningPulse(o, now, reduced, compose.audioLevel?.() ?? 0)
    const pulse = !reduced && now - (o.lastPulse ?? -Infinity) < 650 && !sending
    if (pulse) {
      clearTimeout(o.listenerPulseTimer)
      o.listenerPulseTimer = setTimeout(() => { if (st.open === o) redraw() }, 650 - (now - o.lastPulse))
    }
    const visible = ids.slice(0, 4), extra = ids.length - visible.length
    const wanted = [...visible, ...(extra ? ['more'] : [])]
    o.listeners ??= new Map()
    for (const [id, entry] of o.listeners) {
      if (wanted.includes(id)) { entry.leaving = false; entry.until = null }
      else if (!entry.leaving) { entry.leaving = true; entry.until = Date.now() + 240 }
      else if (Date.now() >= entry.until) o.listeners.delete(id)
    }
    for (const id of wanted) if (!o.listeners.has(id)) o.listeners.set(id, {})
    clearTimeout(o.listenersTimer); o.listenersTimer = null
    const leaving = [...o.listeners.values()].filter((entry) => entry.leaving)
    if (leaving.length) o.listenersTimer = setTimeout(() => { if (st.open === o) redraw() }, Math.max(1, Math.min(...leaving.map((entry) => entry.until)) - Date.now()))
    if (extra) o.listenerExtra = extra
    const remaining = ids.slice(4).map(name).join(', ')
    return h('div', { key: 'listeners', class: `tt-listeners${sending ? ' sending' : ''}`, role: 'group', 'aria-label': 'Message recipients' },
      ...[...o.listeners].map(([id, entry], i) => h('span', { key: id, class: `tt-listener${entry.leaving ? ' leaving' : ''}${o.listenerHint === id ? ' show-name' : ''}`, 'data-persona': id, role: 'img',
        title: id === 'more' ? remaining : name(id), 'aria-label': id === 'more' ? remaining : name(id), 'aria-hidden': entry.leaving ? 'true' : null,
        onpointerdown: (e) => pressListener(o, id, e), onpointerup: () => clearTimeout(o.listenerPress), onpointercancel: () => clearTimeout(o.listenerPress),
        onpointerleave: () => clearTimeout(o.listenerPress), oncontextmenu: (e) => e.preventDefault(),
        tabindex: entry.leaving ? '-1' : '0', style: { '--listener-tone': id === 'more' ? 'var(--paper-ink-3)' : look(id).color, '--listener-index': i, '--listener-shape': id === 'more' ? '8px' : look(id).shape, '--listener-strength': o.pulseStrength ?? 1 } },
        pulse && id !== 'more' && !entry.leaving ? h('span', { key: `pulse:${o.pulse}`, class: 'tt-listener-ripple', 'aria-hidden': 'true' }) : null,
        h('span', { class: 'tt-listener-look' }, id === 'more' ? h('span', { class: 'tt-listener-more', text: `+${extra || o.listenerExtra || 0}` }) : avatar(id, 26)))))
  }
  function mentionMarkdown(text, pictures = null) {
    const nodes = renderText(text ?? '', { chatLinks: true, images: pictures?.images })
    const visit = (node) => {
      if (['CODE', 'PRE', 'A', 'FIGURE'].includes(node.nodeName)) return
      for (const child of [...(node.childNodes ?? [])]) {
        if (child.nodeType !== 3) { visit(child); continue }
        const parts = String(child.nodeValue).split(/((?<![\w@])@[\p{L}\p{N}_-]+)/u)
        if (parts.length < 2) continue
        for (const part of parts) {
          const token = /^@[\p{L}\p{N}_-]+$/u.test(part) ? h('span', { class: 'mention', text: part }) : document.createTextNode(part)
          if (token.nodeType === 1) decorateMention(token, part)
          node.insertBefore(token, child)
        }
        node.removeChild(child)
      }
    }
    nodes.forEach(visit); return nodes
  }
  const providerCatalog = () => fixtureProviders ?? providers()
  const engineOverride = (member) => member && Object.hasOwn(member, 'engine_override') ? member.engine_override : member?.engine
  const providerName = (member) => providerCatalog().find((p) => p.id === member.engine)?.name ?? member.engine ?? 'Connect a provider'
  const modelName = (member) => modelLabel(providerCatalog().find((p) => p.id === member.engine), member.model).name ?? 'Agent’s default'
  const modelTitle = (member) => modelTooltip(providerCatalog().find((p) => p.id === member.engine), member.model) || 'Agent’s default'
  function openConfiguration() {
    const o = st.open
    o.configuration = { returnLabel: 'Providers & models' }
    o.choice = null
    o.focusConfiguration = true; st.mention = null; redraw()
  }
  function closeConfiguration() {
    const o = st.open
    if (!o) return
    o.configurationReturn = o.configuration?.returnLabel
    o.configuration = null; o.choice = null; redraw()
  }
  function configurationControl(member, field) {
    const o = st.open, label = `${field === 'engine' ? 'Provider' : 'Model'} for ${name(member.persona)}`
    const active = o.choice === `${field}:${member.persona}`
    const supported = field === 'engine' || Object.hasOwn(member, 'model')
    return modelPill({ label: field === 'engine' ? providerName(member) : modelName(member), lead: field === 'engine' ? providerMark(member.engine, 14) : null,
      title: field === 'model' ? `${label} · ${modelTitle(member)}` : label, on: active, disabled: o.patching || !supported || (field === 'model' && !member.engine),
      onclick: () => {
        o.choice = active ? null : `${field}:${member.persona}`; redraw()
      }, props: { id: elementId(`tt-${field}-${member.persona}`), class: `modelbtn tt-${field === 'engine' ? 'engine' : 'model'}-select${active ? ' on' : ''}`, 'aria-label': label,
        'aria-description': field === 'engine' ? providerName(member) : modelName(member), 'data-value': member[field] ?? '',
        'aria-haspopup': 'listbox', 'aria-controls': elementId(`tt-${field}-${member.persona}-options`), 'aria-expanded': String(active),
        onkeydown: (e) => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); o.choice = `${field}:${member.persona}`; redraw() } } } })
  }
  function configurationOptions(member, field) {
    const o = st.open, label = `${field === 'engine' ? 'Provider' : 'Model'} for ${name(member.persona)}`
    if (o.choice !== `${field}:${member.persona}`) return null
    const catalog = providerCatalog()
    let options
    if (field === 'engine') {
      options = catalog.map((p) => ({ ...p, name: p.name ?? p.id, disabled: p.connected !== true }))
      if (Object.hasOwn(member, 'engine_override')) options.unshift({ id: '', name: 'Agent’s default' })
      if (member.engine && !options.some((p) => p.id === member.engine)) options.push({ id: member.engine, name: member.engine, reason: 'Unavailable', disabled: true })
    } else {
      options = [{ id: '', name: 'Agent’s default' }, ...modelsOf(catalog.find((p) => p.id === member.engine))]
      if (member.model && !options.some((m) => m.id === member.model)) options.push({ id: member.model, name: modelName(member), reason: 'Saved model' })
    }
    const selected = field === 'engine' ? engineOverride(member) : member[field]
    const close = () => { o.choice = null; o.configurationFocus = label; redraw() }
    return menuSurface(h('div', { class: 'tt-configuration-options', id: elementId(`tt-${field}-${member.persona}-options`), role: 'listbox' }, ...options.map((option) => settingsRow({ title: option.name, sub: option.reason,
        lead: field === 'engine' ? providerMark(option.id, 18) : null,
        right: (option.id ?? '') === (selected ?? '') ? icon.check(16) : null,
        onclick: async () => {
          o.choice = null
          const value = option.id || null
          if (value !== selected) {
            o.configurationSaved = false
            const ok = await patch({ [field]: { [member.persona]: value } })
            o.configurationSaved = ok
          }
          if (st.open === o) { o.configurationFocus = label; redraw() }
        }, props: { class: `setrow${(option.id ?? '') === (selected ?? '') ? ' on' : ''}`, role: 'option',
          'aria-selected': String((option.id ?? '') === (selected ?? '')), 'aria-disabled': String(Boolean(option.disabled)), tabindex: '-1', 'data-value': option.id ?? '', disabled: option.disabled,
          title: field === 'model' ? modelTooltip(catalog.find((p) => p.id === member.engine), option.id) : option.name } }))),
      { title: label, close, heading: false, anchored: true, initialFocus: 'selected', width: 320,
        anchor: `[id=${JSON.stringify(elementId(`tt-${field}-${member.persona}`))}]` })
  }
  function configurationSheet() {
    const o = st.open
    if (!o?.configuration) return []
    const conf = o.configuration
    return [h('button', { key: 'configuration-scrim', class: 'scrim tt-configuration-scrim', 'aria-label': 'Close providers and models', tabindex: '-1', onclick: closeConfiguration }),
      menuSurface(h('section', { key: 'configuration', class: 'sheet tt-configuration', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Providers & models',
        onkeydown: (e) => {
          if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeConfiguration() }
          if (e.key === 'Tab') {
            const controls = [...e.currentTarget.querySelectorAll('button:not(:disabled)')], at = controls.indexOf(document.activeElement)
            if (e.shiftKey && at <= 0) { e.preventDefault(); controls.at(-1)?.focus() }
            else if (!e.shiftKey && at === controls.length - 1) { e.preventDefault(); controls[0]?.focus() }
          }
        } }, sheetHandle(closeConfiguration),
        h('div', { class: 'sheet-head' }, h('h2', { class: 'display sheet-title', tabindex: '-1', text: 'Providers & models' }),
          h('p', { class: 'tt-configuration-note', text: 'For this thread. Kept until you change them.' })),
        h('div', { class: 'tt-configuration-body' }, ...o.thread.members.map((member) => h('div', { class: 'tt-configuration-member', key: member.persona, 'data-member': member.persona },
          h('div', { class: 'tt-configuration-who' }, avatar(member.persona, 32), h('strong', { text: name(member.persona) }), member.persona === o.thread.default ? h('span', { class: 'tag', text: 'Default' }) : null),
          h('div', { class: 'tt-configuration-controls' }, configurationControl(member, 'engine'), configurationControl(member, 'model')),
          configurationOptions(member, 'engine'), configurationOptions(member, 'model'),
          !Object.hasOwn(member, 'model') ? h('p', { class: 'tt-configuration-note', text: 'Model settings need a runtime update.' }) : null)),
          !o.thread.members.length ? h('p', { class: 'tt-configuration-note', text: 'Bring an agent into this thread to choose their provider and model.' }) : null,
          h('div', { class: 'tt-configuration-add' }, ...catalog().filter((a) => !o.thread.members.some((m) => m.persona === a.id)).map((a) => button([icon.plus(12), ` ${a.name}`], () => patch({ add: [a.id] }), { disabled: o.patching, 'aria-label': `Add ${a.name}` }))),
          o.error ? h('p', { class: 'banner', role: 'alert', text: failureSentence(o.error) }) : null),
        h('div', { class: 'tt-configuration-foot' }, h('span', { role: 'status', text: o.patching ? 'Saving…' : o.configurationSaved ? 'Saved to this thread' : 'Each agent uses their own settings' }), button('Done', closeConfiguration))), { title: 'Providers & models', width: 480, heading: false, anchor: `button[aria-label=${JSON.stringify(conf.returnLabel)}]`, close: closeConfiguration }) ]
  }
  async function deleteThread() {
    const o = st.open
    if (!o || o.patching || o.creating || o.createFailed) return
    o.patching = true; o.error = ''; redraw()
    try { await transport('DELETE', path(o.thread.id)); listingRequest++; st.threads = st.threads.filter((t) => t.id !== o.thread.id); if (st.open === o) close() }
    catch (e) { o.error = e.status === 409 ? `Wait for ${[...o.typing][0] || o.thread.members.some((m) => m.busy || m.running) ? name([...o.typing][0] ?? o.thread.members.find((m) => m.busy || m.running).persona) : 'the current turn'} to finish, then delete.` : e.status === 405 ? 'Deleting threads needs a runtime update.' : failureSentence(e.message); o.deleteBlocked = e.status === 409 ? o.error : null }
    finally { o.patching = false; redraw() }
  }
  function hasExtensionView(view) {
    return registry.entries('thread.header.action', { slot: 'below', thread: st.open?.thread, state: st.open }).some(entry => entry.view === view)
  }
  function currentView() { return st.manage ? 'details' : st.open?.extensionView || null }
  function setView(view) {
    const o = st.open
    if (!o || (currentView() === (view || null))) return
    if (st.manage && o.atEnd === false) o.restoreTop = o.readingTop
    if (view === 'details') {
      const pane = dom.querySelector('.tt-stream')
      o.readingTop = pane?.scrollTop; o.readingPosition = readingAnchor(pane); o.focusDetails = true
    } else o.focusChat = true
    st.manage = view === 'details'
    o.extensionView = view && view !== 'details' ? view : null
    o.choice = null
    redraw()
  }
  function chooseView(view, options) {
    if (currentView() === view) return
    setView(view); viewAddress(view, options)
  }
  function showDetails() { chooseView('details') }
  function details() {
    const o = st.open, general = o.thread.general || o.thread.id === 'general', busy = o.patching || o.creating || o.createFailed
    const back = () => { chooseView(null); o.focusTitle = true }
    const history = historyButton()
    const lead = history ?? (st.wide ? null : button([icon.chevronLeft(18), 'Chat'], back, { class: 'backbtn' }))
    const actions = sidePanel ? [...side().controls().slice(0, 2), round('Thread actions', icon.dots(18), () => { back(); o.headerMenu = true; redraw() }), side().controls().at(-1)] : []
    if (st.wide && !history) actions.unshift(round('Close thread details', icon.close(16), back))
    return h('main', { class: 'tt-details', key: `details:${o.thread.id}` },
      panelHeader({ title: 'Thread details', back: lead, actions, props: { class: 'chat-head plain tt-details-head' } }),
      registry.draw('app.overlay', { slot: 'player' }),
      h('div', { class: 'tt-details-workspace' }, st.wide ? settingsNav({ title: null, active: o.detailsSection ?? 'identity',
        items: [{ id: 'identity', label: 'Conversation', glyph: icon.chat(18) }, { id: 'members', label: 'Members', glyph: icon.users(18) }, { id: 'manage', label: 'Manage', glyph: icon.sliders(18) }],
        choose: (item) => { o.detailsSection = item.id; const pane = dom.querySelector('.tt-details-scroll'), target = dom.querySelector(`#${elementId(`tt-${item.id}`)}`); if (pane && target) pane.scrollTo({ top: pane.scrollTop + layoutPixels(target.getBoundingClientRect().top - pane.getBoundingClientRect().top) - 24, behavior: globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); redraw() },
        props: { class: 'setnav tt-details-nav', role: 'navigation', 'aria-label': 'Thread settings' } }) : null,
      h('div', { class: 'setmain tt-details-scroll' },
        h('section', { class: 'tt-details-identity', id: elementId('tt-identity') }, h('div', { class: 'tt-details-icon' }, threadFace(o.thread, 68)),
          general ? h('h3', { class: 'display', text: o.thread.title }) : o.renaming ? h('div', { class: 'titlefield' }, kept(title)) : h('h3', {}, button([o.thread.title || 'New thread', icon.pencil(13)], beginRename, { class: 'chattitle tt-details-title', title: 'Rename thread', disabled: busy })),
          !general ? h('div', { class: 'tt-icon-choices', role: 'group', 'aria-label': 'Thread icon' }, ...threadIcons.map(([value, label]) => button(threadGlyph(value, 20), () => patch({ icon: value }), { class: 'iconbtn tt-icon-choice', 'aria-label': value === null ? 'Default icon' : `Use ${value.startsWith('glyph:') ? label : value} icon`, 'aria-pressed': String((o.thread.icon ?? null) === value), disabled: busy }))) : null),
        h('section', { class: 'tt-details-section' }, h('h3', { text: 'Who replies by default' }),
          h('div', { class: 'tt-default-choices', role: 'group', 'aria-label': 'Default agent' },
            button([icon.users(20), 'Nobody'], () => patch({ default: null }), { class: `chip tt-default-chip${!o.thread.default ? ' on' : ''}`, 'aria-pressed': String(!o.thread.default), disabled: busy }),
            ...o.thread.members.filter(m => catalog().some(a => a.id === m.persona)).map((m) => button([avatar(m.persona, 24), name(m.persona)], () => patch({ default: m.persona }), { class: `chip tt-default-chip${o.thread.default === m.persona ? ' on' : ''}`, 'aria-label': name(m.persona), 'aria-pressed': String(o.thread.default === m.persona), disabled: busy }))),
          !o.thread.default ? h('p', { class: 'tt-nobody-note', text: 'Nobody replies unless you @mention someone.' }) : null),
        h('section', { class: 'tt-details-section', id: elementId('tt-members') }, h('h3', { text: 'Members' }), ...o.thread.members.map((m) => h('div', { class: 'tt-member', key: m.persona },
          settingsRow({ as: 'div', lead: h('span', { class: 'run-member-face' }, avatar(m.persona, 40), memberTakingTurn(o.thread, m, o, running?.forMember(o.thread.id, m.persona)) ? h('span', { class: 'dot run-live', title: 'Turn in progress', role: 'img', 'aria-label': 'Turn in progress' }) : null), title: name(m.persona), sub: h('span', { class: 'tt-member-state' }, memberTakingTurn(o.thread, m, o, running?.forMember(o.thread.id, m.persona)) ? 'Working' : backgroundCount(m) ? 'Waiting' : m.last_spoke_at || o.messages.some((line) => line.author === m.persona) ? 'Active in this thread' : 'Hasn’t spoken yet', backgroundBadge(m)),
            right: m.persona === o.thread.default ? h('span', { class: 'tag', text: 'Default' }) : round(`Remove ${name(m.persona)}`, icon.close(12), () => patch({ remove: [m.persona] }), { disabled: busy }) }),
          h('div', { class: 'tt-member-config' }, configurationControl(m, 'engine'), configurationControl(m, 'model'), running?.forMember(o.thread.id, m.persona) ? button('End session', () => running.requestEnd(running.forMember(o.thread.id, m.persona)), { class: 'quietlink run-member-end' }) : null),
          configurationOptions(m, 'engine'), configurationOptions(m, 'model'),
          !Object.hasOwn(m, 'model') ? h('p', { class: 'tt-configuration-note', text: 'Model settings need a runtime update.' }) : null)),
          h('h4', { class: 'kicker', text: 'Bring someone in' }), h('div', { class: 'tt-add-members' }, ...catalog().filter((a) => !o.thread.members.some((m) => m.persona === a.id)).map((a) => button([icon.plus(12), ` ${a.name}`], () => patch({ add: [a.id] }), { class: 'pillbtn', 'aria-label': `Add ${a.name}`, disabled: busy })))),
        h('section', { class: 'tt-details-section tt-details-actions', id: elementId('tt-manage') },
          settingsRow({ title: o.thread.pinned ? 'Unpin thread' : 'Pin thread', lead: h('span', { class: 'icon-tile' }, icon.pin(18)), onclick: () => patch({ pinned: !o.thread.pinned }), props: { disabled: busy || general } }),
          !general ? settingsRow({ title: o.thread.archived ? 'Unarchive thread' : 'Archive thread', lead: h('span', { class: 'icon-tile' }, icon.folder(18)), onclick: () => patch({ archived: !o.thread.archived }), props: { disabled: busy } }) : null,
          !general ? settingsRow({ title: 'Delete thread', lead: h('span', { class: 'icon-tile' }, icon.close(14)), onclick: () => { o.confirmDelete = true; o.focusDelete = true; redraw() }, props: { class: 'setrow tt-danger', disabled: busy } }) : null,
          o.confirmDelete && o.confirmDelete !== 'header' ? h('div', { class: 'tt-delete-confirm', role: 'group', 'aria-label': 'Confirm delete' }, h('p', { text: 'Delete this thread and its conversation? This cannot be undone.' }), button('Keep thread', () => { o.confirmDelete = false; redraw() }), button('Delete permanently', deleteThread, { class: 'pillbtn tt-danger', disabled: busy })) : null),
        o.error ? h('p', { class: 'banner', role: 'alert', text: failureSentence(o.error) }) : null)))
  }
  function emptyThread() {
    return h('div', { class: 'pane-body' }, registry.draw('app.overlay', { slot: 'player' }), emptyState({ object: 'speech', title: 'Good things start here.', body: 'Choose a conversation or start a new thread.' }))
  }
  function changeDraftPrompt(o) {
    const revision = o.changingDraft
    if (!revision || !agentDrafts.rows.some(d => d.draft === revision.draft && d.rev === revision.rev)) return null
    return o.thread.default ? `Tell ${name(o.thread.default)} what to change` : 'Say what to change'
  }

  function thread() {
    const o = st.open
    if (!o) return emptyThread()
    if (st.manage) return details()
    const general = o.thread.general || o.thread.id === 'general'
    o.branches = st.threads.filter((row) => row.branched_from?.reply && row.branched_from.thread === o.thread.id)
    syncTasks(o)
    const defaultMember = o.thread.members.find((m) => m.persona === o.thread.default)
    const unmatched = [...o.permissions.values()].filter((p) => !o.messages.some((m) => m.id === p.message_id))
    const closeMenu = (act) => () => { o.headerMenu = false; act(); redraw() }
    const headerContext = { thread: o.thread, state: o, open, patch, view: currentView(),
      selectView: chooseView, backButton: historyButton, panel: sidePanel ? 'side' : 'main', wide: st.wide }
    const controls = placement => registry.draw('thread.header.action', {
      ...headerContext, slot: 'control', placement, closeMenu: () => { o.headerMenu = false; redraw() },
    })
    const header = chatHeader({ wide: st.wide,
      back: o.extensionView ? null : historyButton() ?? (st.wide ? null : round('Back to threads', icon.chevronLeft(20), () => { close(); refreshList(); redraw() }, { class: 'iconbtn ghost tt-back' })),
      face: threadFace(o.thread, st.wide ? 42 : 40), onIdentity: showDetails, identityTitle: 'Thread details',
      title: o.thread.title || 'New thread', subtitle: null,
      onRename: general ? null : beginRename, renameLabel: 'Rename thread', titleField: o.renaming ? kept(title) : null,
      titleProps: { class: `${st.wide ? 'chattitle' : 'headtitle'} tt-title` },
      props: { class: `${st.wide ? 'chat-head' : 'thread-head'} tt-head` },
      actions: [...(sidePanel ? side().controls().slice(0, 2) : [headerActionsSlot(o, controls), taskIndicator()]),
        round('Thread actions', icon.dots(20), () => { o.headerMenu = !o.headerMenu; redraw() }, { class: 'iconbtn ghost', 'aria-expanded': String(Boolean(o.headerMenu)) }),
        sidePanel ? side().controls().at(-1) : null] })
    const chosenModel = defaultMember?.engine && composerModel(defaultMember, o.messages, providerCatalog())
    compose.field().__paintMention = decorateMention
    for (const node of compose.field().querySelectorAll?.('.mention') ?? []) decorateMention(node, node.textContent)
    const renderedComposer = compose.render({ sharedThread: true, record: { session_id: `thread:${o.thread.id}` }, agentId: o.thread.default,
      disabled: o.sending || o.loading || o.refreshing || o.readFailed || o.creating || o.createFailed,
      readOnly: o.readFailed || o.sending || o.createFailed,
      requiresText: Boolean(o.replyTo?.thread),
      changePrompt: changeDraftPrompt(o),
      placeholder: (!o.messages.length && o.promptPlaceholder) || (o.thread.default ? `Message ${name(o.thread.default)}` : o.creating ? 'A little room for your thought…' : 'Write a thought, or @mention someone'),
      mentionPicker: st.mention ? h('div', { key: 'mentions', class: 'popover tt-mentions', role: 'listbox', 'aria-label': 'Mention an agent',
        'data-menu-inputs': '.draft, .tt-mention-button', onmenudismiss: () => { st.mention = null; redraw() } }, ...mentionChoices().map((a, i) => settingsRow({ title: a.name, sub: a.sub, lead: a.everyone ? icon.users(24) : avatar(a.id, 24), onclick: () => insertMention(a), props: { type: 'button', role: 'option', 'aria-selected': String(i === (st.mention.index ?? 0)), class: `setrow${i === (st.mention.index ?? 0) ? ' on' : ''}` } }))) : null,
      modelControl: modelPill({ label: chosenModel?.name ?? 'Providers & models', lead: defaultMember?.engine ? providerMark(defaultMember.engine, 14) : icon.users(14),
        title: defaultMember?.engine ? chosenModel.title ?? `${name(defaultMember.persona)} · ${providerName(defaultMember)} · ${modelTitle(defaultMember)} · Settings for this thread` : 'Providers & models',
        disabled: Boolean(compose.dictating?.()) || o.loading || o.readFailed || o.creating || o.createFailed, onclick: () => openConfiguration(),
        props: { 'aria-label': 'Providers & models', 'aria-haspopup': 'dialog', 'aria-expanded': String(Boolean(o.configuration)) } }),
      mentionButton: round(st.mention ? 'Remove mention' : 'Mention an agent', '@', toggleMention, { class: `plusbtn tt-mention-button${st.mention ? ' on' : ''}`, onpointerdown: (e) => e.preventDefault(), disabled: o.sending || Boolean(compose.dictating?.()), 'aria-expanded': String(Boolean(st.mention)) }) })
    // Place player rows before asking for the bar: visible rows own playback.
    const pending = pendingRows(o).map(item => ({
      id: `pending-${item.body.submission_id}`, author: owner().id, kind: 'message',
      text: item.body.text, reply_to: item.body.reply_to, reply_source: item.body.reply_thread ? item.reply : null, attachments: item.attachments, queued: item,
    }))
    const messages = pending.length ? withPending(o.messages, pending) : withPending(o.messages, o.pending)
    const messageRows = [
      ...messageRuns(groupJoinNotices(withMarks(messages, o.marks), catalog())), ...unmatched.map(permission),
    ]
    const listeners = listeningFaces()
    o.listeningInset = listeningInset(o.listeners.size > 0, o.listeningInset, o.atEnd)
    return chatFrame({ wide: st.wide, props: { class: 'tt-thread', key: `thread:${o.thread.id}`, ...compose.dragProps() }, children: [
      st.fixture ? h('div', { class: 'tt-fixture' }, 'Design preview · sample conversation') : null,
      header,
      registry.draw('thread.header.action', { ...headerContext, slot: 'below' }),
      registry.draw('app.overlay', { slot: 'player' }),
      o.headerMenu ? menuSurface(h('div', { key: 'header-menu', class: 'popover tt-header-menu', role: 'menu' },
        side()?.available() && !o.creating && !o.createFailed ? settingsRow({ title: 'Open to the side', lead: icon.panelRight(18), onclick: closeMenu(() => side().openSide({ kind: 'thread', id: o.thread.id })) }) : null,
        sidePanel ? controls('menu') : null,
        sidePanel ? settingsRow({ title: 'Tasks', lead: icon.code(18), onclick: closeMenu(() => { o.tasksOpen = true; o.focusTasks = true }) }) : null,
        settingsRow({ title: 'Thread details', lead: icon.users(18), onclick: closeMenu(showDetails) }),
        !general ? settingsRow({ title: o.thread.pinned ? 'Unpin thread' : 'Pin thread', lead: icon.pin(18), onclick: closeMenu(() => patch({ pinned: !o.thread.pinned })) }) : null,
        !general ? settingsRow({ title: o.thread.archived ? 'Unarchive thread' : 'Archive thread', lead: icon.folder(18), onclick: closeMenu(() => patch({ archived: !o.thread.archived })) }) : null,
        !general ? settingsRow({ title: 'Delete thread', lead: icon.close(14), onclick: closeMenu(() => { o.confirmDelete = 'header'; o.focusDelete = true }) }) : null), { title: 'Thread actions', anchor: '[aria-label="Thread actions"]', close: () => { o.headerMenu = false; redraw() } }) : null,
      o.confirmDelete === 'header' ? menuSurface(h('div', { key: 'header-confirm', class: 'popover tt-delete-confirm tt-header-confirm', role: 'dialog', 'aria-label': 'Confirm delete', onkeydown: (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); o.confirmDelete = false; o.focusActions = true; redraw() } } },
        h('p', { text: 'Delete this thread and its conversation? This cannot be undone.' }),
        button('Keep thread', () => { o.confirmDelete = false; o.focusActions = true; redraw() }),
        button('Delete permanently', deleteThread, { class: 'pillbtn tt-danger', disabled: o.patching })), { title: 'Delete thread?', anchor: '[aria-label="Thread actions"]', close: () => { o.confirmDelete = false; redraw() } }) : null,
      h('section', { key: 'conversation', class: `tt-conversation pane-body${o.listeners.size ? ' has-listeners' : ''}`, inert: (o.extensionView && hasExtensionView(o.extensionView)) ? true : null, style: { '--tt-listening-inset': `${LISTENING_INSET}px` } },
        h('div', { class: 'msgs tt-stream', role: 'log', 'aria-label': 'Thread messages', style: { paddingBottom: o.dockHeight ? `${o.dockHeight + 10 + o.listeningInset}px` : '' }, 'aria-busy': String(o.loading || o.typing.size > 0), onscroll: (e) => {
          const was = o.atEnd, previous = o.scrollTop ?? e.target.scrollTop
          if (Math.abs(e.target.scrollTop - previous) > 1) { o.openingBoundary = null; o.replyAnchor = null; o.taskReplyAnchor = null; o.readingPosition = readingAnchor(e.target) }
          follow.scrolled(e.target); o.atEnd = follow.following(e.target); o.scrollTop = e.target.scrollTop
          if (o.atEnd && was === false && o.listeningInset && !o.listeners?.size) redraw()
          if (o.atEnd && was === false && !o.landing) markRead(o)
          if (e.target.scrollTop < previous && e.target.scrollTop < 160 && !o.landing) earlier()
        }, onwheel: () => { o.openingBoundary = null; o.taskReplyAnchor = null }, onpointerdown: () => { o.openingBoundary = null; o.taskReplyAnchor = null }, onkeydown: () => { o.openingBoundary = null; o.taskReplyAnchor = null } },
          h('div', { class: 'msgcol' },
            o.loading ? h('div', { class: 'tt-opening-note', role: 'status', text: 'Opening the latest messages…' }) : null,
            o.has_more || o.moreBusy ? h('div', { class: 'tt-history', key: 'history' },
              o.moreBusy ? h('div', { class: 'earlier tt-history-loading', role: 'status', text: 'Loading earlier…' }) : button('Earlier messages', () => earlier(), { class: 'earlier' }),
              o.historyError ? h('div', { class: 'muted', role: 'status', text: o.historyError }) : null) : null,
            !o.loading && !messages.length ? emptyConversation({
              title: o.promptTitle || (o.thread.default ? `A new thread with ${name(o.thread.default)}` : 'A thread without an agent'),
              body: o.promptBody || (o.thread.default ? 'Start with a thought. Bring others in with @.' : 'Keep a note here, or tag an agent with @ to start a conversation.'),
              face: h('div', { class: 'tt-empty-icon' }, threadFace(o.thread, 68)),
            }) : null,
            ...messageRows,
            agentDraftController.view(o.thread.id))),
        chatDock(h('div', { class: 'tt-composer' },
          listeners,
          o.error ? h('div', { class: 'banner', role: 'alert' }, o.error, o.createFailed ? button('Try creating again', () => create(o.thread.default, { promptTitle: o.promptTitle, promptBody: o.promptBody, placeholder: o.promptPlaceholder, draft: draft.value, source: o.replyTo?.thread ? o.replyTo : null })) : button('Refresh thread', () => open(o.thread.id))) : null,
          o.connection ? h('div', { class: 'noticerow', role: 'status', text: o.connection }) : null,
          composingReply(), renderedComposer), compose.hint?.())),
      branchPanel(),
    ] })
  }

  function view(wide) {
    if (st.open) rememberReading(st.open)
    clearTimeout(awaitTimer); awaitTimer = null; awaitDue = Infinity
    for (const id of awaitingSince.keys()) if (!st.open || !memberGenerating(st.open, id) || !threadWaiting(st.open, id, name(id), 0)) awaitingSince.delete(id)
    st.wide = wide
    const shell = h('div', { class: `tt-shell${wide ? ` desk${collapsed() ? ' collapsed' : ''}` : ''}${st.open ? ' has-thread' : ''}` },
      wide ? rail() : null, wide ? sidebar?.view?.() ?? list() : !st.open ? list() : null,
      wide ? h('main', { class: 'pane', 'data-panel': 'main' }, collapsed() ? round('Show sidebar', icon.panelLeft(18), () => collapse(false), { class: 'iconbtn showbar', key: 'show-sidebar' }) : null, thread()) : st.open ? thread() : null,
      !wide && !st.open ? nav() : null)
    if (st.open?.tasksOpen) {
      for (const child of shell.childNodes) if (child.nodeType === 1) child.setAttribute('inert', '')
      for (const overlay of taskPanel()) shell.appendChild(overlay)
    }
    if (st.open?.configuration) {
      for (const child of shell.childNodes) if (child.nodeType === 1) child.setAttribute('inert', '')
      for (const overlay of configurationSheet()) shell.appendChild(overlay)
    }
    return shell
  }
  function pane({ empty = false } = {}) {
    st.wide = true
    if (empty) return [emptyThread()]
    if (st.open) rememberReading(st.open)
    clearTimeout(awaitTimer); awaitTimer = null; awaitDue = Infinity
    const body = thread()
    if (st.open?.tasksOpen || st.open?.configuration) body.setAttribute('inert', '')
    return [body, ...(st.open?.tasksOpen ? taskPanel() : []), ...(st.open?.configuration ? configurationSheet() : [])]
  }
  function leanFaces() {
    // A DOM Range measures the real caret, including wrapped markdown and
    // mention decorations. Selection changes also call this after rendering.
    const field = compose.field(), selection = globalThis.getSelection?.()
    const shelf = dom.querySelector('.tt-listeners')
    if (shelf && selection?.rangeCount && field.contains?.(selection.focusNode)) {
      const range = document.createRange()
      range.setStart(selection.focusNode, selection.focusOffset); range.collapse(true)
      const rect = range.getClientRects()[0], box = field.getBoundingClientRect()
      if (rect && box.width) shelf.style.setProperty('--listener-lean', `${Math.max(-3, Math.min(3, ((rect.x - box.x) / box.width - .5) * 6))}px`)
    }
  }
  function afterRender() {
    let systemChanged = false
    for (const node of dom.querySelectorAll?.('.tt-system-text') ?? []) {
      if (!node.scrollHeight) continue
      const key = node.getAttribute('data-system-key')
      const long = node.scrollHeight > parseFloat(getComputedStyle(node).lineHeight) * 3 + 1
      if (long !== longSystemLines.has(key)) {
        if (long) longSystemLines.add(key); else longSystemLines.delete(key)
        systemChanged = true
      }
    }
    if (systemChanged) redraw()
    const list = dom.querySelector('.tt-list .scroll')
    observeLine(st.wide && collapsed() ? null : list?.querySelector('.tt-chats-line[data-line-layout]'))
    observeHeaderActions(sidePanel ? null : dom.querySelector('.tt-head .tt-header-actions[data-header-actions-layout]'))
    if (list && st.resetListScroll) { list.scrollTop = 0; st.resetListScroll = false }
    if (st.focusList) {
      const target = dom.querySelector(st.archived ? '[aria-label="Back to Chats"]' : '[aria-label="Thread list actions"]')
      if (target) { target.focus({ preventScroll: true }); st.focusList = false }
    }
    const sentinel = list?.querySelector('.tt-archive-more')
    if (sentinel !== archiveSentinel) {
      archiveObserver?.disconnect(); archiveObserver = null; archiveSentinel = sentinel
      if (sentinel && globalThis.IntersectionObserver) {
        archiveObserver = new IntersectionObserver(entries => {
          if (entries.some(e => e.isIntersecting) && sentinel.isConnected) { archiveObserver?.unobserve(sentinel); archiveSentinel = null; moreArchived() }
        }, { root: list, rootMargin: '0px 0px 500px 0px' })
        archiveObserver.observe(sentinel)
      }
    }
    selection.paint()
    gesture.paint()
    if (st.open?.focusReplyDraft) { compose.field().focus({ preventScroll: true }); st.open.focusReplyDraft = false }
    leanFaces()
    if (st.open?.focusTasks) { dom.querySelector('.tt-tasks-panel h2')?.focus({ preventScroll: true }); st.open.focusTasks = false }
    if (st.open?.focusTasksReturn) { (dom.querySelector('.tt-tasks-button:not(:disabled)') ?? dom.querySelector('.tt-title'))?.focus({ preventScroll: true }); st.open.focusTasksReturn = false }
    if (st.open?.focusTaskRow) { dom.querySelector(`.tt-task-row[data-task=${JSON.stringify(st.open.focusTaskRow)}]`)?.focus({ preventScroll: true }); st.open.focusTaskRow = null }
    if (st.open?.configurationReturn) { dom.querySelector(`button[aria-label=${JSON.stringify(st.open.configurationReturn)}]`)?.focus({ preventScroll: true }); st.open.configurationReturn = null }
    if (st.open?.focusConfiguration) {
      const o = st.open, pane = dom.querySelector('.tt-configuration')
      pane?.querySelector('h2')?.focus({ preventScroll: true })
      o.focusConfiguration = false
    }
    if (st.open?.configurationFocus) { dom.querySelector(`button[aria-label=${JSON.stringify(st.open.configurationFocus)}]`)?.focus({ preventScroll: true }); st.open.configurationFocus = null }
    if (st.focusSearch) { dom.querySelector('.tt-search input')?.focus(); st.focusSearch = false }
    const dock = dom.querySelector('.tt-conversation .thread-dock')
    if (dock && st.open && dock.offsetHeight > 0 && st.open.dockHeight !== dock.offsetHeight) { st.open.dockHeight = dock.offsetHeight; redraw() }
    if (st.open?.focusDelete) { const confirm = dom.querySelector('.tt-delete-confirm'); if (st.open.confirmDelete !== 'header') confirm?.scrollIntoView({ block: 'nearest' }); confirm?.querySelector('button')?.focus({ preventScroll: true }); st.open.focusDelete = false }
    if (st.open?.focusActions) { dom.querySelector('[aria-label="Thread actions"]')?.focus({ preventScroll: true }); st.open.focusActions = false }
    if (st.open?.focusRename) { title.focus(); title.select(); st.open.focusRename = false }
    if (st.open?.focusDetails) { dom.querySelector('.tt-details-head h2')?.focus({ preventScroll: true }); st.open.focusDetails = false }
    if (st.open?.focusTitle) { dom.querySelector('.tt-title')?.focus({ preventScroll: true }); st.open.focusTitle = false }
    if (st.open?.focusChat) { dom.querySelector('.tt-title')?.focus({ preventScroll: true }); st.open.focusChat = false }
    const pane = dom.querySelector('.tt-stream')
    const o = st.open
    if (pane && o && o.visitId == null) {
      o.visitId = visitId()
      const place = readingPlaces.get(o.visitId)
      if (place?.id === o.thread.id) {
        o.atEnd = place.atEnd
        o.savedAnchor = place.atEnd ? null : place.anchor
        o.landing = false
        readingPlaces.delete(o.visitId)
      }
    }
    if (o?.readOnShow && visible() && o.atEnd !== false) { o.readOnShow = false; markRead(o) }
    const placeBoundary = (id) => {
      const boundary = [...pane.querySelectorAll('[data-unread-boundary]')].find((n) => n.getAttribute('data-unread-boundary') === id)
      if (!boundary) return false
      pane.scrollTop += layoutPixels(boundary.getBoundingClientRect().top - pane.getBoundingClientRect().top) - 56
      return true
    }
    follow.watch(pane)
    if (pane && o?.replyJump) {
      const anchor = { id: o.replyJump, offset: 96 }
      if (restoreAnchor(pane, anchor)) {
        const target = [...pane.querySelectorAll('[data-message-id]')].find((n) => n.getAttribute('data-message-id') === anchor.id)
        o.taskReplyAnchor = anchor; o.readingPosition = anchor; o.replyAnchor = null; o.highlightedReply = anchor.id; o.replyJump = null
        // Following a control hands off its focus. An address-bar visit just lands at the message.
        if (o.replyJumpFocus) target?.focus({ preventScroll: true })
        o.replyHighlightTimer = setTimeout(() => { if (st.open === o) { o.highlightedReply = null; redraw() } }, 2000)
        redraw()
      } else if (!o.moreBusy && !o.loading && !o.refreshing && (o.historyError || (!o.has_more && !o.gapBefore))) {
        o.error = o.historyError || (o.jumpKind === 'quote' ? 'This earlier message is no longer available.' : 'The reply that started this task is no longer available.')
        o.replyJump = null; redraw()
      }
      follow.release()
    }
    else if (pane && o?.taskReplyAnchor) { restoreAnchor(pane, o.taskReplyAnchor); o.readingPosition = o.taskReplyAnchor; o.replyAnchor = null; follow.release() }
    else if (pane && o?.savedAnchor && restoreAnchor(pane, o.savedAnchor)) { o.readingPosition = o.savedAnchor; o.savedAnchor = null; o.replyAnchor = null; o.landing = false; o.atEnd = false; follow.release() }
    else if (pane && o?.landAt) {
      const landed = placeBoundary(o.landAt) || restoreAnchor(pane, { id: o.landAt, offset: 96 })
      if (landed) {
        o.atEnd = pane.scrollHeight - pane.scrollTop - pane.clientHeight <= 2
        o.openingBoundary = o.atEnd ? null : o.landAt
        o.replyAnchor = null; o.landAt = null; o.landing = false
        if (o.atEnd) follow.pin(pane); else follow.release()
      }
    }
    else if (pane && o?.openingBoundary && placeBoundary(o.openingBoundary)) { o.replyAnchor = null; follow.release() }
    else if (pane && o?.replyAnchor) { restoreAnchor(pane, o.replyAnchor); o.readingPosition = o.replyAnchor; o.replyAnchor = null; follow.release() }
    else if (pane && o?.restoreTop !== undefined) { pane.scrollTop = o.restoreTop; delete o.restoreTop; follow.release() }
    else if (pane && o?.atEnd !== false) { follow.pin(pane); if (!o.loading) o.landing = false }
    if (pane && o) {
      o.scrollTop = pane.scrollTop
      if (o.atEnd === false && !o.replyAnchor) o.readingPosition = o.taskReplyAnchor ?? readingAnchor(pane)
      if (!o.readingObserver && typeof ResizeObserver !== 'undefined') {
        o.readingObserver = new ResizeObserver(() => {
          if (st.open !== o || o.atEnd !== false || o.landing || o.replyAnchor || o.savedAnchor || o.openingBoundary) return
          restoreAnchor(pane, o.readingPosition)
          o.scrollTop = pane.scrollTop
          o.readingPosition = readingAnchor(pane)
        })
        const column = pane.querySelector('.msgcol')
        if (column) o.readingObserver.observe(column)
      }
      if (!o.loading && !o.refreshing && !o.moreBusy && !o.contextScheduled && (o.replyJump || o.needsContext || o.gapBefore)) {
        o.contextScheduled = true
        requestAnimationFrame(() => setTimeout(() => { o.contextScheduled = false; if (st.open === o) earlier({ context: true }) }, 0))
      }
    }
    authors.watch(pane)
    if (o && !o.loading && !o.refreshing) o.quotes?.ensure(o.messages, o.has_more || Boolean(o.gapBefore))
    o?.prose?.watch(pane)
    o?.replySteps?.watch(pane)
  }
  function tick(active) {
    if (authExpired()) return
    if (!active) { stopList(); return }
    ensureList(); listLink?.wake()
    refreshCreationCards(st.open?.messages)
    if (st.open && Date.now() - (st.open.jobsReadAt ?? 0) >= 5000) refreshJobs(st.open)
    if (!st.listLive && st.checked && Date.now() - st.refreshed > 15000 && !st.loading) load({ quiet: true })
  }
  function dirty() {
    const state = st.open
    if (!state) return false
    const context = { slot: 'below', thread: state.thread, state }
    return registry.entries('thread.header.action', context).some(entry => registry.invoke(entry, 'dirty', context))
  }
  function pause() { generation++; gesture.reset(); selection.disconnect(); clearReturn(); clearListeningTimers(st.open); clearTaskTimers(st.open); clearTimeout(st.open?.replyHighlightTimer); clearTimeout(st.open?.copyMessageTimer); if (st.open) st.open.copiedMessage = null; authors.disconnect(); st.open?.prose?.stop(); st.open?.replySteps?.watch(null); clearTimeout(awaitTimer); awaitTimer = null; controller?.abort(); clearTimeout(retry); stopList() }
  const uploadPreview = (file, name) => transport('POST', `${path(st.open.thread.id)}/attachments`, { name, size: file.size, type: file.type })
  return { uploadPreview, st, load, agentDrafts, loadAgentDrafts, refreshAgents() { runtimeAgents = null; redraw() }, preview, open, close, create, list, showArchived, row: threadRow, activityRows, activityFace: t => threadFace(t, 36), activityInfo: t => {
    const state = threadSubtitle({ ...t, unread: 0 }, name, activityOptions(t))
    const groups = threadActivity(t, activityOptions(t))
    return { status: groups.asking.length ? 'Needs your input' : groups.working.length ? 'Working' : groups.waiting.length ? 'Waiting' : 'Quiet',
      secondary: state === 'Quiet' ? t.last?.text || 'Quiet' : state }
  }, catalog, view, pane, currentView, setView, afterRender, tick, dirty, send, saveCompose, transferTo, receiveVisit: (id, visit) => visits.set(id, visit), mention, mentionKey, pause, defaultName: () => name(st.open?.thread.default) }
}
