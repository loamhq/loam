# Agent profiles and execution settings

Settings → Agents has three groups: Agents (face and name), Engineers (platform
first, projects by name, then Every other project), and System (Nightly reviewer
with its schedule). The existing Chats face menu opens the same agent profile.
Names have no descriptive subtitle; Soul carries that information.

The profile reads the host's `GET /api/agents/<id>` record. Its fixed header
keeps the face, editable name, current status, quoted first two soul sentences,
Read soul, Change by talking and primary New thread above the tabs. Threads
opens first and pages all matching thread summaries twelve at a time. The host
currently supplies the complete summary list; this is display pagination, not
conversation-history paging. Threads in archives remain readable. Activity
shows the same history filters as the main Activity page, scoped to this agent.

Schedules contains wakes/crons and Manage schedules. Settings contains execution
defaults and enabled plugin profile contributions, including voice. Files opens
the workspace, Soul, Config, memory files named by their loading time and Skills.
The header stays above each tab's independent scroller on phones and desktop,
including the desktop Settings dialog.

Back from a file or folder opened from a main-pane profile returns to the
profile and the tab it was opened from. Settings profiles stay inside the
Settings popup at both sizes. A file opened from a Settings profile returns to
the underlying panel on Back. Use the Agents chevron within Settings to return
to the Agents list.

Tap the face for image upload or removal. Uploaded faces come
from the host card, have digest-addressed URLs, and take priority over every
theme's art. Removing the image restores the selected theme face. Tap the
name to rename; conflicts leave the field and the host's sentence in place.
The stable id never changes.

See [U3](first-release-u3.md) for conversation-based creation, revision-confirmed
drafts, archive/restore and the host handoff that remains outside this repo.

Provider, Model and Thinking save through `PATCH /api/agents/<id>`. The
existing `/api/providers` catalog supplies models and per-model thinking
levels; the page keeps no parallel list. Switching provider lets the host
clear model/effort. Switching model clears an incompatible thinking choice.
A short page-written footnote explains where defaults apply. A non-empty
`not_honoured` adds a separate quiet line that wakes and scheduled runs stay
on Claude Code for now; server implementation notes are not shown verbatim.
Existing thread members retain their own provider, and thread overrides win.

Schedules show plain cadence, next run and an enabled switch. Expand a row
for its last run/result, timing field and instructions. Manage schedules keeps
the complete file list, host change marks, Run now, editing and task creation.
Engineer profiles retain Recent jobs and the project-filtered full activity view.

Engineer settings use `/api/engineers[/<project or default>]`. Allowed
providers come from that row. A single allowed provider is shown as fixed text.
Model and thinking choices come from the same provider catalog. A project with
an own setting can return to its default. Deploy when a job lands appears only
for a non-null `deploy_on_land`. Instructions without a Files root are plain
explanatory text, with no link, chevron or folder icon. Recent jobs and All jobs reuse Activity job rows,
filtered by project; pagination can load older host pages. Every other project
has neither instructions nor jobs. Changes apply to the next job.

Engineers also have Quick, Standard and Hard model pickers directly beneath
Model, with short hints for small mechanical jobs, most work, and design-heavy
and subtle work. Each uses the effective provider's same model catalog.
`effective.tiers.<tier>` supplies the selected model and `from` (`tier` or
`model`); a fallback reads “Default · <model name>”. An unavailable selected
model remains visible as a disabled option, as in Model.

Each picker sends only `{"tiers":{"<tier>":"<model id>"}}` through the existing
`PATCH /api/engineers/<project or default>` route. Selecting Default sends
`null` for that tier and restores the engineer's model. The answer is the
whole engineer listing; the controller replaces both the listing and the
open detail from it. A provider change clears tiers on the host, so all three
pickers redraw from that answer with the new catalog and fallback. Host
refusals remain visible and restore the saved choice. Personas use the same
host PATCH contract (`engineer.tiers.<tier>`); there is no page-only store
or write route. Agent and reviewer profiles retain their existing pickers.

The tier contract was supplied in the implementation brief. Its referenced
host receipt was not available in the checkout or through connected tools;
no live host settings were read or changed. See
[tier verification and captures](evidence/engineer-tiers/README.md).

The reviewer uses `/api/system/reviewer`. Its time picker preserves the
existing recurrence and PT grammar; Recurrence can edit the entire expression
for weekdays or named days. Model and thinking apply from the next run. Last
run and the instructions reference come from the host. Saving the schedule is
a host timer change, never a request to start a review.

Plugins declare `agent_settings` and the `agent.profile` place. Enabled plugin
fields render as picker/text rows using the generic plugin route; custom page
code can render the public place around `defaultView()`. Core profile code has
no read-aloud-specific branch. The shipped Read aloud manifest declares the
same thirty prebuilt voices the speech service accepts, with Charon as its
default. Both speech preparation and cache lookup read `stored.voice` from the
plugin's per-agent route. No stored voice, an off plugin, or an unavailable
older host preserves `SPEECH_VOICES` and its character instructions. Document
speech retains its separate device-local Files voice. Settings → Voice has no
per-agent list to migrate.

Host-backed profile choices are never stored in localStorage. Agent and Settings
profiles have independent controllers so opening an engineer over an agent's
page cannot replace its identity, pending edits or activity scope. Generation
checks ignore stale reads and writes after navigation.

Run `npm test` for the automatic suite. For real-browser checks, provide an
external Playwright installation and Chromium, then run:

```sh
LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs LOAM_CHROME=/path/to/chromium PROFILE_EVIDENCE=/path/to/captures node test/agent-profile-browser.mjs
```

The fixture exercises the real page and relay without real agents or credentials
at 390 and 1440, light and dark. Captures and the verification record live in
[evidence/agent-profiles](evidence/agent-profiles/README.md).
