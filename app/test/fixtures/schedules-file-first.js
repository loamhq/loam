// Supplied 2026-10-15 broker response, including its shortened Activity items.
// Only the scratch agent identity is parameterized for the page fixture.
const example = {
  "schedules": [
    {
      "agent": "scratch", "name": "morning-digest", "kind": "recurring",
      "state": "broken", "running": "previous",
      "state_words": "Not running this file. Line 2: cannot parse schedule 'weekdays at 7am'. A schedule reads `<when> at HH:MM PT`, where <when> is daily, weekdays, weekends, or days such as `Sundays` or `Mondays and Thursdays`. The previous version still runs (weekdays at 07:00 PT).",
      "problem": {
        "why": "cannot parse schedule 'weekdays at 7am'. A schedule reads `<when> at HH:MM PT`, where <when> is daily, weekdays, weekends, or days such as `Sundays` or `Mondays and Thursdays`.",
        "line": 2, "text": "schedule: weekdays at 7am"
      },
      "schedule": null, "words": null, "note": null, "enabled": true, "done": null,
      "body": "Write the morning digest.", "instruction": null,
      "timer": "loam-cron-scratch-morning-digest.timer",
      "file": {"root": "spaces", "path": "scratch/crons/morning-digest.md"},
      "last_fire": null,
      "last_change": {"id": "493ccc598d83", "…": "Activity change item, by owner"},
      "last_edit": {"id": "8253cc34cae0", "ts": "2026-10-15T15:42:05Z", "actor": "owner", "by": "owner", "lane": "app", "taken": false},
      "changed_since_seen": false, "unseen": [],
      "refused": {"id": "8253cc34cae0", "…": "Activity refused item, direct, by owner"},
      "error": "cannot parse schedule 'weekdays at 7am'. The grammar is:\n\n …",
      "next_fire": "2026-10-16T14:00:00Z"
    },
    {
      "agent": "scratch", "name": "inbox-sweep", "kind": "recurring",
      "state": "refused", "running": "previous",
      "state_words": "Not running this file: the change was refused. A change by scratch carries a reason, and this one has none. Say why the schedule is changing. The previous version still runs (daily at 12:00 PT).",
      "problem": {"why": "a change by scratch carries a reason, and this one has none. Say why the schedule is changing.", "line": null, "text": null},
      "schedule": "daily at 12:30 PT", "words": "daily at 12:30 PT", "note": "", "enabled": true, "done": "",
      "body": "Sweep the inbox.", "instruction": null,
      "timer": "loam-cron-scratch-inbox-sweep.timer",
      "file": {"root": "spaces", "path": "scratch/crons/inbox-sweep.md"},
      "last_fire": null,
      "last_change": {"id": "e08b3c32e44e", "…": "the agent's earlier, accepted edit"},
      "last_edit": {"id": "38e9397d910e", "ts": "2026-10-15T15:42:05Z", "actor": "scratch", "by": "agent", "lane": "unknown", "taken": false},
      "changed_since_seen": true, "unseen": ["e08b3c32e44e", "38e9397d910e"],
      "refused": {"id": "38e9397d910e", "…": "Activity refused item, direct"},
      "error": null,
      "next_fire": "2026-10-16T14:00:00Z"
    },
    {
      "agent": "scratch", "name": "drafts", "kind": "recurring",
      "state": "off", "running": "none",
      "state_words": "Off: the file says it is not enabled.",
      "problem": null,
      "schedule": "Sundays at 05:00 PT", "words": "Sundays at 05:00 PT", "note": "", "enabled": false, "done": "",
      "body": "Tidy the drafts.", "instruction": null,
      "timer": "loam-cron-scratch-drafts.timer",
      "file": {"root": "spaces", "path": "scratch/crons/drafts.md"},
      "last_fire": null,
      "last_change": {"id": "2fe62eb15a8e", "…": "Activity change item"},
      "last_edit": {"id": "2fe62eb15a8e", "ts": "2026-10-15T15:42:05Z", "actor": "scratch", "by": "agent", "lane": "unknown", "taken": true},
      "changed_since_seen": true, "unseen": ["2fe62eb15a8e"],
      "refused": null, "error": null,
      "next_fire": null
    },
    {
      "agent": "scratch", "name": "reflection", "kind": "reflection",
      "state": "live", "running": "this", "state_words": "Live.", "problem": null,
      "schedule": "daily at 02:00 PT", "words": "daily at 02:00 PT", "note": "", "enabled": true, "done": "",
      "body": "This is your daily wake. Use it to keep learning and keep your workspace up.",
      "instruction": null,
      "timer": "loam-wake-scratch.timer",
      "file": {"root": "spaces", "path": "scratch/crons/reflection.md"},
      "last_fire": null,
      "last_change": {"id": "d4d66a2c26d0", "…": "Activity change item, by engineer"},
      "last_edit": {"id": "d4d66a2c26d0", "ts": "2026-10-15T15:42:05Z", "actor": "engineer", "by": "operator", "lane": "engineer", "taken": true},
      "changed_since_seen": true, "unseen": ["d4d66a2c26d0"],
      "refused": null, "error": null,
      "next_fire": "2026-10-16T14:00:00Z"
    }
  ],
  "unread": 4
}
export function fileFirstSchedules(agent = 'scratch') {
  return JSON.parse(JSON.stringify(example).replaceAll('scratch', agent))
}
export const goodScheduleText = '---\nschedule: weekdays at 07:00 PT\nenabled: true\n---\nWrite the morning digest.\n'
export const brokenScheduleText = '---\nschedule: weekdays at 7am\nenabled: true\n---\nWrite the morning digest.\n'
