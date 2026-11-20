"""Set the Claude Code permission mode on each lane’s launch command. The pinned
CLI honors its launch flag and the project’s explicit deny rules. A headless
lane cannot wait for tool approval. AskUserQuestion still uses the request
channel to collect the person’s answer.
"""

PERMISSION_MODE = "bypassPermissions"
