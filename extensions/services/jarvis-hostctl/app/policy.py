"""Pure command-risk policy for jarvis-hostctl.

classify(cmd) -> (decision, reason) where decision is "auto" or "ask".

Allowlist model: a command runs without asking only when EVERY segment of the
pipeline/sequence is a known read-only program used in a read-only way and
there is no output redirection or command substitution. Anything else asks.
Extend the allowlist with JARVIS_SAFE_COMMANDS (space-separated program names).
"""

import re
import shlex

SAFE_PROGRAMS = frozenset("""
ls ll cat head tail less more grep egrep fgrep rg wc sort uniq cut tr column
nl file stat du df free uptime date cal whoami id hostname pwd echo printf
which type uname lscpu lsblk lsusb lspci ps pgrep top htop pstree env printenv
basename dirname realpath readlink diff cmp md5sum sha256sum jq yq tree
ip ss ping dig nslookup host journalctl sensors nproc locale timedatectl
true false test
""".split())

# program -> regex the remaining args must NOT match (read-only use only)
CONDITIONAL = {
    "find": re.compile(r"(^|\s)-(delete|exec|execdir|ok|okdir|fprint\w*|fls)\b"),
    "sed": re.compile(r"(^|\s)(-i|--in-place)"),
    "awk": re.compile(r"system\s*\(|>\s*\""),
}

# program -> allowed first subcommands
SUBCOMMANDS = {
    "git": {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files", "blame"},
    "docker": {"ps", "logs", "images", "inspect", "stats", "version", "info"},
    "systemctl": {"status", "list-units", "list-timers", "is-active", "is-enabled", "show"},
    "ods": {"status", "list", "doctor", "logs", "version", "help"},
    "apt": {"list", "show", "search", "policy"},
    "apt-cache": {"policy", "show", "search"},
    "dpkg": {"-l", "-L", "-s", "--list"},
    "tailscale": {"status", "ip", "version", "netcheck"},
    "kscreen-doctor": {"-o"},
}

UNSAFE_SYNTAX = re.compile(r"(?<![<>&0-9])>|>>|\$\(|`|<\(|>\(")
SEGMENT_SPLIT = re.compile(r"\|\||&&|[|;&\n]")


def _segment_decision(segment: str, extra_safe: frozenset) -> tuple[str, str]:
    tokens = shlex.split(segment)
    if not tokens:
        return "auto", ""
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        tokens = tokens[1:]  # leading VAR=value assignments
    if not tokens:
        return "auto", ""
    prog = tokens[0].rsplit("/", 1)[-1]
    rest = " ".join(tokens[1:])
    if prog in SAFE_PROGRAMS or prog in extra_safe:
        return "auto", ""
    if prog in CONDITIONAL:
        if CONDITIONAL[prog].search(" " + rest):
            return "ask", f"'{prog}' used in a way that can modify files"
        return "auto", ""
    if prog in SUBCOMMANDS:
        sub = tokens[1] if len(tokens) > 1 else ""
        if sub in SUBCOMMANDS[prog]:
            return "auto", ""
        return "ask", f"'{prog} {sub}' can change the system"
    return "ask", f"'{prog}' is not on the read-only list"


def classify(cmd: str, extra_safe: frozenset = frozenset()) -> tuple[str, str]:
    if not cmd.strip():
        return "ask", "empty command"
    if UNSAFE_SYNTAX.search(cmd):
        return "ask", "uses output redirection or command substitution"
    for segment in SEGMENT_SPLIT.split(cmd):
        try:
            decision, reason = _segment_decision(segment.strip(), extra_safe)
        except ValueError:
            return "ask", "could not parse the command safely"
        if decision == "ask":
            return decision, reason
    return "auto", "read-only command"
