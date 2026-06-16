#!/usr/bin/env python3
"""
rollover-handoff.py — generate a lightweight, file-backed handoff for a fresh
Claude Code session, so a rolled-over task can be continued without the original
conversation's memory.

Reads the current session's transcript (.jsonl) plus git state and writes a
markdown handoff: the human's recent requests, what the previous session was
doing, files it touched, and the git diff at the moment of handoff.

Usage:  rollover-handoff.py <transcript.jsonl> <cwd> <used_percent> <out.md>
Prints the out path on success; exits non-zero (and writes nothing) on failure.
"""
import json
import subprocess
import sys

MAX_HUMAN = 5          # most recent human requests to include
MAX_ACTIONS = 18       # most recent tool actions to include
MAX_FILES = 40         # distinct files touched
TEXT_CLIP = 700        # clip long text blocks


def clip(s, n=TEXT_CLIP):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def blocks(content):
    """Normalize a message 'content' field to a list of block dicts."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def action_summary(name, inp):
    inp = inp if isinstance(inp, dict) else {}
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return f"{name} {inp.get('file_path') or inp.get('notebook_path') or ''}".strip()
    if name == "Bash":
        return f"Bash: {clip(inp.get('command', ''), 120)}"
    if name in ("Read", "Grep", "Glob"):
        tgt = inp.get("file_path") or inp.get("pattern") or inp.get("path") or ""
        return f"{name} {tgt}".strip()
    if name == "Task":
        return f"Task: {clip(inp.get('description', ''), 80)}"
    return name


def parse_transcript(path):
    human, actions, files, last_assistant_text = [], [], [], ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message", obj)
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role") or obj.get("type")
                bs = blocks(msg.get("content"))
                if role == "user":
                    # real human text only (skip tool_result echoes)
                    txt = " ".join(
                        b.get("text", "") for b in bs if b.get("type") == "text"
                    ).strip()
                    has_tool_result = any(b.get("type") == "tool_result" for b in bs)
                    if txt and not (has_tool_result and not txt):
                        # ignore command wrappers / system-ish noise
                        if not txt.startswith("<") or len(txt) > 40:
                            human.append(clip(txt, 400))
                elif role == "assistant":
                    for b in bs:
                        t = b.get("type")
                        if t == "text" and b.get("text", "").strip():
                            last_assistant_text = clip(b["text"], TEXT_CLIP)
                        elif t == "tool_use":
                            name = b.get("name", "?")
                            inp = b.get("input", {})
                            actions.append(action_summary(name, inp))
                            if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                                fp = (inp or {}).get("file_path") or (inp or {}).get(
                                    "notebook_path"
                                )
                                if fp and fp not in files:
                                    files.append(fp)
    except FileNotFoundError:
        pass
    return human, actions, files, last_assistant_text


def git(cwd, *args):
    try:
        out = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def main():
    if len(sys.argv) < 5:
        return 1
    transcript, cwd, used, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    human, actions, files, last_text = parse_transcript(transcript)

    status = git(cwd, "status", "--short")
    diffstat = git(cwd, "diff", "--stat")
    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    recent_commits = git(cwd, "log", "--oneline", "-5")

    L = []
    L.append("# Rollover handoff (auto-generated)\n")
    L.append(
        f"> The previous Claude Code session reached **{used}%** context and handed "
        f"off to this window. It has **no memory** of that conversation — this file "
        f"and the repo are the handoff. Read it, then continue from where it left off. "
        f"Do not redo completed work.\n"
    )
    if branch:
        L.append(f"- **Branch:** `{branch}`")
    L.append(f"- **Working dir:** `{cwd}`\n")

    L.append("## The task (most recent human requests)\n")
    if human:
        for h in human[-MAX_HUMAN:]:
            L.append(f"- {h}")
    else:
        L.append("- _(none captured — infer from the git diff below)_")
    L.append("")

    if last_text:
        L.append("## Where the previous session left off (its last message)\n")
        L.append(f"> {last_text}\n")

    L.append("## Recent actions it took\n")
    if actions:
        for a in actions[-MAX_ACTIONS:]:
            L.append(f"- {a}")
    else:
        L.append("- _(none captured)_")
    L.append("")

    if files:
        L.append("## Files it edited this session\n")
        for fp in files[:MAX_FILES]:
            L.append(f"- `{fp}`")
        L.append("")

    L.append("## Git state at handoff\n")
    if status:
        L.append("Uncommitted changes (`git status --short`):\n")
        L.append("```")
        L.append(status[:4000])
        L.append("```")
    else:
        L.append("_Working tree clean (no uncommitted changes)._")
    if diffstat:
        L.append("\n`git diff --stat`:\n")
        L.append("```")
        L.append(diffstat[:2000])
        L.append("```")
    if recent_commits:
        L.append("\nRecent commits:\n")
        L.append("```")
        L.append(recent_commits)
        L.append("```")
    L.append("")

    L.append("## Continue from here\n")
    L.append(
        "1. Read the human requests above and the uncommitted diff to see the task "
        "and how far it got.\n"
        "2. Continue the task from the next step. Preserve the approach already in "
        "progress; don't restart or re-decide settled choices.\n"
        "3. If the intent is genuinely unclear from the diff + requests, ask before "
        "making large changes."
    )

    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
    except Exception:
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
