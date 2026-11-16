"""The permission mode every Claude Code lane on this box runs in, from one place.

The owner, 2026-11-12, after an Allow/Deny card for a background subagent's
``Monitor`` call reached a thread: "You should have permission to everything."
Since the authority move (2026-11-08) nothing on this box is enforced by a
permission prompt, and a prompt is either a card that looks like the persona
asking leave, or, on a lane nobody is attached to, a hang.

``bypassPermissions`` rather than ``acceptEdits`` plus an allow list, because
an allow list goes stale the day a CLI release adds a tool: ``Monitor``,
``SendMessage``, ``ToolSearch``, ``Workflow`` and ``Agent`` (the list said
``Task``) all prompted under ``acceptEdits`` before this. Measured on 2.1.322
the same day, as the box's user, in the broker's own stream-json line
(``--permission-prompt-tool stdio``):

- Bash, Write outside the project, ``.claude/`` and ``.git/`` writes,
  ToolSearch, Monitor and a background Agent: no ``can_use_tool`` request.
- A bare-name deny (``CronCreate``) still holds: the tool is not even in the
  init tool list, and ToolSearch cannot load it. A path deny
  (``Write(//<dir>/**)``) still refuses, without a prompt, and shows in
  ``permission_denials``.
- ``AskUserQuestion`` still arrives as ``can_use_tool``, which is what the
  broker turns into a question card. It is a question, not a permission.

So deny rules keep doing what they are for here (the scheduler is the
runtime's; the reviewer writes only its own state) and nothing asks.

**On the launch line, never as a settings ``defaultMode``.** 2.1.322
ignores ``bypassPermissions`` as a project's ``defaultMode`` and starts the
session in ``default`` (caused 2026-11-12: a persona's thread session reported
``permissionMode: default`` in its init with the setting in place, and a
scratch project the same); the ``--permission-mode`` flag is honoured, and
the project's deny rules apply under it. So every lane passes it: the
broker (``loam_broker.CLAUDE_STREAM_FLAGS``, conversations and threads), the
Remote Control units (``bin/gen-rc-units``), ``bin/wake``,
``bin/engineer-job`` (the platform engineer and every project engineer) and
``bin/review``. The settings files keep ``acceptEdits`` and their allow
lists as the fallback for a launch that passes no flag.
"""

PERMISSION_MODE = "bypassPermissions"
