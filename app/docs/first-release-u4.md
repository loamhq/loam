# Claim, first hello, sign-in and updates

The claim service serves `public/claim.html` outside the login portal. This is a
standalone document with inline CSS and JavaScript. Since U5, Caprasimo and
variable Figtree are embedded as Latin WOFF2 data URIs: no app module, font,
image or other resource is requested over the network. Its own small palette is the
exception to the app theme-token rule because the claim CSP permits no external
assets; `font-src data:` permits only embedded fonts. The host supplies
`frame-ancestors 'none'`; the document repeats the
remaining policy in a meta tag. It consumes the fragment token immediately,
removes the fragment before a request, and sends the token, username and password
only to same-origin `/claim`. Refusal sentences are shown verbatim; 410 offers
Open Loam at this origin's HTTPS root. Success clears the password, waits the
host's `wait_seconds`, and opens the returned portal URL. The app server does
not implement or proxy account creation.

The runtime marks the starter summary `hello: true`. Before showing its ordinary
workspace, the page reads that thread and the provider catalog. A complete
record containing only presets is the fresh-install signal; no browser setup
flag is stored. This routine interpretation fills the contract's lack of an
explicit onboarding-complete field: after the person's first reply, a reload
uses ordinary Chats. An installation without a hello thread retains its current
navigation, even when all providers are signed out. An unreadable starter falls
back to ordinary Chats rather than blocking access to existing conversations.

Fresh installs with no connected Claude Code, Codex or Antigravity account see
the welcome screen. It explains that each is the person's own account and uses
the shared descriptor-driven provider sign-in cards, including device codes,
pasted codes, cancellation and polling. Continue enables when one is connected
and opens the runtime's hello thread. If a provider was connected already, the
untouched hello opens directly. Presets show agent identity, prose and ordinary
message actions, with no provider/model chip, turn facts or lazy steps request.
The composer's model choice describes the next reply, as in any thread.

Settings → Sign-in opens `https://auth.<current-domain>/settings` in another tab.
While that flow is open, the page reads `/_loam/login-code` directly through the
portal every three seconds, with the same-origin cookie and `cache: no-store`.
The web relay and broker never receive that code. The UI displays its age and
Copy code, rejects expired readings, clears codes on error, and cancels the poll
on Done, navigation, dismissal or expired app login. A request that settles after
dismissal cannot restore its code. The host remains responsible for owner-only
access and the 15-minute lifetime.

Settings → Update reads the running version and checks when Settings opens or
when the person chooses Check for updates. No periodic check or badge exists.
The five allowlisted `/api/update` routes relay the runtime's status and JSON,
including structured refusals, with no-store responses and server-side bearer
authentication. Before numbered releases, `no-version` reads as a calm install
state. U5 labels a `current.kind: development` install as Development build,
shows the host's local commit count when known, and explains a null version
without drawing an error card. An older runtime's 404/405 says the update
service is not ready.

The supplied contract has file changes, not editorial release notes; What's new
therefore lists its added, changed and removed files without inventing a release
summary. Local library changes display the runtime diff. Back up and update is a
separate explicit action before `accept_library_edits: true`; Cancel makes no
request. A late `library-edits` refusal enters the same review. Dirty/conflict
sentences and affected files remain visible with an invitation to ask the agent.
Rollback has its own confirmation. Progress polls only a running/starting run,
about every two seconds, preserves host step words and details, tolerates restart
failures, and reloads after `applied`. Failed, refused, interrupted, applied and
rolled-back results have distinct treatments; rollback availability belongs to
the runtime.

Both profile New thread and Settings New agent now put the plus before the
label. No capability was retired, no dependency was added to the app, and this
page increment does not cut a numbered release or request a deployment.

Run `npm test`. The real-layout browser walk uses isolated loopback fixtures:

```sh
LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs U4_EVIDENCE=/path/to/captures node test/release-u4-browser.mjs
LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs U4_EVIDENCE=/path/to/captures node test/release-u4-entry-browser.mjs
```

The [evidence](evidence/first-release-u4/README.md) records commands, results,
390/1440 light/dark captures and the limits of the fixture proof. The live broker,
claim service and Authelia were not supplied to the job; their real sign-ins and
update route readiness remain staging checks. No live restart was attempted.
