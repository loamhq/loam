# Live HTML: host handoff

The app implements live HTML, but it needs a **separate content hostname**.
This document is the host change to review and apply. No DNS, Caddy, portal,
network, or deployment changes were made by the app job.

## Configuration

Set `FILES_CONTENT_ORIGIN` to the dedicated HTTPS origin, with no path, query,
credentials, or fragment. It must have a different hostname from the app;
changing only a port does not separate cookies. HTTP loopback origins are
accepted for local browser checks. This is a second origin on the app's
existing HTTP server and port, not another process or another mount.

Keep `FILES_VAULT_URLS` pointed at the existing published vault. Its **first**
prefix is used for Copy link. See [standalone copy links](#standalone-copy-links)
for additional published folders and app-only fallback labels.

The existing vault host was not assumed to serve workspace files or to have
the necessary security headers. The dedicated origin serves HTML and relative
assets from **all** roots through the broker's existing read-only download
route. Files are not copied onto the container or another host.

## Proxy change for the host engineer

Choose the content hostname, provision TLS at the existing private ingress,
and proxy **only** `/content/*` to the same app upstream. Preserve the incoming
Host including its port: the app dispatches by Host before considering an API
or app route. Do not rewrite the hostname to the app's hostname and do not
trust a client-provided `X-Forwarded-Host` as its replacement.

This Caddy site is a template, with environment placeholders for the chosen
hostname and existing upstream. Adapt it to the host's existing private ingress
policy; it is not a request for a public, anonymous file server:

```caddyfile
{$FILES_CONTENT_HOST} {
    @content path /content/*
    handle @content {
        reverse_proxy {$LOAM_UPSTREAM} {
            header_up Host {http.request.hostport}
            header_up -Cookie
            header_up -Authorization
        }
    }
    handle {
        respond "not found" 404
    }
    header -Set-Cookie
}
```

`FILES_CONTENT_HOST` and `LOAM_UPSTREAM` above are proxy-template inputs, not
new app environment variables. Do not attach the app's CSP, its cookies,
its CORS settings, or its session relay to this hostname. The content response
already carries its own sandbox and CSP. Keep access logs off for this site,
or redact the segment immediately after `/content/`, which is a bearer grant.
The app redacts that segment in its own logs.

The login portal's app cookies must be **host-only** (no parent-domain
`Domain` attribute), Secure and HttpOnly. A separate registrable domain for
content also avoids parent-domain cookie sharing. Do not forward app cookies
to the content upstream or set cookies on the content origin. A sibling
hostname alone does not protect a broad parent-domain cookie. The browser
check proves host-only app cookies stay off the content requests; the actual
portal configuration still needs the host engineer's verification.

The file gateway itself requires an app-issued, HMAC-signed URL. A URL grants
read-only access to one broker Files root for one hour, and an app restart
revokes every outstanding URL. The browser gets no broker token. This permits
relative scripts, stylesheets, images, ES modules and fetches for sibling data
without using the app's login cookie. Never strip the signature or add a
directory listing or an unsigned file route to the proxy. Do not log or share
these preview URLs. Copy link uses the ordinary vault URL or app deep link,
never this temporary grant. Reload issues a fresh URL.

After deployment, check the private app's `/up`, then open an HTML file and
confirm that the iframe URL has the content hostname. Check on that hostname
that `/api/agents`, `/api/sessions`, `/app.js`, `/files`, and unsigned
`/content/anything` cannot reach the app or broker. They should all be 404;
POST to a signed content URL is 405. On the app hostname a signed `/content/`
path must be 404. Inspect the returned CSP and reproduce the browser check
below. Do not claim activation from configuration alone.

## Isolation and remaining power

The iframe and the HTTP response use:

```
sandbox allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads
```

`allow-same-origin` is deliberate **on the different content hostname only**.
It preserves normal relative fetches, modules and dialogs. It is never added
to the legacy app-origin preview. The client rejects a live URL on the app's
origin, and the app refuses to issue one on its own hostname. The server never
serves live file bytes on the app authority, including a direct navigation.
The content origin has no app shell, static app modules, session routes,
file-write routes, CORS grants, or broker credentials.

The app also rejects browser API requests carrying a cross-origin
`Sec-Fetch-Site` or `Origin` (including `null`) before relaying anything. This
blocks causing a request through a form, image, navigation or simple fetch,
as well as reading its response. Command-line checks without browser headers
continue working behind the existing access boundary.

The content CSP permits inline JavaScript, HTTPS script/style/font/image
resources and local assets. **`connect-src` is limited to the content origin**:
fetch/XHR/WebSocket/beacon calls to the app or an external collector fail.
External embedded frames, plugins and workers are disabled. Top-level app
navigation and popups escaping the sandbox are not allowed; ordinary
`target="_blank"` links work and inherit the sandbox. The app's own Open in
new tab has `noopener noreferrer`; the new tab receives the same HTTP sandbox.

This is isolation from agent controls, **not an untrusted-data containment
system**. A malicious HTML file can read any broker-readable file in its
granted root, including other vault notes, while its URL is valid. It can
alter its own content, use the content origin's own storage, show dialogs,
open popups and attempt social engineering. It can send data out through
allowed HTTPS script/image URLs, form submissions or outgoing links, even
though `connect-src` blocks fetch. CDN code has that same power. These are
explicit remaining capabilities of supporting ordinary interactive pages;
the app does not claim CSP prevents all exfiltration. Grants do not authorize
another root or any writes, and the app's cookies, storage and relay remain
separate. Open only pages whose behavior you accept.

Without the host configuration, HTML keeps the existing script-free static
preview, fills the pane, and plainly says the live origin is not configured.
Source, editing and Download remain available. No existing preview capability
is removed to force a host change.

## Large HTML documents

Opening HTML in Preview does not read its source into the editor. The isolated
live frame streams the broker's offered download directly, including embedded
images and scripts. Source reads only when selected; the broker's text-read
limit and the app's save-body limit still apply. A source refusal keeps Preview
available, explains the size/type limitation in readable language, and offers
Back to preview and Download. No empty source can be saved after a failed read.
Cold panel addresses obtain their action capabilities from stat, without
fetching source; missing or denied metadata still blocks opening.

The static fallback still asks the broker to read the HTML. If that read is
refused specifically for size (413), it uses the offered download instead,
buffering at most 16 MiB before applying the existing script-free sandbox.
Both Content-Length and actual streamed bytes are checked; an over-limit stream
is destroyed. Missing or denied downloads retain their HTTP status and show a
plain explanation. The existing 32-asset, 2 MiB per-image, 8 MiB total-image,
and 256 KiB per-stylesheet guards remain in place. No text, upload or save limit
is raised. The live content origin and its permissions are unchanged.

[Large HTML evidence](evidence/large-html/README.md) records a synthetic
3,232,179-byte prototype with four embedded PNGs at phone and desktop sizes,
including the desktop side pane. It distinguishes that reproducible local
check from verification of a private original on staging.

## Reproduce the evidence

The normal suite includes `test/files-content.test.js`: authority separation,
grant forgery/root changes/expiry/restart, traversal, non-GET refusal,
cross-origin relay reads and writes, configuration, and Copy link routing.

The optional Chromium check uses a recorded broker and two loopback hosts.
Install Playwright and Chromium in job scratch (no production dependency),
then run:

```sh
FILES_HTML_EVIDENCE="$TMPDIR/files-html-shortcuts-links" \
LOAM_PLAYWRIGHT="<installed-playwright-module>" \
LOAM_CHROME="<chromium-executable>" \
node test/files-html-browser.mjs
```

It exercises actual inline and sibling JS/CSS/JSON, the Chart.js 4.4.9 CDN
library, native dialogs, Reload, target-blank links, Open in new tab,
file/folder shortcuts, a missing shortcut, and the real clipboard at 390 and
1440 in both themes. It attempts app DOM/storage/fetch access, a relay fetch,
external fetch, and a popup navigation into the relay, and asserts zero broker
mutations and no app-cookie leakage. It also demonstrates the residual read
of another vault note. No production vault or agent session is contacted.

Retained captures and results live in
[`evidence/files-html-shortcuts-links/`](evidence/files-html-shortcuts-links/README.md).
Browser sandbox behavior is based on the
[HTML iframe sandbox reference](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/iframe#sandbox)
and the API gate follows
[Fetch Metadata resource isolation](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Fetch_metadata).

## Standalone copy links

Copy link returns the published file's own address. The first
`FILES_VAULT_URLS` base serves the whole vault with the same folder layout.
Each path segment is URL-encoded once, preserving nesting, spaces, Unicode,
literal percent signs, query marks and hashes in filenames. Opening the link
uses the published host's ordinary login and file serving; relative scripts,
styles and fetches resolve beside that file, outside the app shell.

The app does **not** probe that address with an anonymous HEAD request.
Private file hosts redirect anonymous requests to login, which previously
made valid links silently become app links. Deployment configuration states
which trees are served. Copying a link publishes nothing and cannot bypass
the file host's access controls.

Files currently offers two roots: Vault and AI Spaces. Commons is a folder
inside AI Spaces, not a third root. Only its published web subtree has a
standalone host; the rest of AI Spaces has no stated standalone address.
Configure any published subtree using `FILES_WEB_URLS`:

```sh
FILES_WEB_URLS='[{"root":"spaces","path":"shared/www","url":"https://files.example.org"}]'
```

This is an example: use the exact Files-relative folder supplied by the host,
never a host filesystem path. A request for
`spaces:shared/www/Some folder/page.html` then copies
`https://files.example.org/Some%20folder/page.html`. The folder prefix is
removed; similarly named siblings and other agents' `www` folders do not
match. The longest matching folder wins; explicit mappings precede the vault
default. An empty `path` maps a whole root. Invalid mappings fail startup.
Only HTTPS bases without credentials, query or fragment are accepted.

For unserved locations, the same menu row and toolbar icon say **Copy app
link**. They still open that file in the authenticated app. No extra menu
choice or temporary preview URL is added. Without a configured vault base,
vault files also use this honest fallback. A link API failure uses the same
fallback and label.

The [copy-link evidence](evidence/file-links/README.md) records the regression,
real clipboard/new-tab checks at 390/1440, and production-access limitations.
