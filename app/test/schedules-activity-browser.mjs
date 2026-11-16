// Compatibility entry point: both current page drivers, with separate paints.
import path from 'node:path'
const output = process.env.CONTRACT_EVIDENCE
if (!output) throw new Error('Set CONTRACT_EVIDENCE to a directory outside the repository')
process.env.SCHEDULE_FILE_EVIDENCE ||= path.join(output, 'schedules')
process.env.ACTIVITY_COMPACT_EVIDENCE ||= path.join(output, 'activity')
await import('./schedules-file-first-browser.mjs')
await import('./activity-compact-browser.mjs')
