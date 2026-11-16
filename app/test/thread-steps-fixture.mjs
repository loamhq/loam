// Synthetic full-record contract. Never copies a private conversation/output.
export const intro = 'I’ll read the notes and check the plan before making a recommendation.'
export const conclusion = 'The notes are ready. Leave a little room for the unexpected, and keep the next step close to the conversation.'
export const longOutput = Array.from({ length: 90 }, (_, i) => `Check ${String(i + 1).padStart(2, '0')}: the weekly notes are ready to review.`).join('\n')
export const fullSteps = [
  { type: 'text', text: intro, parent: null, message: { model: 'claude-sonnet-4-6' } },
  { type: 'thinking', thinking: 'Read first, then check the details.', parent: null },
  { type: 'tool', id: 'read', name: 'Read', input: { file_path: 'notes.md' }, output: 'The notes are ready.', is_error: false, parent: null },
  { type: 'tool', id: 'check', name: 'Bash', input: { command: 'check weekly-notes', description: 'Check the weekly notes' }, output: longOutput, is_error: false, parent: null },
  { type: 'tool', id: 'agent', name: 'Agent', input: { description: 'Review the plan', subagent_type: 'Reviewer', prompt: 'Read the plan and report useful details.' }, output: 'The review is complete.', is_error: false, parent: null },
  { type: 'text', text: 'I’ll check the smaller details.', parent: 'agent' },
  { type: 'tool', id: 'child', name: 'Read', input: { file_path: 'plan.md' }, output: [{ type: 'text', text: 'Leave room between commitments.' }], is_error: false, parent: 'agent' },
  { type: 'text', text: 'The plan leaves enough room.', parent: 'agent' },
  { type: 'tool', id: 'missing', name: 'Read', input: { file_path: 'missing.md' }, output: 'The optional note could not be found.', is_error: true, parent: null },
  { type: 'permission', event: { type: 'broker.permission_request', request_id: 'approve', tool_name: 'Read', input: { file_path: 'notes.md' } } },
  { type: 'permission_resolved', event: { type: 'broker.permission_resolved', request_id: 'approve', behavior: 'allow', tool_name: 'Read' } },
  { type: 'permission', event: { type: 'broker.permission_request', kind: 'question', request_id: 'question', tool_name: 'AskUserQuestion', questions: [{ id: 'pace', header: 'Pace', question: 'Which pace should we use?', options: [{ label: 'A little room' }] }] } },
  { type: 'permission_resolved', event: { type: 'broker.permission_resolved', kind: 'question', request_id: 'question', behavior: 'allow', answers: { pace: ['A little room'] } } },
  { type: 'text', text: conclusion, parent: null },
  { type: 'end', event: { type: 'result', subtype: 'success', usage: { input_tokens: 1800, output_tokens: 210 }, total_cost_usd: 0.02, duration_ms: 1400, is_error: false } },
]
export function classicEvents() {
  const events = [{ type: 'broker.user_message', text: 'Can you check the notes?' }]
  fullSteps.forEach((step, i) => {
    if (step.type === 'text' || step.type === 'thinking') events.push({ type: 'assistant', parent_tool_use_id: step.parent,
      message: { id: `assistant-${i}`, ...step.message, content: [step.type === 'text' ? { type: 'text', text: step.text } : { type: 'thinking', thinking: step.thinking }] } })
    else if (step.type === 'tool') {
      events.push({ type: 'assistant', parent_tool_use_id: step.parent, message: { id: `assistant-${i}`, content: [{ type: 'tool_use', id: step.id, name: step.name, input: step.input }] } })
      events.push({ type: 'user', parent_tool_use_id: step.parent, message: { content: [{ type: 'tool_result', tool_use_id: step.id, content: step.output, is_error: step.is_error }] } })
    } else events.push(step.event)
  })
  return events.map((event, i) => ({ ...event, seq: i + 1 }))
}
