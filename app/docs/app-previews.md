# App page previews

Apps are websites. The launcher uses a picture of the website, with a small
thumbnail in the desktop list and a rounded, two-column card on phones. Name
and author sit below the picture; the face appears once in that author line.
The phone details button stays inside the card, with a 44px target. Holding
the card for 550ms also opens details; moving a finger to scroll cancels the
hold. Browser-only apps have one quiet browser mark. Desktop main/side marks,
hover/focus side actions and the shared panel/item navigation are unchanged.

A capture failure uses a smaller distinguishing letter on a softly tinted field.
A verified site icon is centred there when no page picture is available; it
becomes a small supplemental mark once a page preview is present.
Related names use their suffix, and later shared prefixes select a different
letter: the fixture family’s bench/demo/design variants read B/D/S. Colours
tint the theme’s paper with persona accents; letters stay in the reading ink
family, independently of face-style preferences. Allocation is deterministic for a catalog and resolves
palette collisions; adding/removing a conflicting app can reassign a slot.
These are content images, with no change to theme/action tier tokens.

## Where capture happens

`src/app-capture.js` starts the configured Chromium executable **beside the
server, as the server's own user**. On a Loam install that is the app's
systemd unit (`loam-app@<app>.service`, since 2026-11-16; until then a Docker
image with Alpine's package): the host has Debian's `chromium-headless-shell`,
`fonts-noto-core` and the speech encoder `ffmpeg`, and `APP_PREVIEW_CHROME`
names the headless shell. Any run can set `APP_PREVIEW_CHROME` to an installed executable; absent Chromium or unwritable
storage produces the designed fallback. Capture failures also produce a
structured `app-preview` warning.

Each capture has an empty temporary profile under the preview cache. It
receives a minimal environment with process/library/font paths, never the
broker token, provider keys or a host/browser profile. No browser login is
copied. By default the site is reached at its catalog URL through the box’s
network. The optional host-owned route below reaches private app pages without
a browser login. Local plugin pages always use the web server’s own loopback
origin; if that page itself requires a login, capture falls back.

Chrome DevTools Protocol uses inherited pipes: no debugging listener, new HTTP
port, bind mount or host permission is needed. Chromium runs with
`--no-sandbox`; the unit's sandbox is its isolation: a dynamic uid of its
own, a read-only system with `/home` hidden, its own `/tmp` and `/dev`, no
capabilities and no new privileges. All profile/cache writes stay in the
unit's state directory. With `APP_PREVIEW_OOM_SCORE_ADJ` set (a Loam install
sets the agents' 800) it starts through `choom`, so the browser and every
process it starts are what the kernel ends first if memory runs out. The process group and temporary profile are
removed on completion, failure or the 18-second budget expiring.

One browser runs at a time. It photographs the page’s top at a 1280 × 800 CSS
viewport into an 800 × 500 JPEG. Reduced motion and a light media preference
make cached pictures consistent. After navigation it waits at most four seconds
for the load event, then at most three seconds for fonts/initial painting. These
deadlines run in Node, so an unfinished resource or a stalled page animation
callback cannot consume the full budget. It takes the current picture even if
those waits expire; the overall 18-second browser budget still applies to
startup, navigation, screenshot and pixel inspection. It rejects HTTP
errors/cross-origin document redirects, and checks the
actual screenshot pixels in a clean target. A nearly uniform image (including
an empty canvas after a graphics failure), or an image with more than 98% of its
sampled pixels near black, selects the fallback. A tiny loading label therefore
does not turn a black WebGL canvas into a preview. WebGL draws through
SwiftShader, the software renderer Chromium ships (`--use-angle=swiftshader
--enable-unsafe-swiftshader`): a headless host has no GPU, and Debian's
build no longer falls back to it by itself, so without the two flags a WebGL
page stalled the compositor and its capture timed out (2026-11-16). Page
subresources can load normally. No interaction is performed inside the photographed page.

Each successfully stored capture emits an info-level structured log:
`{"event":"app-preview","app":"<catalog name>","state":"ok","ms":1234}`.
`ms` measures the capture operation, excluding time queued behind another app.
Serving an existing cached picture does not emit a capture-success line.
Failure warnings retain their `fallback` or `kept` state.

## Optional internal route

The host can supply two environment variables for a preview service available
only to the app: on a Loam install, Caddy on the broker's address, walled to the
box itself and the broker's bridge. Neither has an installation-specific default.

| Variable | Meaning |
| --- | --- |
| `APPS_PREVIEW_BASE` | Plain HTTP origin, e.g. `http://preview.example.test:<port>`, without a path, query, fragment or credential. |
| `APPS_PREVIEW_ADDR` | Optional IP address without a port. Required where the route’s virtual hostnames do not resolve in DNS. With no base configured it is ignored. |

For a `site` or `project`, the renderer takes the **first label of the public
URL’s hostname** and prepends it to the configured preview hostname. The port
comes from the preview base; path, query and fragment stay with the app. For
example, `https://garden.owner.example.org/notes?view=grid#today` becomes
`http://garden.preview.example.test:<port>/notes?view=grid#today`. The public
catalog URL is never mutated. Browser clicks, copy links, toolbar addresses,
panel identities and media cache keys continue to use it. HTTPS app URLs under
`APPS_DOMAIN` are eligible whether `frame` is true or false; framing controls
where a person opens the app, not whether its page can be photographed. Running
plugin pages remain eligible on the web server's own host.

Chromium receives `--host-resolver-rules=MAP *.<preview-host> <address>` for
these private names. The plain HTTP icon reader connects directly to the
address/port and supplies `Host: <label>.<preview-host>`. It does not need DNS
for those names and does not write `/etc/hosts`. If the address is unset,
ordinary DNS/fetch behavior applies. No credentials or cookies are forwarded.

Relative redirects, including 307s, stay on the internal origin. Root-relative
page assets use that same virtual host in Chromium. Icon/manifest URLs and
redirects that explicitly name the app’s own public origin are rewritten to
the internal origin before fetching; unrelated CDNs keep their own URLs.
Compressed HTTP responses are decoded under the existing byte/time budgets.
Unknown-label 404, outside-network 403 and expired-app 410 answers settle to
fallbacks, without sending an erroring image request to the person’s browser.

Plugin rows are **never mapped onto the preview route**. Their `/x/<id>/`
pages and supplied icons stay on the web server’s own host. No deployment
network, permission or credential workaround is introduced. Other installs
with no preview base retain the previous capture/icon behavior.

The configured base and address enter the site/project cache fingerprint.
Enabling the route, or changing either value, refreshes an old public-login
failure immediately on the next media read; it does not wait for the previous
failure’s retry interval. The last good picture can remain visible meanwhile.

## Browser package

A Loam install selects `chromium-headless-shell` through `APP_PREVIEW_CHROME`
(Debian 12's package, 156.0.8078.79 at 2026-11-16, the same Chromium as the
Alpine package the Docker image used). Other runs default to `chromium`
unless they set that variable.
The same CDP pipe, screenshots, font wait, host resolver mapping and empty-canvas
check run against headless-shell in the native integration tests.

At the 2026-11-15 check, Alpine v3.24’s `156.0.8078.79-r0` listings give
headless-shell at **198.5 MiB installed**, versus
**315.5 MiB** for the full Chromium package, before shared dependencies. This
supported the package choice when the app was an Alpine image.
[Headless-shell package](https://pkgs.alpinelinux.org/package/v3.24/community/x86_64/chromium-headless-shell),
[full Chromium package](https://pkgs.alpinelinux.org/package/v3.24/community/x86_64/chromium).

## Icon resolution without probing

`src/app-icons.js` reads only the page’s declared apple-touch icons, manifest
icons and `rel=icon` links (or a plugin’s supplied icon). It follows same-origin
HTML/manifest redirects, resolves relative paths and tries declared candidates
in preference order. CDN image bytes may be fetched server-side. Neither
cookies nor broker credentials accompany those requests. There are **no
conventional `/apple-touch-icon.png`, `/favicon.svg` or `/favicon.ico` guesses**.

Four icon readers can run concurrently. HTML is capped at 128 KiB, manifests
at 64 KiB, and an image at 512 KiB, under a seven-second per-reader budget.
Image signatures are checked: a login page answering 200 cannot become an
icon. Verified bytes are cached and served from this app’s origin. SVG responses
carry a sandboxed document policy so directly opening one cannot execute code.
The browser receives no remote image candidate list and performs no failover
requests to the sites. A failed local image decode simply keeps the tile.

## Cache and refresh

`APP_MEDIA_DIR` defaults to `/storage/app-media`. Cache entries use a SHA-256
key of the catalog URL, contain the JPEG/icon and a regenerable JSON sidecar,
and are written atomically. They store no catalog or user record. Removing
this cache loses no app/session state. The cache is bounded to 128 entries and
sweeps entries not refreshed for seven days. Temporary profiles have no durable
role. Images are limited to 2 MiB per JPEG.

The successful refresh interval is `APP_PREVIEW_TTL_HOURS` (six hours by
default). A changed catalog version/modified/updated/created/icon/frame/state
fingerprint starts a refresh on the next read as well. The capture revision also
enters the fingerprint: deploying the browser-only/partial-render change refreshes
old skipped or timed-out entries immediately, without deleting the cache.
Concurrent requests
coalesce. The old good image remains available during refresh and after a
failed refresh; first-time failures use the designed tile and retry after at
most 30 minutes. Icons and images survive a server restart. Cache read/write
or browser failures do not hold up the catalog or opening an app.

The page reads one batch, briefly checks pending jobs, then schedules its next
check from the server’s `refresh_after_ms`. An open Apps page therefore refreshes
on the same slow schedule. Capture jobs continue independently of the requesting
browser; a later client gets the cache. Pending requests return promptly rather
than holding an HTTP request through the browser queue.

## Web-only routes

All routes use the existing authenticated web origin and re-read the current
broker catalog. The browser never supplies an arbitrary capture target.

| Route | Answer |
| --- | --- |
| `GET /api/apps/media` | `{apps: [{url, pending, refresh_after_ms, preview, icon}]}`. The two image fields are same-origin URLs or `null`; failed captures settle with `pending:false`. |
| `GET /api/apps/media/image?url=…&kind=preview\|icon&v=…` | Cached verified bytes, private HTTP caching. A removed catalog URL is refused. A missing cache file gives an uncached harmless image with 200, avoiding a broken-image probe while refresh catches up. |
| `GET /api/apps/icon?url=…` | Compatibility for previously open tabs: `{icons:[<resolved same-origin URL>]}` or an empty list, with no remote candidates. |

The `v` value is the image digest. Media is private; the routes do not expose a
public screenshot service. No broker route, coding CLI login, session write
guard, plugin registration surface or panel ownership rule changes.

[Verification and captures](evidence/apps-previews/README.md) include a real
Chromium capture of local fixture pages, failure cases, server-only icon reads,
zero-console-error checks and the inherited panel regression. The
[internal-route follow-up](evidence/apps-internal-route/README.md) also exercises
virtual Host/IP routing, redirects, cache invalidation and quieter fallback
geometry. Real-site access remains a staging check when a
job cannot reach the host’s internal route.

[Browser-only and unfinished-page checks](evidence/apps-all-previews/README.md)
exercise bounded partial rendering, blank WebGL fallback, success logs and
browser-only pages with their real icon retained on a preview.
