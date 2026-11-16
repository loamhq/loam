import { selectionMark } from './chat-ui.js'
import { h, kept, icon } from './ui.js'
import { askTarget, askReason, grantWords } from './asks.js'

// Shared permission and question cards for persona threads.
export function createPermissionCards({ schedule, answerPermission, answering = new Set() }) {
  const openBlocks = new Set()
  function toggleOpen(key) { if (openBlocks.has(key)) openBlocks.delete(key); else openBlocks.add(key); schedule() }
  function askRow(p, ctx, workspace) {
    const sending = answering.has(p.request_id)
    const grant = p.suggestions.length > 0 ? grantWords(p.suggestions, workspace) : ''
    const buttons = h(
      'div',
      { class: 'buttons' },
      h('button', { class: 'allow', type: 'button', disabled: sending, onclick: () => answerPermission(p.request_id, 'allow') }, icon.check(14), 'Allow'),
      p.suggestions.length > 0
        ? h('button', {
            class: 'allowchat',
            type: 'button',
            disabled: sending,
            text: 'Allow for this chat',
            onclick: () => answerPermission(p.request_id, 'allow', { updatedPermissions: p.suggestions }),
          })
        : null,
      h('button', { class: 'pillbtn deny', type: 'button', text: 'Deny', disabled: sending, onclick: () => answerPermission(p.request_id, 'deny') }),
    )
    const grantLine = grant ? h('div', { class: 'grant', text: `For this chat: ${grant}` }) : null
    const skill = skillAsked(p)
    if (skill) {
      const detail = p.decision_reason || p.description || ''
      const key = `why:${p.request_id}`
      const isOpen = openBlocks.has(key)
      // Anything the skill was asked to do beyond opening is still shown.
      const args = skill.args ? (typeof skill.args === 'string' ? skill.args : JSON.stringify(skill.args, null, 2)) : ''
      return h(
        'div',
        { class: 'askrow skill', key: `ask:${p.request_id}` },
        h('div', { class: 'asking', text: `${ctx?.agentName || 'The agent'} wants to open the skill ${skill.name}` }),
        detail
          ? h(
              'button',
              { class: `whyfold${isOpen ? ' open' : ''}`, type: 'button', 'aria-expanded': String(isOpen), onclick: () => toggleOpen(key) },
              h('span', { class: 'whyfold-label', text: 'Why' }),
              isOpen ? h('span', { class: 'why', text: detail }) : null,
            )
          : null,
        args ? h('pre', { text: args }) : null,
        grantLine,
        buttons,
      )
    }
    const target = askTarget(p.tool_name, p.input, workspace)
    const reason = askReason(p, ctx?.agentName)
    const input = typeof p.input === 'string' ? p.input : JSON.stringify(p.input ?? {}, null, 2)
    const key = `input:${p.request_id}`
    const isOpen = openBlocks.has(key)
    return h(
      'div',
      { class: 'askrow', key: `ask:${p.request_id}` },
      h('div', { class: 'askhead' }, h('span', { class: 'asktool', text: p.tool_name }), target ? h('span', { class: 'asktarget', text: target }) : null),
      reason ? h('div', { class: 'why', text: reason }) : null,
      input && input !== '{}'
        ? h(
            'button',
            { class: `inputfold${isOpen ? ' open' : ''}`, type: 'button', 'aria-expanded': String(isOpen), onclick: () => toggleOpen(key) },
            h('span', { class: 'whyfold-label', text: isOpen ? 'Hide details' : 'Details' }),
          )
        : null,
      isOpen ? h('pre', { text: input }) : null,
      grantLine,
      buttons,
    )
  }

  // The skill a `Skill` approval asks to open, or `null` for any other request —
  // including a `Skill` request whose input names no skill, which keeps the
  // ordinary card and its raw input rather than a sentence with a hole in it.
  function skillAsked(p) {
    if (p.tool_name !== 'Skill') return null
    const name = typeof p.input?.skill === 'string' ? p.input.skill.trim() : ''
    return name ? { name, args: p.input.args ?? null } : null
  }

  // Keep custom and secret answer drafts in stable DOM fields until resolved.
  const questionDrafts = new Map()
  const OTHER = Symbol('other')

  function questionDraft(p) {
    if (!questionDrafts.has(p.request_id)) {
      // `refused` is the host's sentence for the last answer it would not take,
      // until the person changes something.
      const draft = { chosen: new Map(), fields: new Map(), sending: false, refused: null }
      for (const q of p.questions) {
        draft.chosen.set(q.id, new Set())
        if (!q.other) continue
        const field = h('input', {
          class: 'qfield',
          type: q.secret ? 'password' : 'text',
          autocomplete: 'off',
          placeholder: q.options.length === 0 ? 'Type your answer' : 'Something else',
          'aria-label': q.header || q.question || 'Your answer',
          oninput: () => {
            // Typing is choosing Other: on a one-choice question it is instead of
            // the options, on a several-choice one it is beside them.
            const chosen = draft.chosen.get(q.id)
            draft.refused = null
            if (field.value.trim() !== '') {
              if (!q.multi) chosen.clear()
              chosen.add(OTHER)
            }
            schedule()
          },
          onkeydown: (e) => {
            // Enter sends when this field is the whole of the card.
            if (e.key !== 'Enter' || p.questions.length !== 1 || q.options.length !== 0) return
            e.preventDefault()
            sendAnswers(p)
          },
        })
        draft.fields.set(q.id, field)
      }
      questionDrafts.set(p.request_id, draft)
    }
    return questionDrafts.get(p.request_id)
  }

  // One question's answer as it would be sent: the chosen labels in the order the
  // options are listed, and the typed value as typed. Other chosen with nothing
  // typed in it is not an answer.
  function answerOf(q, draft) {
    const chosen = draft.chosen.get(q.id)
    const out = q.options.filter((o) => chosen.has(o.label)).map((o) => o.label)
    const field = draft.fields.get(q.id)
    const typed = field ? field.value : ''
    if (field && (chosen.has(OTHER) || q.options.length === 0) && typed.trim() !== '') out.push(typed)
    return out
  }

  // Only the questions that have an answer: an optional one left empty has no
  // key at all, which is what the host's contract names for it.
  function answersOf(p, draft) {
    const answers = {}
    for (const q of p.questions) {
      const answer = answerOf(q, draft)
      if (answer.length > 0) answers[q.id] = answer
    }
    return answers
  }

  function questionReady(p, draft) {
    return p.questions.every((q) => !q.required || answerOf(q, draft).length > 0)
  }

  function choose(p, q, value) {
    const draft = questionDraft(p)
    const chosen = draft.chosen.get(q.id)
    draft.refused = null
    if (q.multi) {
      if (chosen.has(value)) chosen.delete(value)
      else chosen.add(value)
    } else {
      chosen.clear()
      chosen.add(value)
    }
    // Choosing Other puts the person in its field.
    if (value === OTHER && chosen.has(OTHER)) {
      const field = draft.fields.get(q.id)
      requestAnimationFrame(() => field?.focus?.())
    }
    schedule()
  }

  async function sendAnswers(p) {
    const draft = questionDraft(p)
    if (draft.sending || !questionReady(p, draft)) return
    draft.sending = true
    draft.refused = null
    schedule()
    settle(draft, await answerPermission(p.request_id, 'allow', { answers: answersOf(p, draft) }, { onCard: true }))
  }

  async function declineQuestion(p) {
    const draft = questionDraft(p)
    if (draft.sending) return
    draft.sending = true
    draft.refused = null
    schedule()
    settle(draft, await answerPermission(p.request_id, 'deny', null, { onCard: true }))
  }

  // What an answer's send leaves on the card: nothing when it went, the card live
  // again when it did not, and the host's sentence on it when the host refused it.
  function settle(draft, outcome) {
    if (outcome === true) return
    draft.sending = false
    if (typeof outcome === 'string') draft.refused = outcome
    schedule()
  }

  function questionCard(p) {
    return h(
      'div',
      { class: 'line left', key: `ask:${p.request_id}` },
      h('div', { class: 'approve question', role: 'form', 'aria-label': 'A question from the agent' }, ...questionBody(p)),
    )
  }

  // The form itself, for the card alone or for a row among approvals.
  function questionBody(p) {
    const draft = questionDraft(p)
    const ready = questionReady(p, draft)
    return [
        h('div', { class: 'kicker', text: p.questions.length === 1 ? 'Needs your answer' : `${p.questions.length} questions` }),
        p.message ? h('div', { class: 'why', text: p.message }) : null,
        ...p.questions.map((q) => questionBlock(p, q, draft)),
        draft.refused ? h('div', { class: 'qrefused', role: 'alert', text: draft.refused }) : null,
        h(
          'div',
          { class: 'buttons' },
          h(
            'button',
            {
              class: 'allow',
              type: 'button',
              disabled: !ready || draft.sending,
              onclick: () => sendAnswers(p),
            },
            icon.check(14),
            'Send',
          ),
          h('button', {
            class: 'pillbtn deny',
            type: 'button',
            text: 'Decline',
            title: 'The agent is told you declined to answer',
            disabled: draft.sending,
            onclick: () => declineQuestion(p),
          }),
        ),
    ]
  }

  function questionBlock(p, q, draft) {
    const chosen = draft.chosen.get(q.id)
    const role = q.multi ? 'checkbox' : 'radio'
    const row = (key, value, label, description, preview) =>
      h(
        'div',
        { class: `qopt${chosen.has(value) ? ' on' : ''}`, key },
        h(
          'button',
          {
            class: 'qpick',
            type: 'button',
            role,
            'aria-checked': String(chosen.has(value)),
            onclick: () => choose(p, q, value),
          },
          selectionMark({ kind: role, chosen: chosen.has(value) }),
          h(
            'span',
            { class: 'qtext' },
            h('span', { class: 'qlabel', text: label }),
            description ? h('span', { class: 'qdesc', text: description }) : null,
          ),
        ),
        // A preview is the option shown rather than described — a snippet, a
        // layout — and it is folded, because it is the long part.
        preview ? h('details', { class: 'qpreview' }, h('summary', { text: 'Preview' }), h('pre', { text: preview })) : null,
      )
    const field = draft.fields.get(q.id)
    return h(
      'div',
      { class: 'qblock', key: `q:${q.id}`, role: q.multi ? 'group' : 'radiogroup', 'aria-label': q.header || q.question },
      q.header ? h('div', { class: 'qhead', text: q.header }) : null,
      q.question ? h('div', { class: 'qask', text: q.question }) : null,
      q.multi && q.options.length > 0 ? h('div', { class: 'qhint', text: 'Choose any' }) : null,
      ...q.options.map((o, i) => row(`o${i}`, o.label, o.label, o.description, o.preview)),
      q.other && q.options.length > 0 ? row('other', OTHER, 'Other', null, null) : null,
      field && (q.options.length === 0 || chosen.has(OTHER)) ? h('div', { class: 'qother' }, kept(field)) : null,
    )
  }


  return { askRow, questionBody, questionCard, questionDrafts }
}
