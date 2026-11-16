import { copyText } from "../clipboard.js"
import { h, icon } from "../ui.js"
import { foldsAsStep, blockView, compactionWords, foldFacts, toolSummary, modelShortName, contextWords } from "../transcript.js"
import { agentProse, userBubble, messageFiles, stepFold } from "../chat-ui.js"
import { markdown, strip } from "../markdown.js"
import { departureLine } from "../asks.js"
import { listedModelName } from "../providers.js"

// Mounted once. Cross-module state and operations are explicit host services.
export function mount(host) {


// The pill that says what a chat runs as and opens the Model-and-Effort menu.
//
// One component, two places: row two of the composer, where it names the open
// chat's model, and the New-chat sheet, where it names what the chat about to
// be started will run as. It was the composer's alone until 2026-09-23, when
// the sheet's "Juno's default model · default effort  Change" line was replaced
// by it — a person who has learnt the pill in a chat has learnt it before the
// chat exists, and two components saying the same thing in two shapes was the
// thing to avoid.

// Which line of the transcript this is, for the reconciler.
//
// It cannot be anything the item *says*, because an item is written to after it
// is drawn — a user line is drawn before the broker gives it its `seq`, and an
// assistant message grows blocks as it streams. The identity of the item object
// itself is the one thing that does not move, so that is what is remembered.
const lineKeys = new WeakMap()

let lineCount = 0

function keyOf(item) {
  if (!lineKeys.has(item)) {
    lineCount += 1
    lineKeys.set(item, `l${lineCount}`)
  }
  return lineKeys.get(item)
}




// ------------------------------------------- a long message of mine, folded
//
// **The height a sent message clamps to, in pixels, and it is one constant.**
// Six lines of the transcript's own type — 16px at 1.35 — plus the bubble's
// padding, which is the same ceiling the composer's draft has (`DRAFT_MAX_PX`)
// and is deliberately the same number: what a person typed in six lines reads
// back in six lines.
const MINE_MAX_PX = 132


// A message a few pixels over the clamp is not a message worth a control, and a
// fade over one clipped word is worse than no fade. Under this much overflow it
// is simply drawn.
const MINE_SLACK_PX = 10




// **Which message is long is measured, because only a browser knows.** How many
// lines a message takes is its font, its width, its words and its markdown all
// at once; a rule counting characters would clamp a six-line message on a phone
// and not on a laptop, and would be wrong about a table either way.
//
// So it is the same shape as `fitDock()`: after the draw, read the node. A
// clamped element's `scrollHeight` is still the height of what is in it, so the
// reading is the same before and after the clamp is applied and a message never
// oscillates. The set only ever grows, which is what makes the redraw below
// settle after one pass.
function fitMine() {
  let found = false
  for (const node of (host.panelDOM ?? host.root).querySelectorAll?.('.say.user') ?? []) {
    const key = node.getAttribute?.('data-mine')
    if (!key || host.longMine.has(key)) continue
    const body = node.querySelector?.('.user-message-body')
    if ((body ? body.scrollHeight + 18 : node.scrollHeight ?? 0) > MINE_MAX_PX + MINE_SLACK_PX) {
      host.longMine.add(key)
      found = true
    }
  }
  if (found) host.schedule()
}



// ------------------------------------------------------------------ copying
//
// **One copy, everywhere a button copies.** Every code block and table in a
// reply (2026-09-30) and the Dictation page's old terms go through
// `copyWithCue()`: the text onto the clipboard, and the button that was pressed
// reading "Copied" for a moment. Which button that was is an id — `oldterms`,
// or a block's own source — held here and not on a node, because
// the next redraw builds the button again and has to be told what it says.
const COPIED_MS = 1500

let copied = null

async function copyWithCue(id, text) {
  const ok = await copyText(text)
  if (!ok) return false
  copied = id
  host.schedule()
  setTimeout(() => {
    if (copied !== id) return
    copied = null
    host.schedule()
  }, COPIED_MS)
  return true
}



// The button in a code block's or a table's corner. The owner, 2026-09-30: "when
// a text or code box is formatted in the AI output, it doesn't have an easy copy
// text or code button and because it is its own separate scrollable container,
// copying becomes hard." It copies the block's **source**: the code as written,
// and a table as the markdown it was written in — not the drawn cells. A ghost
// in the corner; the stylesheet shows it on hover where there is one, and
// always, muted, where there is not.
function copyButton(source, what) {
  const done = copied === `block:${source}`
  return h(
    'button',
    {
      type: 'button',
      class: `copyblk${done ? ' done' : ''}`,
      title: done ? 'Copied' : `Copy this ${what}`,
      'aria-label': done ? 'Copied' : `Copy this ${what}`,
      onclick: () => copyWithCue(`block:${source}`, source),
    },
    done ? icon.check(12) : icon.copy(13),
    h('span', { text: done ? 'Copied' : 'Copy' }),
  )
}



// Nested subagent history uses the same step folds as its parent. It never
// owns a session, composer, stream or read mark.
function stepItems(items) {
  const out = []
  for (const item of items) {
    // A result whose call is above the window: a step of its own, at the point
    // in the conversation it came back. It is in the timeline rather than in a
    // message because no message on this page ever asked for it.
    if (item.kind === 'tool') {
      out.push({ what: 'step', block: item.block })
      continue
    }
    // An approval's line folds with the calls around it (`foldsAsStep`).
    if (foldsAsStep(item)) {
      out.push({ what: 'step', block: item })
      continue
    }
    if (item.kind !== 'assistant') {
      out.push({ what: item.kind, item })
      continue
    }
    for (const block of item.blocks) {
      if (!block) continue
      if (block.type === 'text') {
        if ((block.text ?? '').trim() === '') continue
        out.push({ what: 'text', block })
      } else if (block.type === 'thinking') {
        // **The agent's thinking is machinery, not what it said**, so it is a
        // step inside the fold like a tool call. It was an italic paragraph of
        // its own in the thread until 2026-09-25, between the sentences a person
        // is reading.
        if ((block.text ?? '').trim() === '') continue
        out.push({ what: 'step', block })
      } else if (block.type === 'tool') {
        // A plan card is the agent saying what it intends, it is already a card
        // that opens, and the design draws it whole — so it is never inside a
        // fold, and it ends the run before it like any other line.
        out.push({ what: blockView(block).view === 'plan' ? 'plan' : 'step', block })
      }
    }
  }
  return out
}

function stepChildren(items) {
  const flat = stepItems(items), out = []
  for (let i = 0; i < flat.length;) {
    if (flat[i].what !== 'step') { out.push(...itemNode(flat[i])); i++; continue }
    let end = i
    while (end < flat.length && flat[end].what === 'step') end++
    out.push(foldCard(flat.slice(i, end).map((entry) => entry.block))); i = end
  }
  return out
}

function itemNode({ what, item, block }) {
  if (what === 'text') return [h('div', { class: 'line left', key: keyOf(block) }, agentProse(markdown(block.text, { copy: copyButton })))]
  if (what === 'plan') return [toolNode(block)]
  if (what === 'user') return [h('div', { class: 'line right', key: keyOf(item) }, userBubble({}, messageFiles(item.attachments ?? []), ...markdown(item.text ?? '')))]
  if (what === 'answer') return [answeredLine(item)]
  if (what === 'save_notice') return [h('div', { class: 'notice save-notice ' + item.tone, key: keyOf(item), role: 'status', text: item.text })]
  if (what === 'permission') return [askLine(item)]
  if (what === 'result') return [turnLine(item, keyOf(item))]
  if (what === 'exit') return [h('div', { class: 'exitline', key: keyOf(item), text: 'The session’s process ended (exit ' + (item.code ?? '?') + ')' })]
  if (what === 'compaction') return [h('div', { class: 'compactline', key: keyOf(item), role: 'separator', text: compactionWords(item) })]
  return []
}



// The fold: the design's collapsible card over a run of steps, with the steps
// themselves inside it rather than a list about them.
//
// **Every step of the run is in here** — the call running now, the call that
// failed, the agent's thinking — and the header is where the three things a
// person needs without opening it are said:
//
// - what it is doing, while it is doing it: the latest line of the agent's own
//   thinking, in place of the count, with the breathing dot the plan card uses.
//   One line, clipped by the stylesheet. Once the run has finished the header
//   goes back to the count and the tick.
// - how many steps it holds, which is what opening it shows.
// - **that one of them failed, quietly.** After the count, in the header's own
//   muted type: no icon, no fill, and never the error colour. The owner,
//   2026-09-25: "when an action by an agent fails, please still keep it in the
//   steps drop down. Maybe just have a small and discrete indicator that the
//   dropdown includes a failure… It should be a very very discrete thing since
//   it is usually not a big deal."
//
// **Folded, it says nothing else.** It listed the tools it had run beside the
// count until 2026-09-21 — "5 steps · Bash · Read" — which is detail about work
// a person has chosen not to look at; the names are on the rows themselves,
// which is where opening it puts them.
function foldCard(steps, context) {
  const key = `fold:${keyOf(steps[0])}`, facts = foldFacts(steps)
  return stepFold({ key, open: openBlocks.has(key), facts, live: facts.running && facts.thinking ? facts.thinking : null,
    toggle: () => toggleOpen(key), body: () => steps.map((step) => stepNode(step, context)) })
}



// One step inside an open fold: a tool call as its own row, or a block of the
// agent's thinking as the paragraph it is.
function stepNode(step, context) {
  if (step.type === 'thinking') return h('div', { class: 'thinking', key: keyOf(step), text: step.text })
  if (step.kind === 'permission') return askLine(step, context)
  return toolNode(step)
}



// The one line a closed request leaves: in a fold for an approval, in the
// thread for a question.
function askLine(item, context) { return h('div', { class: 'askline ' + item.behavior, key: keyOf(item), text: departureLine(item, context?.agentName ?? '') }) }



// A tool call is one of three things, and which one is the seam's to say.
const openBlocks = new Set()

 // which rows and which folds the reader has opened
const longToolDetails = new Set()

function toggleOpen(key) {
  if (openBlocks.has(key)) openBlocks.delete(key)
  else openBlocks.add(key)
  host.schedule()
}

function toolNode(block) {
  const view = blockView(block)
  const line = keyOf(block)
  const key = `tool:${line}`
  const isOpen = openBlocks.has(key)
  const toggle = () => toggleOpen(key)

  if (view.view === 'plan') {
    return h(
      'div',
      { class: 'line left', key: line },
      h(
        'div',
        { class: 'plan' },
        h(
          'button',
          { class: 'plan-head tool-toggle', type: 'button', onclick: toggle, 'aria-expanded': String(isOpen) },
          view.running ? h('span', { class: 'spinner' }) : icon.check(12),
          h('b', { text: view.title }),
          h('span', { text: view.sub }),
          h('span', { class: 'chev' }, icon.chevronDown(12)),
        ),
        isOpen && view.steps.length > 0
          // A plan's steps come off a markdown document, so a step reads as one
          // line of prose rather than as its marks. It is one line and not a
          // rendering: the card's rows are the design's, not a transcript's.
          ? h('ol', {}, ...view.steps.map((s, i) => h('li', { class: s.state, key: `step:${i}`, text: strip(s.text) })))
          : null,
        isOpen ? h('div', { class: 'toolrow' }, toolDetail(line, 'Input', JSON.stringify(block.input ?? {}, null, 2)),
          block.result !== null ? toolDetail(line, 'Output', block.result) : null) : null,
      ),
    )
  }

  if (view.view === 'diff') {
    return h(
      'div',
      { class: 'line left', key: line },
      h(
        'div',
        { class: 'diff record-diff' },
        h('button', { class: 'diff-head tool-toggle', type: 'button', onclick: toggle, 'aria-expanded': String(isOpen) },
          icon.file(14), h('span', { class: 'file', text: `${block.name} ${view.file}` }),
          h('span', { class: 'add', text: `+${view.added}` }), h('span', { class: 'del', text: `−${view.removed}` })),
        isOpen ? h('div', { class: 'toolrow' }, toolDetail(line, 'Input', JSON.stringify(block.input ?? {}, null, 2)),
          block.result !== null ? toolDetail(line, 'Output', block.result) : null) : null,
      ),
    )
  }

  // A background subagent's whole conversation, under the call that started it
  // — and folding inside it too, because it is a thread like any other.
  const children = isOpen ? stepChildren(block.children ?? []) : []
  // **A step that failed shows what it said without a second tap.** A person who
  // has opened the fold to find the failure has already asked the question the
  // row's own chevron asks, and the error text is the answer to it.
  const showOut = isOpen || view.isError
  return h(
    'div',
    { class: 'line left', key: line },
    h(
      'div',
      { class: `toolrow${view.isError ? ' err' : ''}` },
      h(
        'button',
        { class: 'toolrow-head tool-toggle', type: 'button', 'aria-expanded': String(isOpen), onclick: toggle },
        h('span', { class: 'sigil', text: block.name ?? '$' }),
        h('span', { class: 'cmd', text: block.earlier ? view.command : toolSummary(block) }),
        h('span', { class: 'sub', text: view.sub }),
      ),
      isOpen ? toolDetail(line, 'Input', typeof block.input === 'string' ? block.input : JSON.stringify(block.input ?? {}, null, 2)) : null,
      showOut && block.result !== null ? toolDetail(line, 'Output', block.result) : null,
      isOpen && children.length > 0 ? h('div', { class: 'out tool-children' }, ...children) : null,
    ),
  )
}

function toolDetail(line, label, text) {
  const key = `detail:${line}:${label}`, expanded = openBlocks.has(key)
  const long = longToolDetails.has(key) || text.length > 2400 || text.split('\n').length > 18
  return h('div', { class: 'tool-detail' },
    h('div', { class: 'tool-detail-label', text: label }),
    h('div', { class: `${label === 'Output' ? 'out' : 'tool-input'} tool-content${long && !expanded ? ' clamped' : ''}`, 'data-tool-detail': key, text }),
    long ? h('button', { class: 'morebtn tool-more', type: 'button', text: expanded ? 'Show less' : 'Show more', 'aria-expanded': String(expanded), onclick: () => toggleOpen(key) }) : null)
}

function fitToolOutputs() {
  let changed = false
  for (const node of (host.panelDOM ?? host.root).querySelectorAll?.('.tool-content') ?? []) {
    const key = node.getAttribute('data-tool-detail')
    if (node.scrollHeight > 240 && !longToolDetails.has(key)) { longToolDetails.add(key); changed = true }
  }
  if (changed) host.schedule()
}




// The line an answered question leaves: what was asked, and what came back —
// or that it was declined. The labels are the ones chosen, as written; a secret
// value is not drawn back onto the page it was typed into.
function answeredLine(item) {
  const questions = item.questions ?? []
  const declined = item.behavior !== 'allow'
  const answers = item.answers ?? {}
  const rows = declined
    ? []
    : questions.length > 0
      ? questions.map((q) => ({ key: q.id, head: q.header || q.question, values: answers[q.id] ?? [], secret: q.secret }))
      : Object.entries(answers).map(([id, values]) => ({ key: id, head: '', values, secret: false }))
  return h(
    'div',
    { class: 'line left', key: keyOf(item) },
    h(
      'div',
      { class: 'answered' },
      h('div', { class: 'kicker', text: declined ? 'Declined to answer' : 'Answered' }),
      ...rows.map((r) =>
        h(
          'div',
          { class: 'arow', key: r.key },
          r.head ? h('span', { class: 'ahead', text: r.head }) : null,
          h('span', {
            class: 'avals',
            text: r.values.length === 0 ? '—' : r.secret ? '••••••' : (Array.isArray(r.values) ? r.values : [r.values]).join(', '),
          }),
        ),
      ),
    ),
  )
}



// What this turn ran on, and what it cost — quiet by default and whole on a tap.
//
// The owner, 2026-09-22: "I like the model and information shown under a response
// from the model. I want to add to that how many tokens of context was used,
// how many tokens of context is supported with that session's set up. Also, I
// want the design to be less visible and in my face and more available easily
// if I want it, maybe with one click or something."
//
// So the default is one muted token — the model's short name, which is the
// thing worth reading at a glance — and a tap expands it in place to the whole
// line. **A tap and not a hover**: every `:hover` in this app is inside
// `@media (hover: hover)` and a phone has none, so a reveal that needed a
// pointer would be a reveal a phone could not get at.
//
// Every figure is the broker's or the CLI's. This app computes none: `cost` is
// `cost_delta_usd`, the running total resets at a resume and is only ever shown
// with the word "total" beside it, and `context` is the host's reading of what
// the model's window held. All of them arrive on the session snapshot a moment
// after the turn ends, so a line drawn before that says less and then fills in.
// Unknown is nothing, never zero.
const openTurns = new Set()

function toggleTurn(key) {
  if (openTurns.has(key)) openTurns.delete(key)
  else openTurns.add(key)
  host.schedule()
}



// What to call the model under a turn: the provider's own name for it when its
// row lists the id — "GPT-6-Luna", not "Gpt 6" — and the id's short form when
// it does not. Claude Code's turns name full ids (`claude-opus-5`) where its
// row lists aliases (`opus`), so they take the short form, as they always did.
function turnModelName(id, provider = null) { return listedModelName(provider, id) ?? modelShortName(id) }

function turnFacts(n, provider = undefined) {
  const bits = []
  const model = turnModelName(n.model, provider)
  if (model) bits.push(model)
  if (n.effort) bits.push(n.effort)
  const context = contextWords(n.context)
  if (context) bits.push(context)
  if (n.usage.input !== null || n.usage.output !== null) bits.push(`${n.usage.input ?? 0} in / ${n.usage.output ?? 0} out`)
  if (n.durationMs !== null) bits.push(`${(n.durationMs / 1000).toFixed(1)}s`)
  if (typeof n.cost === 'number') bits.push(`$${n.cost.toFixed(4)}`)
  if (typeof n.totalCost === 'number' && n.totalCost !== n.cost) bits.push(`$${n.totalCost.toFixed(4)} total`)
  if (n.isError) bits.push(n.subtype ?? 'error')
  return bits
}

function turnLine(n, key = null) {
  const bits = turnFacts(n)
  // **Collapsed, the line is the model's name or it is nothing.** The owner saw a
  // turn line reading "0 in / 0 out" under a message on 2026-09-23: the figures
  // were the first thing the turn had, because the host had named no model for
  // it, and a raw token count is not what that line is for. A turn whose model
  // is unknown says nothing at all — the figures are still on the record and
  // still on the next turn's line, and one muted token that reads as noise is
  // worse than no token.
  //
  // Nothing to say is also nothing to tap, and nothing drawn: a turn whose
  // snapshot has not arrived yet has no line until it does. It was an empty
  // `div` until 2026-09-30, which is a node with padding and no words.
  const lead = turnModelName(n.model)
  if (bits.length === 0 || !lead) return null
  const mine = key ?? 'turn'
  const isOpen = openTurns.has(mine)
  return h('button', {
    class: `turn${n.isError ? ' err' : ''}${isOpen ? ' open' : ''}`,
    key,
    type: 'button',
    title: isOpen ? 'Hide what this turn ran on' : 'What this turn ran on, and what it cost',
    // Collapsed: the model's short name, which is `bits[0]` whenever there is
    // one — the whole line is what a tap is for.
    text: isOpen ? bits.join(' · ') : lead,
    onclick: () => toggleTurn(mine),
  })
}
return Object.defineProperties({}, {
    lineKeys: { enumerable: true, get: () => lineKeys },
    lineCount: { enumerable: true, get: () => lineCount, set: value => { lineCount = value } },
    keyOf: { enumerable: true, get: () => keyOf },
    MINE_MAX_PX: { enumerable: true, get: () => MINE_MAX_PX },
    MINE_SLACK_PX: { enumerable: true, get: () => MINE_SLACK_PX },
    fitMine: { enumerable: true, get: () => fitMine },
    COPIED_MS: { enumerable: true, get: () => COPIED_MS },
    copied: { enumerable: true, get: () => copied, set: value => { copied = value } },
    copyWithCue: { enumerable: true, get: () => copyWithCue },
    copyButton: { enumerable: true, get: () => copyButton },
    stepItems: { enumerable: true, get: () => stepItems },
    stepChildren: { enumerable: true, get: () => stepChildren },
    itemNode: { enumerable: true, get: () => itemNode },
    foldCard: { enumerable: true, get: () => foldCard },
    stepNode: { enumerable: true, get: () => stepNode },
    askLine: { enumerable: true, get: () => askLine },
    openBlocks: { enumerable: true, get: () => openBlocks },
    longToolDetails: { enumerable: true, get: () => longToolDetails },
    toggleOpen: { enumerable: true, get: () => toggleOpen },
    toolNode: { enumerable: true, get: () => toolNode },
    toolDetail: { enumerable: true, get: () => toolDetail },
    fitToolOutputs: { enumerable: true, get: () => fitToolOutputs },
    answeredLine: { enumerable: true, get: () => answeredLine },
    openTurns: { enumerable: true, get: () => openTurns },
    toggleTurn: { enumerable: true, get: () => toggleTurn },
    turnModelName: { enumerable: true, get: () => turnModelName },
    turnFacts: { enumerable: true, get: () => turnFacts },
    turnLine: { enumerable: true, get: () => turnLine }
})
}
