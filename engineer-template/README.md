# The project-engineer template

What `bin/provision --project <name>` renders into a project's checkout to
give it an engineer. A project engineer is self-contained (phase-5 closing
plan, decision 21): its whole context is its checkout, with nothing from
this box present. So everything here is written for a reader who has never
heard of this runtime, and the rendered output must not name it.

**The harness is in the checkout and never in the repository** (since
2026-10-01). `CLAUDE.md`, `role.md`, `project.md`, `memory/` and `.claude/`
are listed in the checkout's `.git/info/exclude` before they are written,
and a new repository's first commit is a README stub and nothing else, so a
repo that goes public carries the project and nothing about how it is
worked on. The memory is therefore on this box's disk only. loam-ui and
one other project were provisioned before this and still commit their
harness; `harness_tracked()` tells the two arrangements apart, the role
render keeps the "committed with the code" sentence only for them, and
`--rerender` refuses them, because a render there is a commit in a
repository that is its engineer's. loam-dictate went public-ready by being
recreated with a clean history.

Two kinds of file, one owner each:

- **`repo/`** is copied verbatim into the checkout's root. `CLAUDE.md`
  (two imports), `project.md` (the skeleton the platform engineer fills from
  the brief) and `memory/MEMORY.md` (two lines of operator voice, then the
  engineer's).
- **`.claude/settings.json`** is `provision.project_settings()`: Bash
  allowed, edits inside the repo, no added directories, no MCP servers, no
  hooks. A function rather than a file because `.claude/` directories are
  rendered by provision everywhere on this box and the boundary guard
  refuses a hand-written one, template included.
- **`role.md`** is not here. It is rendered by `provision.render_craft()`
  from the operator-authored role file in the vault -- the same source the
  platform engineer's own `role.md` is copied from -- with the lines that
  only mean something inside this runtime removed and its names made
  neutral. The rendering is mechanical, marked with the source revision, and
  refuses to produce output that still names this runtime, so a change to
  the source flows to every project on the next render or fails loudly.

`bin/check-drift` compares each registered project's checkout against both,
and for an excluded harness also checks the five lines are in
`.git/info/exclude`. Edit the template or the role, then
`bin/provision --project <name> --rerender` (CLAUDE.md, role.md and
settings; never project.md or the memory); never edit a rendered copy.

For an existing private repository, connect it with
`bin/provision --project <name> --repo <url>`. An explicit
`--vault-folder <Folder>` routes receipts to that folder in the vault's
projects area. Add `--vault-write` to let jobs write deliverables in the
existing folder. The job's repository can still be read-only for landing.
