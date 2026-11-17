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
