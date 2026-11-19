#!/usr/bin/env python3
"""The engineer, as one tool. A brief goes in, a receipt comes back.

Move 9, decisions 1-7 of the phase-5 closing plan. This is the whole of the
persona-facing surface: one server, one tool that does the work, one
blocking call -- and, since 2026-10-13, one that only looks (`status`).

**Why one blocking call and not start-plus-poll.** Both branches of that
either/or were probed on 2026-09-05 and the start-plus-poll branch died. A
single stdio MCP tool call waits 65 minutes and there is no practical wall
behind it once the server's `.mcp.json` entry carries a `timeout` -- which
`bin/provision` renders for every persona. What kills the polling design is
not that it fails but that it is unnecessary: the receipt arrives on its own.
A background subagent ran fifteen model turns over fifteen minutes with NO
client attached anywhere, and its completion woke the parent session
unprompted, sixty-eight seconds later, with a `<task-notification>` nobody
sent. There is no "are you done yet" to build.

**So the loop is the persona's, and the tool's description is where that is
taught** -- see the description below. For anything past a couple of minutes
the persona calls this from a same-identity background subagent and gets the
receipt as a task notification while the conversation carries on. That is
decision 6 exactly as written, and it needs no configuration change anywhere.
(`CLAUDE_AUTO_BACKGROUND_TASKS=1` on the Remote Control units would let the
runtime do the same thing with fewer moving parts. It is recorded as the
later simplification and is deliberately not built here.)

**This server decides nothing, and that is a rule rather than a tidiness
(since 2026-09-07).** It is spawned by the persona's `claude` session once
per conversation and lives as long as the conversation does, so whatever
code it holds is frozen at the moment the conversation opened. Caused that
morning: a conversation opened at 06:55 held a fixed map of projects, a
project was provisioned at 08:03, and the first brief to it was refused with
"No engineer for project" for as long as that conversation lived. Reading the
registry per call fixed that instance; the rule that closes the class is
that nothing here may branch on anything. The tool takes its arguments,
hands them as one JSON object to `bin/engineer-job --brief` -- a fresh
process on every call, running today's code -- and returns the one JSON
object that comes back. Which projects exist, whether a field is empty, the
clock's default and ceiling, the job id, the brief's header, the path the
receipt lands at, every clock and every refusal: all the launcher's. A
persona in a conversation opened a week ago gets today's behaviour on its
next call, for everything except this tool's own signature and description.

**A second tool, `status` (since 2026-10-13), by the same rule.** "Where
are we now?" -- what is running on the box, which jobs are landing, who
is queued -- was answered by globbing job folders, which a persona's reads
no longer reach. The tool is `bin/engineer-job --status`, run fresh per
call, and returns its text: one source, so the tool and the operator's
command cannot drift apart. Read-only; it takes no arguments.

The one thing this file does that is not a hand-off is to say so when the
hand-off itself fails: a launcher that printed no JSON is a runtime defect,
and the persona is told that rather than handed an empty string.

**The wait yields (since 2026-09-07, evening).** Seen that afternoon: two
briefs from one conversation, to two projects with separate locks and two
free slots, ran one after the other -- the second job started two seconds
after the first finished, and the persona was told nothing. The handler
below was `async def` and called `subprocess.run`, which never yields, so
the server's event loop was held for the length of the first job and the
second request sat unread in the pipe until it returned. The library is not
at fault and was checked rather than assumed: `mcp` 2.1.0's
`JSONRPCDispatcher.run` spawns every non-`initialize` request as its own
task in one task group (`shared/jsonrpc_dispatcher.py`, `_dispatch` ->
`_spawn`), so it serves calls concurrently exactly to the degree the
handler lets go of the loop. A plain `def` would have been run on a worker
thread by the same library; an `async def` that blocks is the one shape
that serialises everything.

So the launcher is awaited with `anyio.run_process`, which is the library's
own backend (`MCPServer.run` calls `anyio.run`) and is `subprocess.run` in
async clothing: it drains stdout and stderr in tasks of its own, so a large
receipt cannot fill a pipe and stall the child, and it holds no thread for
the hours a job may take. `asyncio.to_thread` around the old call was the
other candidate; it would have worked, but a worker thread pinned for four
hours per job is a resource with a cap, and the thread cannot be cancelled.
This can: if the client cancels the request or the conversation ends, the
task is cancelled, the pipes close, and the launcher's OUTER process is
killed. That is the right casualty -- it is only the waiter. The job is a
transient systemd unit the outer already handed off, it does not notice,
and its receipt lands on disk and in the vault regardless.
"""

from __future__ import annotations

import json
import sys as _sys
from pathlib import Path
from typing import Any

_sys.path.append(str(Path(__file__).resolve().parent.parent / "lib"))
import loam_config as lc  # noqa: E402  constants only; nothing here holds state

import anyio
from mcp.server import MCPServer

# The launcher, by path. Not imported from lib/ -- this file imports nothing
# of this repo's, so there is nothing of this repo's for it to hold stale.
LAUNCHER = str(lc.ROOT / "bin" / "engineer-job")


def _site_domain() -> str:
    """The domain an agent's site is under, for the `site` tool's text."""
    try:
        import loam_deploy  # noqa: PLC0415
        return loam_deploy.domain()
    except Exception:  # noqa: BLE001 -- the text must not stop the server
        return "<domain>"

mcp = MCPServer("loam-engineer")


@mcp.tool(
    name="brief",
    description=(
        "Hand a job to the engineer for a project. A brief goes in, a "
        "receipt comes back — one call, which blocks until the work is "
        "done.\n"
        "\n"
        "**Three things first, because the rest of this may reach you "
        "truncated.**\n"
        "1. `title` — a short noun phrase naming the job. It becomes the "
        f"receipt's filename in the vault, which is how {lc.OWNER_NAME} finds it in a "
        "folder listing.\n"
        "2. For anything past a minute or two, **call this from a background "
        "subagent** — one whose whole task is this single call — and keep "
        "talking. The receipt reaches you as a task notification when the job "
        "finishes, even with nobody attached. Calling it directly takes you "
        "out of the room for the length of the job. If launching the subagent "
        "is refused, say so and call it directly; that refusal is a "
        f"permission question for {lc.OWNER_NAME}, not something to route around.\n"
        f"3. `involvement` is {lc.OWNER_NAME}'s words for this job, quoted. When he "
        "has said nothing in particular, the default is *go and do it, "
        f"{lc.OWNER_NAME} reviews after it is running*. A job that must change "
        "nothing opens it with `Read-only` (`Read-only. Change nothing, "
        "commit nothing, open no PR.`): the launcher then lands nothing, "
        "pushes nothing and opens no pull request, whatever the tree holds. "
        "Saying it only in the brief's body does not do that.\n"
        "\n"
        "**What a brief is.** Prose, not a ticket. Say what must be true when "
        "the job is done and why it matters; say what you already know about "
        "how the thing works today and what you are unsure of. The engineer "
        "reads the code before it changes it and will tell you what it found "
        "that you did not name — so a brief that is honest about its own "
        "uncertainty gets better work than one that guesses at mechanism. "
        "Anything you state as true of the box carries a date; the engineer "
        "checks premises rather than trusting them, and reports the stale "
        "ones back to you. Name the boundaries: what it may change and what "
        "it must leave alone.\n"
        "\n"
        "**`tier` — which of the engineer's three models.** `quick` for "
        "small, mechanical, well-specified work; `standard` (left out) for "
        "most work; `hard` for design-heavy work, subtle bugs and wide "
        "refactors (hard may draw on a smaller quota). Name the tier, never "
        f"a model: {lc.OWNER_NAME} sets each engineer's three in Settings. On "
        "`continue_job` it is left out to keep the job's own model.\n"
        "\n"
        "**`deploy` — land and go live in one job.** For a project whose app "
        f"runs on this box (loam-ui today), set `deploy: true` when {lc.OWNER_NAME}'s "
        "word for the job is to deploy it ('land and deploy', 'go and do it "
        "... after it is live'). After the branch has merged, passed the "
        "tests and been pushed, the launcher deploys that commit with "
        "`bin/deploy <project> up <commit>`: the app's container (or, for "
        "Loam's own app, its unit) only, the previous image or release kept "
        "and put back if the new one is not healthy, "
        "exposure unchanged, never an earlier commit over a later one. The "
        "receipt's launcher half says what ran before and after and its "
        "health; a failed deploy never fails the landing. Set `deploy: false` "
        "for 'do not deploy' or 'staging first' (left out, the engineer's own "
        f"'Deploy when a job lands' setting decides, off unless {lc.OWNER_NAME} turned "
        "it on in Settings > Agents): the job lands only, and staging "
        "or a later deploy is a Loam brief titled `Deploy <project>`, as "
        "before. Writing 'deploy' in `involvement` alone does not deploy.\n"
        "\n"
        f"**The involvement dial, in full.** Tighten it when {lc.OWNER_NAME} has asked "
        "to see something before it lands ('show me the plan before changing "
        "anything'), loosen it when he has said to just get it done. It is "
        "per job, not per project, and it is his to set, not yours. It goes "
        "into the receipt as written.\n"
        "\n"
        "**A job has no clock and no round-trip cap.** It runs until it is "
        "done, whoever briefed it and from whichever lane. `max_minutes` is "
        "an optional ceiling for this one job: leave it out (or send 0) and "
        "there is none. The nightly recycle never cuts a job, nor the call "
        "waiting on one. A call that waits close to four hours ends with "
        "*still running*; the job goes on and its receipt lands in the "
        "vault.\n"
        "\n"
        "**Jobs start by the box's memory, not by a count.** A job starts "
        "when the box has room for it (the session broker's rule: 2 GB or "
        "12% of RAM kept free). With no room it queues and starts by itself; "
        "`status` says what it waits on. Each job is capped at 20% of the "
        "box's RAM.\n"
        "\n"
        "**`continue_job` — when a job was cut off, do not start over.** A "
        "receipt whose outcome says *cut off* (at a ceiling its brief set) "
        "or *killed at its memory ceiling* is unfinished work with its context still on "
        "disk. Send the next brief with `continue_job` set to that job's id "
        "(the `job` row of its launcher record) and the engineer picks up in "
        "the same session: what it read and found is still in front of it, "
        "and your brief is the next thing said to it rather than a fresh "
        "start that re-reads the codebase. It gets a new job id and its "
        "own receipt, and that receipt names "
        "the job it continued. Write the brief as the next word in a "
        "conversation — what to do now, what changed, what you want done "
        "differently — not as a restatement of the first one. Same project "
        "only; it is refused if that job is still running or has no session "
        "to resume, and the sentence says which.\n"
        "\n"
        "**The receipt is the evidence, and it has two halves.** The "
        "engineer's half links every material claim to what proves it — "
        "changed files to a diff or a commit, tests to the command and its "
        "output, 'live' to a URL that answered. The launcher's half is "
        "written by a process that is not the engineer: start and end, exit "
        "status, turns, cost, the last heartbeat, and every tree's git HEAD "
        "before and after. Read them against each other. A claim of "
        "'committed' beside a HEAD that did not move is the thing that split "
        "is there to catch.\n"
        "\n"
        "The result carries `receipt_path` — the vault file at "
        f"`{lc.vault_folder('projects')}/<project>/receipts/`, which is where {lc.OWNER_NAME} reads it. "
        "Pass that path on when you tell him.\n"
        "\n"
        f"**What the engineer will not do.** It never talks to {lc.OWNER_NAME} — you "
        "are its only interlocutor, so anything marked as needing him comes "
        "back to you to ask. It never makes anything public. It never writes "
        "into an agent's own files. If a brief is ambiguous in a way that "
        "would change the work, it returns the brief instead of guessing.\n"
        "\n"
        "Jobs run side by side, for one project or several: each works on a "
        "branch of its own, and when it finishes the launcher merges that "
        "branch into main, runs the project's tests on the result and pushes. "
        "The receipt says whether it landed. One that did not (a conflict "
        "with another job's work, failing tests) keeps its branch and comes "
        "back with the brief that resolves it — send that with "
        "`continue_job`. The engineer is reachable from every lane: a "
        "conversation, a wake, a cron or a one-off, and none of them puts a "
        "clock on the job."
    ),
    # No explicit input_schema: this MCP build derives one from the
    # annotations below, and a default makes a parameter optional the way
    # it reads. `project` is a plain string rather than an enum so a project
    # engineer joins without this signature changing; the launcher answers
    # an unknown name with a sentence naming the ones that exist.
    # `max_minutes` defaults to 0, which the launcher reads as "no ceiling"
    # (since 2026-11-10), so the rule has one home and it is not this file.
    # `continue_job` defaults to "", which the launcher reads as "a fresh
    # session"; every rule about which job may be continued, and every
    # sentence refusing one, is the launcher's. `deploy` (2026-11-12)
    # defaults to None since 2026-11-13: left out, the engineer's own
    # "Deploy when a job lands" setting decides (off unless the owner set it);
    # which app it means, and when it is refused, is the launcher's too
    # (lib/loam_autodeploy.py). `tier` (2026-11-15) defaults to "", which
    # the launcher reads as `standard` on a fresh job and as "the job's own
    # model" on a continuation (lib/loam_engineer.py, "Three tiers").
)
async def brief(
    project: str,
    title: str,
    brief: str,
    involvement: str,
    max_minutes: int = 0,
    continue_job: str = "",
    deploy: bool | None = None,
    tier: str = "",
) -> dict[str, Any]:
    # No timeout here. The launcher owns every clock in this system: it
    # kills the session at a ceiling only when the brief set one, and it
    # stops waiting a grace period after that or at its own ceiling. A second timeout in this
    # process could only fire earlier than the one that knows what is going on,
    # and firing early is exactly how a job gets orphaned with no receipt.
    #
    # Awaited, never blocked on: see the docstring. Every other call this
    # server is handling shares one event loop with this one.
    result = await anyio.run_process(
        [LAUNCHER, "--brief"],
        input=json.dumps({
            "project": project,
            "title": title,
            "brief": brief,
            "involvement": involvement,
            "max_minutes": max_minutes,
            "continue_job": continue_job,
            "deploy": deploy,
            "tier": tier,
        }).encode("utf-8"),
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    try:
        return json.loads(stdout)
    except ValueError:
        return {
            "ok": False,
            "error": (
                f"The launcher returned no verdict (exit {result.returncode}). "
                f"That is a defect in the runtime rather than an outcome of "
                f"your job. stdout: {stdout.strip()[:400]!r}; "
                f"stderr: {stderr.strip()[:400]!r}"
            ),
        }


@mcp.tool(
    name="status",
    description=(
        "Where the engineer is right now, across every project: the box's "
        "memory as admission reads it (available, kept free, room for "
        "another or not), every run going (job id, project, title, who "
        "briefed it, since when, or the wake or cron), which jobs have "
        "finished their session and are landing, and the queue in order "
        "(position, project, title, briefer, when it joined, what it waits "
        "on). There is no count of slots: a job starts when there is room. Read-only, takes no arguments, answers in a second. Use "
        "it to answer \"where are we?\" instead of reading job folders. A "
        "job's own outcome is its receipt, not this."
    ),
)
async def status() -> str:
    # The launcher's own view, rendered by the launcher (`box_status`), so
    # this and `engineer-job --status` are one text.
    result = await anyio.run_process([LAUNCHER, "--status"], check=False)
    stdout = result.stdout.decode("utf-8", errors="replace")
    if result.returncode == 0 and stdout.strip():
        return stdout
    stderr = result.stderr.decode("utf-8", errors="replace")
    return (f"The launcher returned no status (exit {result.returncode}). "
            f"That is a defect in the runtime. stderr: {stderr.strip()[:400]!r}")


@mcp.tool(
    name="cancel",
    description=(
        "Cancel an engineer job by its id, durably, with `by` your name. A "
        "queued job never starts. A running job is stopped: its session ends, "
        "its record says it was cancelled, how long it ran and what it had "
        "committed, its branch is kept and never lands, and nothing restarts "
        "or continues it. A job already landing stops before it moves `main`, "
        "unless its merge has begun, which the answer says. Answers in "
        "seconds (at most about a minute); `status` drops the job at once."
    ),
)
async def cancel(job_id: str, by: str) -> str:
    result = await anyio.run_process(
        [LAUNCHER, "--cancel", job_id, "--by", by or "unknown"], check=False)
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    return stdout or stderr or f"the launcher said nothing (exit {result.returncode})"


SITE = str(lc.ROOT / "bin" / "site")
THREAD = str(lc.ROOT / "bin" / "thread")


async def _hand_off(argv: list[str]) -> str:
    # The same rule as `brief`: a fresh process per call, today's code, and
    # its one JSON object back as it came.
    result = await anyio.run_process(argv, check=False)
    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    if stdout:
        return stdout
    stderr = result.stderr.decode("utf-8", errors="replace")
    return json.dumps({"ok": False, "error": (
        f"{argv[0]} printed nothing (exit {result.returncode}); a runtime "
        f"defect. stderr: {stderr.strip()[-400:]!r}")})


@mcp.tool(
    name="site",
    description=(
        f"Your own subdomain, `<name>.{_site_domain()}`, behind {lc.OWNER_NAME}'s login: a "
        "mockup, slides, a prototype or its backend. Nobody approves it; it is "
        f"private unless {lc.OWNER_NAME} has said yes to more.\n"
        "- action `up` with `name` (lowercase letters, digits, -) and `by` "
        "(your name): a static site served from `root` (default "
        "`ai-labs/<you>/sites/<name>/`, created with a placeholder "
        "index.html; write your files there), or, with `port`, a proxy to "
        "your own container published on 127.0.0.1:<port>. `expires` "
        "(YYYY-MM-DD, the last day) or `duration` (`2d`, `36h`) makes it clean itself up. Live in "
        "under a minute; the shared browser reaches it, nobody else without "
        "the login. Calling `up` again on your site updates it.\n"
        "- action `down` with `name`: the name goes; your files stay.\n"
        f"- action `publish` with `name` and `yes` ({lc.OWNER_NAME}'s own words and "
        "the date, quoted): takes the login off. Only on his direct yes, each "
        "time; the vault is behind the same login. Action `private` puts it "
        "back.\n"
        "- action `list`: every site, its owner, access and expiry."
    ),
)
async def site(action: str = "list", name: str = "", by: str = "", root: str = "",
               port: int = 0, expires: str = "", duration: str = "", yes: str = "") -> str:
    argv = [SITE, action]
    if action != "list":
        argv.append(name)
    if action == "up":
        argv += ["--by", by]
        if root:
            argv += ["--root", root]
        if port:
            argv += ["--port", str(port)]
        if expires:
            argv += ["--expires", expires]
        if duration:
            argv += ["--for", duration]
    elif action == "publish":
        argv += ["--yes", yes]
    return await _hand_off(argv)


@mcp.tool(
    name="thread",
    description=(
        "Post into, rename or branch a Loam thread, from a turn, a wake or a "
        "cron.\n"
        "- action `post` with `thread` (an id, or `general`), `text` and `by` "
        "(your name): appends your message to the thread's record. `bulletin` "
        "true marks a wake bulletin (\"woke\"), which goes in General, short, "
        "and only when there is something worth saying. An @mention in a post "
        "wakes the agent it names, as one in a reply does (capped like replies "
        "between agents); a post with no mention gives nobody a turn. `reply_to` (a message id like `m12`, from that thread) makes it "
        "a reply to that message, quoted above yours as the app's reply is; "
        "an id the thread does not hold is refused.\n"
        "- action `rename` with `thread` (an id), `title` and `by`: renames "
        "the thread as the app's details page does (record, file names and "
        f"board link together; General can't be), for when {lc.OWNER_NAME} asks or a "
        "thread's auto-title is clearly wrong.\n"
        "- action `branch` with `thread` (an id), `message` (the id of the "
        "message to branch from), `persona` (who the new thread is with; the "
        "source's default if empty), `title`, `text` and `by`: what the app's "
        "Branch into a new thread does. A new thread with that persona, whose "
        "first line links back to the message and with a line under the "
        "message linking forward; `text` is the opening waiting, unsent, in "
        f"{lc.OWNER_NAME}'s composer there (write it in his voice: what the branch is "
        "about and the context it needs, ending open). The persona's first "
        "turn there gets the stretch of the old thread leading to the "
        f"message. For when {lc.OWNER_NAME} asks for a branch.\n"
        "- action `list`: the threads, their members and their board files. "
        "A board is a markdown file you edit directly."
    ),
)
async def thread(action: str = "list", thread: str = "general", text: str = "",
                 by: str = "", bulletin: bool = False, title: str = "",
                 reply_to: str = "", message: str = "", persona: str = "") -> str:
    argv = [THREAD, action]
    if action == "branch":
        argv += [thread, message]
        for flag, value in (("--persona", persona), ("--title", title),
                            ("--text", text), ("--by", by)):
            if value:
                argv += [flag, value]
    if action == "rename":
        argv += [thread, title]
        if by:
            argv += ["--by", by]
    if action == "post":
        argv += [thread, text]
        if by:
            argv += ["--by", by]
        if bulletin:
            argv.append("--bulletin")
        if reply_to:
            argv += ["--reply-to", reply_to]
    return await _hand_off(argv)


MARKET = str(lc.ROOT / "bin" / "loam-market")


# The Market, for an agent (2026-11-18). The owner: "Agents should be able to
# go navigate the Marketplace and create that card in chat to let the user
# install something if they wanted to ... It should just be a capability that
# the agents have access to." The ranking is the Market page's own
# (lib/loam_marketplace.search); the card is a line of the reply.
@mcp.tool(
    name="market",
    description=(
        "Look in the Market (Settings › Market: themes, extensions, apps, "
        f"agents and skills others made for Loam) for something {lc.OWNER_NAME} "
        "wants that Loam can't do yet. `query` is plain words, as a person "
        "would type them (\"sync with my phone\", \"a calm dark theme\"); "
        "`kind` (theme, extension, app, agent or skill) narrows it. The same "
        "search, in the same order, as the Market's own field.\n"
        "Each result: id, name, author, `creator` (the maker's name and the "
        "links they chose to show, each with its kind: website, github, "
        "patreon, email...), kind, description, `cover` (a public address of its "
        "picture, when it has one), whether it is installed here, whether it is "
        "installable today (agents and skills are listed, not installed yet), "
        "and `card`.\n"
        "To offer one, put its `card` line in your reply on a line of its own, "
        "exactly as given (one to three): the app draws it as a card with the "
        "listing's icon, maker, reach and an Install button, and the thread "
        "record keeps it as the name linked to its page. Say in a sentence why "
        "it fits. The card is an offer: installing is always "
        f"{lc.OWNER_NAME}'s tap on it, never yours. When nothing fits, or the "
        "Market lists nothing (`say` tells you), offer to build it as their own "
        "plugin instead.\n"
        "`link`: instead of searching, read a plugin someone shared by its GitHub "
        "link (any shape: the repository's address, owner/repo, a /tree/ or release "
        "link). It finds the newest release and the plugin's folder, installs "
        "nothing, and answers what it is and touches with a `card` line to put in "
        "your reply the same way (a repository of several plugins answers each, "
        "with its own card). Its Install opens the app's install-from-link sheet."
    ),
)
async def market(query: str = "", kind: str = "", limit: int = 8, link: str = "") -> str:
    if link.strip():
        return await _hand_off([MARKET, "link", "--json", "--", link.strip()])
    argv = [MARKET, "search", "--json", "--limit", str(limit or 8)]
    if kind:
        argv += ["--kind", kind]
    # Words after `--`, so a query that starts with a dash is words.
    argv += ["--", *query.split()]
    return await _hand_off(argv)


SETTINGS = str(lc.ROOT / "bin" / "settings")


@mcp.tool(
    name="settings",
    description=(
        f"Change any setting {lc.OWNER_NAME} can change in the Loam app, through the "
        "same route the app's page uses, so it is checked and drawn live "
        "exactly as his click is, with your name on it (a thread gets a line "
        f"saying you did it). For when {lc.OWNER_NAME} asks you to change one.\n"
        "- action `describe`: every setting and action, the page route it "
        "is, and its reach: `persona` is yours; `owner` (sign-ins, "
        "credentials, config), `device` (kept in the owner's browser) and `own-tools` "
        "(file writes: use your file tools) say why not. With `setting` (and "
        "`target`): that one, its current value and its allowed values.\n"
        "- action `get` with `setting` and `target`: its value now.\n"
        "- action `set` with `setting`, `value`, `target` and `by` (your "
        "name): e.g. `thread.title`, `thread.icon` (an emoji), "
        "`thread.default` (a persona), `thread.engine`/`thread.model` (with "
        "`member`), `thread.pinned`, `appearance.desktop.theme`, "
        "`appearance.desktop.options.latte.agent-shapes` (a theme's option), "
        "`appearance.desktop.mode` (`sun` too), `appearance.location` (a JSON "
        "place, or null), "
        "`schedule.enabled`.\n"
        "- action `do` with `setting` (an action), `target`, `by`, and "
        "`value` where it takes one: e.g. `thread.members.add` (value a "
        "persona), `thread.create` (value a title), `thread.delete`, "
        f"`session.end`, `schedule.run`. Deleting and ending only on {lc.OWNER_NAME}'s "
        "word, never from a wake or a cron.\n"
        "`target` is a thread id (`mcp__engineer__thread` `list` gives them), "
        "a session id, or `<agent>/<name>` for a schedule. `value` is JSON "
        "when it reads as JSON (true, null, [\"iris\"]), else text. A "
        "schedule change is your own schedules only and needs a `reason`."
    ),
)
async def settings(action: str = "describe", setting: str = "", value: str = "",
                   target: str = "", member: str = "", by: str = "",
                   reason: str = "") -> str:
    argv = [SETTINGS]
    for flag, given in (("--target", target), ("--member", member),
                        ("--by", by), ("--reason", reason)):
        if given:
            argv += [f"{flag}={given}"]
    # Positionals after `--`, so a title that starts with a dash is a title.
    argv += ["--", action]
    if setting:
        argv.append(setting)
        if action in ("set", "do") and value != "":
            argv.append(value)
    return await _hand_off(argv)


if __name__ == "__main__":
    mcp.run()
