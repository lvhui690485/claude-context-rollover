#!/usr/bin/env bash
#
# rollover-core.sh — shared logic for the Claude (PostToolUse hook) and Codex
# (background watcher) frontends. Sourced, not executed.
#
# The frontends own what genuinely differs (the context% signal, the trigger
# model, the stop mechanism, the seed text, and the per-agent env guards). Every
# function here is identical across both, so it lives in one place.

# Append a line to the rollover log.
rollover_log() {  # LOG MSG...
  local log="$1"; shift
  printf '%s %s\n' "$(date '+%F %T' 2>/dev/null)" "$*" >> "$log" 2>/dev/null
}

# Count SPAWN lines logged within the last 10 minutes (for burst auto-disable).
rollover_recent_spawns() {  # LOG NOW PY
  "$3" -c '
import sys,time,datetime
log,now=sys.argv[1],int(sys.argv[2]); c=0
try:
    for ln in open(log):
        if " SPAWN " not in ln: continue
        try:
            ep=time.mktime(datetime.datetime.strptime(ln[:19],"%Y-%m-%d %H:%M:%S").timetuple())
            if ep>=now-600: c+=1
        except Exception: pass
except FileNotFoundError: pass
print(c)' "$1" "$2" 2>/dev/null
}

# Add a repo-relative path to git's local exclude (worktree-safe: .git is a file
# in a worktree, a dir in a normal repo; rev-parse resolves the right exclude).
rollover_git_exclude() {  # CWD REL
  local cwd="$1" rel="$2" excl
  [ -e "$cwd/.git" ] || return 0
  excl="$(git -C "$cwd" rev-parse --git-path info/exclude 2>/dev/null)"
  [ -n "$excl" ] || return 0
  case "$excl" in /*) :;; *) excl="$cwd/$excl";; esac
  mkdir -p "$(dirname "$excl")" 2>/dev/null
  grep -qxF "$rel" "$excl" 2>/dev/null || printf '%s\n' "$rel" >> "$excl" 2>/dev/null || true
}

# Generate the handoff file. Returns 0 and writes OUT on success.
rollover_make_handoff() {  # PY GEN TRANSCRIPT CWD USED OUT
  local py="$1" gen="$2" transcript="$3" cwd="$4" used="$5" out="$6"
  [ -f "$gen" ] || return 1
  mkdir -p "$(dirname "$out")" 2>/dev/null || return 1
  "$py" "$gen" "$transcript" "$cwd" "$used" "$out" >/dev/null 2>&1 || return 1
  [ -s "$out" ]
}

# Open NEWCMD in a fresh pane/window. The frontend builds NEWCMD (per-agent
# guard + env sanitize + cd + `claude`/`codex '<seed>'`); this is the single
# place that knows the terminal backends. Returns 0 if a window opened.
rollover_spawn() {  # CWD NEWCMD
  local cwd="$1" newcmd="$2"
  if [ -n "${TMUX:-}" ]; then
    if tmux split-window -h -c "$cwd" "$newcmd" 2>/dev/null \
       || { tmux split-window -h -c "$cwd" 2>/dev/null && tmux send-keys "$newcmd" Enter 2>/dev/null; }; then
      return 0
    fi
  fi
  if [ "${TERM_PROGRAM:-}" = "iTerm.app" ]; then
    osascript \
      -e 'on run argv' \
      -e 'set cmd to item 1 of argv' \
      -e 'tell application "iTerm"' \
      -e '  activate' \
      -e '  tell current session of current window to set s to (split vertically with default profile)' \
      -e '  tell s to write text cmd' \
      -e 'end tell' \
      -e 'end run' \
      "$newcmd" >/dev/null 2>&1 && return 0
  fi
  if [ "${TERM_PROGRAM:-}" = "ghostty" ] || [ -d /Applications/Ghostty.app ]; then
    open -na Ghostty.app --args -e zsh -lc "$newcmd" >/dev/null 2>&1 && return 0
  fi
  osascript \
    -e 'on run argv' \
    -e 'set cmd to item 1 of argv' \
    -e 'tell application "Terminal"' \
    -e '  activate' \
    -e '  do script cmd' \
    -e 'end tell' \
    -e 'end run' \
    "$newcmd" >/dev/null 2>&1
}
