# Security

Loam runs on a server you own and holds the keys to your AI accounts, your
notes and your agents' shell. A flaw in it matters, and we want to hear
about one before anyone else does.

## Reporting a vulnerability

Report it privately through GitHub: on this repository's **Security** tab,
choose **Report a vulnerability**. Please do not open a public issue, pull
request or discussion about it.

Say what an attacker could do, what they would need (a share link, a
signed-in session, a shell on the server, nothing at all), and how to
reproduce it. Leave out anything from a real install: names, addresses,
keys and captured sessions. A synthetic example is enough.

We answer within a week, say whether we can reproduce it, and tell you when
a fix is released. We credit you in the release notes if you wish.

## What is supported

Only the newest release receives fixes. `loam update` moves an install to
it (`docs/update.md`).

## Where the walls are

What the edge lets through (the sign-in, share links, the agents' browser
and its wall) is described in `deploy/README.md`; what each agent may reach
on the server is in `docs/config.md` and `docs/plugins.md`. A report that a
wall described there does not hold is exactly the kind we want.

A plugin's server is protected from other pages by one rule, which the
plugin gateway applies to every plugin so its author need not write it: a
request that could change something (any method but GET, HEAD and OPTIONS,
or a WebSocket) is refused when the browser says another origin's page sent
it, and the agents' browser, which passes the sign-in, may change nothing
from another origin on any host. It does not protect a plugin whose GET
changes something, a plugin's own page code (that runs in the app's origin
with the app's reach), or a caller that is not a browser and has its own way
past the sign-in.
