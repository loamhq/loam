// Fold only jobs with an explicit host chain. The newest outcome/receipt wins;
// loaded pages may be partial, so continuation counts also use the host field.
export function foldJobChains(items) {
  const chains = new Map(), result = []
  for (const item of [...items].sort((a, b) => (Date.parse(b.ts) || 0) - (Date.parse(a.ts) || 0))) {
    if (item.kind !== 'job' || !item.chain) { result.push(item); continue }
    if (!chains.has(item.chain)) {
      const row = { ...item, continuationCount: Math.max(0, item.auto_continuation || 0) }
      chains.set(item.chain, { row, ids: new Set([item.job || item.id]) }); result.push(row)
    } else {
      const group = chains.get(item.chain)
      group.ids.add(item.job || item.id)
      group.row.continuationCount = Math.max(group.row.continuationCount, group.ids.size - 1, item.auto_continuation || 0)
    }
  }
  return result
}

export const engineLabel = engine => ({ 'claude-code': 'Claude Code', claude: 'Claude Code', codex: 'Codex', antigravity: 'Antigravity' })[engine] || engine || null
export const jobThread = job => job.thread || null
export function jobContext(job) {
  return job.caller === 'wake' ? 'from a wake' : job.caller === 'cron' ? 'from a schedule'
    : job.caller === 'reviewer' ? 'from a review' : job.caller === 'job' ? 'from an engineer' : null
}
