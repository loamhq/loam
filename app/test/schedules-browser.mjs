// Both legacy entry points run the shared contract browser checks.
if (process.env.SCHEDULE_EVIDENCE) process.env.CONTRACT_EVIDENCE ||= process.env.SCHEDULE_EVIDENCE
await import('./schedules-activity-browser.mjs')
