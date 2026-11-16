# Read-aloud summary shapes

The reference texts were generated in the Codex implementation session from
the new production instructions and synthetic fixtures. They were reviewed
against those fixtures and replayed through the production speech service
with a fake Gemini and fake encoder. They are **not live Gemini outputs or
listened-to audio**: no `GEMINI_API_KEY` was supplied. A live writer/audio
check remains unverified.

| Mode | Source | Exported instructions and input | Reference output |
| --- | --- | --- | --- |
| Files, 1 minute (135–165 words) | [Document fixture](../../../test/fixtures/summary-document.md) | [Document prompt](document-prompt.txt) | [145-word document summary](document-summary.txt) |
| Chat (300-word ceiling) | [Reply fixture](../../../test/fixtures/summary-chat.md) | [Chat prompt](chat-prompt.txt) | [159-word chat brief](chat-brief.txt) |

The document example follows purpose/scope, results, cost, then decisions.
Its dates, packet labels, amounts and decisions retain the source's precision;
it adds no attention section. The chat fixture puts its request first, while
the brief closes with the decision and deadline after summarising the work.
The exclusion of audio recordings qualifies that closing decision.

The prompts are exported from `documentBriefInstruction(150)` and
`briefInstruction(300)` in [src/speech.js](../../../src/speech.js), with the
source inputs appended. The document's frontmatter is removed by the existing
converter. The fixtures contain no installation or private document data.

The document prompt was tightened on 2026-10-24 to preserve meaning-changing
qualifiers, scope, conditions, negation and range bounds, and to omit whole
points when the budget is tight. The reference output predates that tightening;
it remains a pipeline fixture, not evidence of Gemini obeying the new rule.
The server test checks the exported document prompt against the production
instruction and pins the rule on both the first draft and length repair.
The [qualifier-rule diff](qualifier-rule.patch) and
[verification output](qualifier-verification.txt) record this follow-up.

[Server tests](../../../test/speech-server.test.js) pin the instructions at
the writer boundary, including a document rewrite after a short first draft
and the chat instruction to omit an empty attention section. They check that
cleanup, chunking and TTS receive the complete reference texts in order.
These tests establish prompt routing and pipeline behavior; a fake writer
does not evaluate whether Gemini will obey a prompt on arbitrary documents.

[Verification](verification.txt) records commands and results. The optional
Chromium driver gained visible and accessible **Document summary** assertions;
its run stopped before rendering because a browser system library was absent.
The regular phone/desktop chat UI tests passed with the revised menu wording.
