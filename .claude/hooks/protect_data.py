"""Claude Code PreToolUse hook: block commands and edits that would destroy cache/test.db or
cache/backups/, which cannot be recomputed (see AGENTS.md). Exit code 2 blocks the tool call and
shows the message to the agent. Anything else is allowed: this only catches the usual mistakes
(rm/mv/cp over the database, git clean -x, writing SQL through the sqlite3 command line). A command
is only matched where a command starts (not inside quoted text, e.g. `grep "rm cache/test.db"`).
"""

import json
import re
import sys

MESSAGE = (
    "Blocked by .claude/hooks/protect_data.py: cache/test.db and cache/backups/ hold data that cannot "
    "be recomputed. Read the database with .claude/skills/query-database/query.py; if this change is "
    "really wanted, ask the user to do it themselves."
)
# Where a shell command starts: the beginning, or after ; & | ( newline or $( , optionally behind
# sudo / command / xargs [options]
START = r"(?:^|[;&|(\n]|\$\()\s*(?:sudo\s+|command\s+|xargs\s+(?:-\S+\s+)*)?"
REST = r"[^;&|\n]*"  # the rest of the same command
BASH_PATTERNS = [
    # deleting, moving or truncating the database, the backups or the whole cache folder
    rf"{START}(?:rm|mv|shred|truncate|unlink|rmdir)\b{REST}(?:\bcache/?(?:[\s)\"'*]|$)|\btest\.db\b|\bcache/backups\b)",
    # git clean with -x/-X also deletes ignored files, cache/ included
    rf"{START}git\s+clean\b{REST}\s-[a-zA-Z]*[xX]",
    # writing SQL through the sqlite3 command line (no foreign keys there: no cascades)
    rf"{START}sqlite3\b{REST}test\.db{REST}\b(?:delete|update|drop|insert|alter|replace|create|vacuum)\b",
    # copying a file over the database
    rf"{START}(?:cp|rsync|dd)\b{REST}\s\S*cache/test\.db\s*(?:$|[;&|\n])",
    # redirecting output into the database (the redirect's target is a path to it)
    r"(?<![<\w])>{1,2}\s*['\"]?[\w./~-]*cache/test\.db['\"]?(?=\s|$|[;&|)])",
]


def blocked(event: dict) -> bool:
    tool, tool_input = event.get("tool_name"), event.get("tool_input") or {}
    if tool == "Bash":
        command = tool_input.get("command", "")
        return any(re.search(p, command, re.IGNORECASE) for p in BASH_PATTERNS)
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        return bool(re.search(r"(^|/)cache/(test\.db|backups/)", path))
    return False


try:
    event = json.load(sys.stdin)
except ValueError:
    sys.exit(0)
if blocked(event):
    print(MESSAGE, file=sys.stderr)
    sys.exit(2)
