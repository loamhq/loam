// Both legacy entry points run the shared contract browser checks.
if (process.env.ACTIVITY_EVIDENCE) process.env.CONTRACT_EVIDENCE ||= process.env.ACTIVITY_EVIDENCE
await import('./schedules-activity-browser.mjs')
