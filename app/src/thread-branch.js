// The broker chooses the context and prompt. This web half owns the existing
// text-model credential when configured; neither key nor prompt goes to the
// browser. Without a key, the runtime’s record-based fallback is the editable
// opening. Branch creation never depends on a vendor account.
export function createBranchDrafter(config, { fetchImpl = fetch, timeoutMs = 20_000, getKey = async () => config.geminiApiKey || '' } = {}) {
  return async function draftBranch(reading) {
    const fallback = reading.fallback
    if (typeof fallback?.title !== 'string' || typeof fallback?.draft !== 'string') throw new Error('The runtime did not supply a thread opening.')
    let opening = fallback
    const key = await getKey()
    if (key && reading.summarizer) {
      try {
        const res = await fetchImpl(`${config.geminiUrl}/v1beta/interactions`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'x-goog-api-key': key },
          body: JSON.stringify({ model: config.speech.briefModel, system_instruction: reading.summarizer.system_instruction,
            input: reading.summarizer.input, store: false }),
          signal: AbortSignal.timeout(timeoutMs),
        })
        if (!res.ok) throw new Error('Draft unavailable')
        const body = await res.json()
        const text = (body.steps ?? []).filter((step) => step.type === 'model_output')
          .flatMap((step) => step.content ?? []).filter((part) => part.type === 'text').map((part) => part.text).join('\n').trim()
        const answer = JSON.parse(text.replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, ''))
        if (body.status !== 'completed' || typeof answer.title !== 'string' || !answer.title.trim()
          || typeof answer.draft !== 'string' || !answer.draft.trim()) throw new Error('Unreadable draft')
        opening = { title: answer.title.trim(), draft: answer.draft.trim() }
      } catch { /* A model failure still leaves an editable record-based opening. */ }
    }
    return { thread: reading.thread, message: reading.message, persona: reading.persona, people: reading.people, ...opening }
  }
}
