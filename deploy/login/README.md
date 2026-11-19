# deploy/login/ — Loam's sign-in page

The page a person meets at the login portal (`auth.<domain>`): signing in
with a password or a passkey, the second factor a host may ask for, "you're
signed in", signing out, and the sign-in settings (passkeys and the
password). It is Loam's own page in the Latte look the claim page has, in
front of [Authelia](https://www.authelia.com/), which does every part of the
login that matters.

Made 2026-11-16, when the first-day walk found the portal was stock Authelia
(blue Material UI, "Powered by Authelia", a language menu) right after the
claim page: the one visual break of a new person's first five minutes.
Authelia's own theming reaches a logo, a favicon and its words
(`server.asset_path`, read in its 4.39 source), not its colours, type or
layout, so it could not be made to look like Loam.

## What it is, and what it is not

- **Static files.** `index.html`, `login.css`, `login.js` and `icon.svg`.
  `bin/gen-deploy` renders them into `/etc/loam/login/`
  (`lib/loam_deploy.py`, `login_files`): it fills the page's few marks (the
  home address, whether a password reset is offered, the files' version) and
  embeds Loam's two faces from `app/public/fonts/` in the stylesheet.
  `bin/check-drift` diffs the four rendered files like every other.
- **Served by Caddy on the portal host** (`caddy_portal_block`): the page for
  the portal's own routes (`/`, `/2fa/*`, `/authenticated`, `/logout`,
  `/settings`, `/settings/*`), its files under `/_loam/login/`, and
  everything else, the API above all, passed to Authelia as before.
- **Every request it makes is Authelia's API, on the same origin** (the
  endpoints Authelia's own web client calls, with the same bodies). It
  decides nothing: Authelia checks each factor, keeps the session (the same
  `loam_auth` cookie, the same Redis), validates every redirect
  (`targetURL`, `/api/checks/safe-redirection`) and still gates every host.
  The access rules, factors, session lengths and passkeys are unchanged; a
  passkey made on Authelia's page works on this one, because the origin,
  and so the WebAuthn relying party, is the same.
- **No inline script or style.** Caddy sends the page with its own policy
  (`LOGIN_CSP`: script and style from the portal only, fonts embedded,
  requests to the portal only, framed by nobody).

## Which page answers: `login.page`

In `/etc/loam/loam.toml`:

```toml
[login]
page = "loam"        # the default: Loam's page
# page = "authelia"  # Authelia's own page, as before 2026-11-16 (the rollback)
# page = "staged"    # Authelia's, except to a sign-in for the staging host
# staged_host = "next"  # or to a request carrying page=loam
```

Change it, then `sudo bin/gen-deploy`. Only the Caddyfile changes; the
rendered files stay, so switching either way takes effect at Caddy's reload
and signs nobody out.

Whatever the setting, `?authelia=1` on a portal route reaches Authelia's own
page. That is where what this page leaves out still is: setting up or
removing an authenticator app (TOTP), renaming a passkey, choosing a
preferred second factor. The settings page links to it ("More options").

## The states

| Route | What a person sees |
|---|---|
| `/?rd=…` | Sign in: username, password, "keep me signed in for a month", or a passkey. After a passkey sign-in on this device, the passkey comes first ("Welcome back"). |
| `/?rd=…` with a session one factor short | One more step: the password after a passkey, or a passkey or authenticator code after a password. |
| `/` | You're signed in: Open Loam, Sign-in settings, Sign out. |
| `/logout` | You're signed out. |
| `/settings` | Passkeys (list, add, remove) and the password. Adding or removing asks Authelia for an elevated session first: a second factor if the person has one, else a one-time code. This box sends no mail, so the code lands in Authelia's file notifier, and the app's Settings › Account shows it (`/_loam/login-code`, `bin/loam-claim`). |

Its words follow the app's: sentence case, plain, nothing about email.
Light and dark follow the device.

## Changing it

Edit the files here, then run `python3 tests/test_deploy.py` (the render
and the portal block in all three modes; Caddy adapts each when it is on
the box) and walk it in a real browser against a real Authelia: a fresh
install on a throwaway VM, as `engineer/memory/MEMORY.md` describes, with
a virtual WebAuthn authenticator for the passkeys. On a live box, prove it
with `login.page = "staged"` first.
