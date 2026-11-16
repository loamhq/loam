# Agent profiles, lifecycle and plugin settings

The approved U3 brief supersedes the old prototype's New-agent form and its
scrolling profile. The profile's header stays above Threads, Schedules, Settings
and Files. New thread is primary; Read soul opens the source file. Change by
talking opens a new thread with an unsent opening about changing the soul.
No role or descriptor replaces the quotation. The visible profile refreshes its
host status every fifteen seconds, preserving tabs and refusing stale reads
that predate an edit. The quote is read through Files
and contains the source prose's first two sentences, excluding frontmatter and
headings. A read failure is shown without inventing a description.

Threads pages the complete host summary list, including archived threads, twelve
at a time. It includes both default-agent and member-agent matches. Conversation
history continues to use its existing broker pagination. Profile schedules,
execution choices, Files refs and plugin fields keep their original routes.

Settings → Agents → New agent calls `POST /api/agents/new` and opens the returned
conversation. The host chooses the facilitating agent; the person writes first.
The page reads conversation drafts on open and when a turn finishes. Review
shows the soul/config and revision. Create agent sends the displayed `rev`;
a stale-revision refusal reloads the drafts and requires another explicit click.
Confirmation waits up to 21 minutes at the relay, allowing the host's documented
20-minute landing lock, and shows progress throughout. No form creates an agent.

Archive is in the profile's menu. One confirmation says the agent leaves Chats,
pickers and mentions, schedules stop, and threads and files stay. It offers
Cancel, then waits with progress on Archive. The returned agent card updates the
roster and invalidates the thread picker's separate cache. Archived lists only
cards carrying `archived` from `GET /api/agents?archived=1`; Restore applies the
host's returned card. Existing thread records and histories remain available.

Face uploads are unparsed multipart bytes, one `file`, relayed with PUT. The
host enforces supported types and the 2 MB file cap. The relay allows a small
multipart-envelope allowance and rejects larger bodies before forwarding. Image
URLs are served by the authenticated relay; digest query parameters and cache
headers survive. All avatar components prefer the uploaded image over theme art.

Daily notes is controlled by the host's `daily-notes` plugin. Its own plugin
detail contains the switch and folder field. Files only offers Open daily note
when that plugin is on with a known folder, rechecks before opening, and uses
that folder and the existing installation-time-zone date format. No web daily
folder environment variable, installation default or localStorage override
remains. Disabling never removes a folder or note.

Dictation and Read aloud declare `settings.secret` in their manifests. Their
Voice settings and plugin details show status and the broker's check/refusal
sentences, with paste/save, Check again and Forget actions. A pasted value is
cleared from the form on submit and is never stored by the page. Forget can
reveal the host's fallback key (`source: box`); the page reports that status.

The web process no longer reads `GEMINI_API_KEY` from its environment. It calls
`GET /v1/plugins/<id>/secrets/GEMINI_API_KEY/value` server-side at use: Dictation
for transcription/polish, Read aloud for synthesis/briefs and optional thread
opening drafts. A paste/forget needs no restart. Speech health is refreshed after
key changes; missing keys leave the ordinary editable branch fallback intact.
The browser relay allows metadata and key actions only, never `/value`, including
encoded-separator attempts. Neither key nor broker bearer goes to a browser,
log, audio sidecar or repository.

The required vault decision note and a live broker were not supplied in this
job environment. Implementation follows the detailed brief; live creation,
archive/restore and source-box credential acceptance remain staging checks.
All automated writes use isolated fixtures.

Run `npm test`. The opt-in four-case browser walk is:

```sh
LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs U3_EVIDENCE=/path/to/captures node test/release-u3-browser.mjs
```

It uses loopback fixtures and blocks external requests. The
[evidence record](evidence/first-release-u3/README.md) identifies commands,
results, screenshots and verification limits. This is a page increment with no
version bump or release tag requested, and no deployment.
