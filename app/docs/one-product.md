# One product: chats with several authors

Design decision, 2026-11-09. The classic chat is the reference for every shared
surface. A thread adds several authors, mentions, membership and a board to that
conversation; it must not look or behave like a second application.

This document records the approved brief inside the repository. It supersedes
the earlier thread presentation where those descriptions disagree. The broker
contract and durable behavior in [threads and boards](threads-and-boards.md)
remain authoritative. [Shared component inventory](design-system/shared-chat-components.md)
and [paired evidence](evidence/one-product/README.md) document the implementation.

## Layout and reading

On desktop, the rail and list sit on the app background. The conversation is the
classic inset rounded card, with its existing gutter, radius and ground. No
hairlines divide the list, header or board. The board opens from a round header
icon as a dismissible panel inside that card, rather than a third ruled column.

Both views use the same centered reading column and markdown, with the existing
measure for prose and the wider freedom for code/tables. Agent text is unboxed.
The person's messages use the exact classic bubble and long-message expansion.
Joins attributable to mentions in one visible human message share one system
line; separate human messages keep separate join lines. This is a display-only
view over the runtime’s individual durable join records.
One small avatar/name line begins a new speaker's run; consecutive messages by
that author do not repeat it. System notes are quiet and centered. Work uses the
classic expandable step fold and its live indicator. Thinking and opening use
the classic indicators. No second tray or agent bubble is introduced.

## Header and list

Use the classic responsive header, rename field and round icon controls. The
thread icon/default avatar opens details; the title renames inline when allowed.
Members, Board and actions are round controls. Both layouts show one default
face/mark, or the people icon without a default, followed by an inline +N.
General always uses its own people icon in the list, header, loader and details;
its icon picker is omitted. Other threads retain their custom/default identity. Board has an unseen
indicator. The actions menu includes details, pin, archive and deletion where
allowed. Header deletion confirms in place and preserves the conversation view.
General keeps its special restrictions.

The list uses classic's title/actions header, search expansion, row anatomy,
avatar/ring, title, last speaker, time, provider mark, unread preview and selected
hue treatment. Unread uses the classic ring/preview; working uses classic’s
activity dot. Board updates are indicated inside the thread, not by another
list-row dot. New thread is a compose icon, not a text pill. The agent grid
wraps on both layouts without clipping; it can be hidden from list actions. The
sidebar collapses and reopens with the same house movement. Pinned comes first;
the archive door stays below the rows and appears only when an archive exists.

## Composer, board and details

The existing composer remains one implementation: markdown, dictation, terms,
attachments, Files picking, expanded editor, mentions and model control. Restore
the hint line with the send chord on desktop and “@ to bring someone in” for a
thread. The mention button shares the + button’s round chrome and states.
With a default agent the placeholder is “Message <agent>”; only no-default
threads invite a thought or mention. Model fallback copy is “Agent’s default”. Recognized mentions expose the model
chip before agents join, including in a no-default thread. A selected model is
sent for that message and applies to all its addressed agents. Shared choices
come from the providers; an empty intersection explains that each uses its
default rather than hiding the control.
Plain Enter remains a newline in both views, including on a phone.

Board chrome uses classic panel headings and icon buttons for close/edit.
Add and durations use small sans-serif pills. Author/new/until metadata uses the
existing quiet scale. Add, edit, complete, seen, remove, duration and conflict
behavior must preserve the exact stored markdown and draft safety. Board items
keep fixed checkbox/remove columns across tinted and plain rows. Text uses the
remaining width; optional metadata takes its content width immediately before
remove and stays on one line. On phones, metadata flows below longer text while
short text can keep it alongside. First-line controls align, section headings
have symmetric spacing and Mark seen shares the new-items count’s baseline.

Details uses the house settings navigation, rows, icon tiles, labels and
chevrons. Its opaque header stays above an independently scrolling content
area; navigation scrolls only that area. The left nav omits a repeated title,
and add-member chips separate + from the name with a space. Members use settings rows. Default remains chips, with help only for
Nobody. Engine/model choices use the classic model pill and settings-menu rows,
with keyboard navigation and a clear selected value. Rename, icon, membership,
model/engine, default, pin, archive, deletion and opening the board remain.

## Finishing details

The current [application shape](app-shape.md) supersedes the earlier rail theme
tile, Classic chats destination and Activity count. Appearance now has one
home in Settings; Activity carries a live dot. Member identity, first-message
titles, default selection and thread unread dots retain their shared components.

## Quality and verification

Keep every working capability, including live list/reconnect, permissions and
questions, authentication expiry, provider controls and all classic features.
Use the existing tokens, type, radii and motion. New mobile controls have 44 px
hit targets; hover, pressed, focus, empty/loading and reduced-motion states must
match the house patterns.

Commit side-by-side thread/classic evidence for list, multi-author chat and
steps, header, composer, board, details and empty states at 390/1440 in light/dark.
A capability unique to threads uses the classic panel/settings chrome as its
comparison; it does not invent a classic board. Run the full suite and, at the
end, verify a real thread with two agent replies and visible steps, then delete
that test thread. Deployment follows review of the pairs and the running app by
the platform operator; this job does not deploy itself.
