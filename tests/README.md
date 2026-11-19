# Loam's tests

`python3 tests/run-all` runs every `tests/test_*.py`, each as the script it
is, several at a time, and exits non-zero if any fails. It is the command a
landing runs on the candidate commit (`lib/loam_worktree.py`),
and the one to run before you ask for review (`CONTRIBUTING.md`).

```
python3 tests/run-all                      # all of them, half the CPUs at once (2 to 8)
python3 tests/run-all --jobs 1             # one at a time
python3 tests/run-all test_x.py test_y.py  # only those, with their whole output
python3 tests/run-all --live               # the live set (below), by hand
```

## Sealed

A test sees nothing of the install it runs on. `run-all` gives each file a
scratch folder of its own, removed when it ends, holding:

- a configuration of the suite's own (`FIXTURE` in `run-all`), never
  `/etc/loam/loam.toml`. Every data path in it (vault, workspaces, projects,
  plugins, state, logs, jobs, the secrets files) points into the scratch
  folder. The workspaces hold what a fresh install has: the starter agent
  `iris` (its `agent.json` and a soul), `_shared/reach.md` and `commons/`.
- its own `TMPDIR`, lock table, schedule log, host settings, thread store,
  vault and waiter records, so two tests running at once never share a
  temp path or state, and nothing a test leaves behind outlives it.
- no network beyond loopback, and no credential read from a file
  (`lib/loam_offline.py`).

A landing adds a wall (`platform_argv` in `lib/loam_worktree.py`): the run
gets a network namespace of its own with only its own loopback up, and
each of the box's live folders (`sealed_paths`: the vault, the workspaces,
the projects, the plugins, the state folder with every other job's tree,
the secrets folder and the user manager's runtime folder) is covered by an
empty read-only one. A test that reads any of them finds nothing, every
time. `tests/test_sealed.py` proves both halves by causing them.

So a test makes what it needs. One that needs the live box, the live app,
the vault, another repository or the network is not a landing test.

## The live set

`tests/live_*.py` read this box as it is. `run-all --live` runs them one at
a time, unsealed, on the box it is run on. No landing runs them, so they
cannot hold one. Run them by hand after a change to what they cover.

| test | what it asks of the box | split out of |
|---|---|---|
| `live_user_bus.py` | the launcher's user-bus default reaches the real user manager | `test_engineer_bus.py`, 2026-11-19 |

`e2e_files_app.py` (a broker and the app, end to end) is run by hand the
same way. Its docstring says how.

## Writing one

- Name it `test_*.py`. It prints `PASS`/`FAIL` lines and exits non-zero on a
  failure. A test of an MCP server says `Run: mcp/.venv/bin/python` in its
  docstring, and `run-all` runs it with that interpreter.
- Use `tempfile` for every path you write. `TMPDIR` is already yours.
- A port comes from binding port 0, never a fixed number.
- Read the code under test from this checkout (`Path(__file__).parent.parent`),
  never from an installed copy.
