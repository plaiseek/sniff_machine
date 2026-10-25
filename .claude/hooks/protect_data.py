"""Claude Code PreToolUse hook: block commands and edits that would destroy cache/test.db or
cache/backups/, which cannot be recomputed (see AGENTS.md). Exit code 2 blocks the tool call and
shows the message to the agent. It catches the usual mistakes, not every possible way:
- deleting, moving or truncating them (rm, mv, unlink, shred, truncate, find -delete, rsync --delete),
  also after `cd cache`;
- copying or redirecting something over the database (cp, install, ln, dd of=, > and >>);
- `git clean` with -x/-X (it deletes ignored files, cache/ included);
- any sqlite3 command-line use of the database without -readonly (the CLI has no foreign keys,
  hence no cascades, and SQL can come from arguments, stdin, heredocs or .restore).
Commands are split with a shell tokenizer, so quoted text (e.g. `grep "rm cache/test.db"`) and
heredoc bodies are not taken for commands.
"""

import json
import os
import re
import shlex
import sys

MESSAGE = (
    "Blocked by .claude/hooks/protect_data.py: cache/test.db and cache/backups/ hold data that cannot "
    "be recomputed. Read the database with .claude/skills/query-database/query.py; if this change is "
    "really wanted, ask the user to do it themselves."
)
OPERATORS = {";", "&&", "||", "|", "&", "|&", ";;", "(", ")"}
REDIRECTS = {">", ">>", "&>", "&>>", ">|"}
PREFIXES = {"sudo", "env", "command", "nohup", "time", "nice", "exec", "builtin"}

DB = re.compile(r"(^|/)cache/test\.db(-wal|-shm|-journal)?$")
CACHE_DIR = re.compile(r"(^|/)cache/?$")
IN_CACHE = re.compile(r"(^|/)cache/(test\.db|backups|\*|\?|\[)")  # cache/test.db*, cache/backups..., cache/*


def without_heredocs(command: str) -> str:
    """The command without the bodies of its heredocs (data, not commands)."""
    lines, pending = [], []
    for line in command.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        lines.append(line)
        pending += [m.group(2) for m in re.finditer(r"<<-?\s*(['\"]?)([\w.-]+)\1", line)]
    return "\n".join(lines)


def segments(command: str):
    """The simple commands of a shell command line, as lists of words."""
    lexer = shlex.shlex(without_heredocs(command).replace("\n", " ; "), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    current = []
    for token in lexer:
        if token in OPERATORS:
            if current:
                yield current
            current = []
        else:
            current.append(token)
    if current:
        yield current


def protected(path: str, cwd: str) -> bool:
    """Whether a path names the database, the backups or the cache folder (or may, through a glob)."""
    path = path.strip()
    if cwd == "cache":
        path = "cache/" + path.removeprefix("./")
    elif cwd == "backups":
        return True  # everything in cache/backups/ is a backup
    return bool(DB.search(path) or CACHE_DIR.search(path) or IN_CACHE.search(path))


def blocked_segment(words: list, cwd: str) -> tuple[bool, str]:
    """(blocked, new working directory kind) for one simple command."""
    # Redirections into the database, wherever they are in the command
    for i, word in enumerate(words):
        target = words[i + 1] if word in REDIRECTS and i + 1 < len(words) else None
        if target is not None and protected(target, cwd) and not CACHE_DIR.search(target):
            return True, cwd
    args = [w for i, w in enumerate(words) if w not in REDIRECTS and (i == 0 or words[i - 1] not in REDIRECTS | {"<"})]
    args = [w for w in args if w != "<"]
    # The command itself, behind sudo, env, VAR=value, xargs [options]...
    while args and (args[0] in PREFIXES or re.match(r"^\w+=", args[0])):
        args = args[1:]
    if args and os.path.basename(args[0]) == "xargs":
        args = args[1:]
        while args and args[0].startswith("-"):
            args = args[1:]
    if not args:
        return False, cwd
    name, rest = os.path.basename(args[0]), args[1:]
    paths = [a for a in rest if not a.startswith("-")]
    if name == "cd":
        target = paths[0] if paths else ""
        if re.search(r"(^|/)cache/backups/?$", target) or (cwd == "cache" and target.rstrip("/") == "backups"):
            return False, "backups"
        return False, "cache" if CACHE_DIR.search(target) else ("" if target.startswith("/") or not target else cwd)
    if name in ("rm", "unlink", "shred", "truncate", "rmdir", "mv", "srm", "trash", "trash-put"):
        return any(protected(p, cwd) for p in paths), cwd
    if name == "find":
        deleting = "-delete" in rest or any(a in ("rm", "/bin/rm", "shred", "unlink") for a in rest)
        return deleting and (cwd in ("cache", "backups") or any("cache" in p for p in paths)), cwd
    if name == "rsync":
        deleting = any(a.startswith("--delete") or a.startswith("--remove") for a in rest)
        return bool(paths) and (protected(paths[-1], cwd) or (deleting and "cache" in paths[-1])), cwd
    if name in ("cp", "install", "ln", "scp"):
        return bool(paths) and len(paths) > 1 and protected(paths[-1], cwd) and not CACHE_DIR.search(paths[-1]), cwd
    if name == "dd":
        return any(a.startswith("of=") and protected(a[3:], cwd) for a in rest), cwd
    if name == "sqlite3":
        names_db = any(DB.search(p) or (cwd == "cache" and p.startswith("test.db")) for p in paths)
        return names_db and "-readonly" not in rest, cwd
    if name == "git" and "clean" in rest:
        return any(re.match(r"^-[a-zA-Z]*[xX]", a) for a in rest), cwd
    return False, cwd


def blocked_command(command: str) -> bool:
    try:
        cwd = ""
        for words in segments(command):
            blocked, cwd = blocked_segment(words, cwd)
            if blocked:
                return True
        return False
    except ValueError:  # unbalanced quotes: look for the dangerous words anywhere
        return bool(re.search(r"\b(rm|mv|sqlite3|truncate|shred)\b.*cache/(test\.db|backups)|git\s+clean\s+-\w*[xX]", command))


def blocked(event: dict) -> bool:
    tool, tool_input = event.get("tool_name"), event.get("tool_input") or {}
    if tool == "Bash":
        return blocked_command(tool_input.get("command", ""))
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        return bool(re.search(r"(^|/)cache/(test\.db|backups/)", path))
    return False


if __name__ == "__main__":
    try:
        event = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    if blocked(event):
        print(MESSAGE, file=sys.stderr)
        sys.exit(2)
