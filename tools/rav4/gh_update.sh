#!/usr/bin/env bash
# gh_update.sh — run ON the comma device. Updates /data/openpilot from the GitHub branch inside tmux (so a dropped
# phone SSH session does not kill it), remembers the previous commit, and rolls back over SSH if manager breaks.
#
#   bash /data/gh_update.sh status          # current / previous commit, manager running?, recent errors
#   bash /data/gh_update.sh check           # fetch and list what an update would bring, change nothing
#   bash /data/gh_update.sh update [--test] [--reboot] [<commit>]   # default: the branch head
#   bash /data/gh_update.sh rollback [--reboot]                     # back to the commit before the last update
#   bash /data/gh_update.sh official        # last resort: swap in /data/openpilot_official and reboot
#   bash /data/gh_update.sh log             # update / rollback history
#
# Only git: no ECU, panda or settings changes. Refuses while onroad and when tracked files have local changes.
set -u
OP=${OP:-/data/openpilot}
STATE=${STATE:-/data/rav4_update}
REPO=${REPO:-https://github.com/cwhypt/StarPilot.git}
BRANCH=${BRANCH:-rav4h-alpha-long-radar-on-7.0}
PY=/usr/local/venv/bin/python
SESSION=rav4update
TESTS="openpilot/starpilot/conditional_mode/tests opendbc_repo/opendbc/car/toyota/tests/test_lta_range.py
opendbc_repo/opendbc/car/toyota/tests/test_ars510.py openpilot/selfdrive/controls/tests/test_radard_dedupe.py"

# --- re-run inside tmux, so the update survives the SSH connection dropping
if [ -z "${TMUX:-}" ] && [ "${1:-}" != status ] && [ "${1:-}" != log ] && command -v tmux >/dev/null; then
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "an update session is already running; attaching (detach: Ctrl-b then d)"; exec tmux attach -t "$SESSION"
  fi
  exec tmux new-session -s "$SESSION" "bash $(printf '%q ' "$0" "$@"); echo; echo '[done - press Enter to close]'; read -r _"
fi

mkdir -p "$STATE"
say() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { say "ERROR: $*"; exit 1; }
g() { git -C "$OP" "$@"; }

onroad() {
  local v
  v=$(cd "$OP" 2>/dev/null && PYTHONPATH=$OP "$PY" -c "from openpilot.common.params import Params; print(int(Params().get_bool('IsOnroad')))" 2>/dev/null)
  [ "$v" = 1 ]
}
manager_running() { pgrep -f "system/manager/manager.py|manager.py" >/dev/null; }

need_safe() {
  [ -d "$OP/.git" ] || die "$OP is not a git checkout"
  onroad && die "the car is onroad: park and turn the car off (or wait until offroad) first"
  local dirty; dirty=$(g status --porcelain --untracked-files=no)
  [ -z "$dirty" ] || { echo "$dirty" | head -20; die "tracked files have local changes; not touching them (send this output)"; }
}

remote() {
  if g remote get-url gh >/dev/null 2>&1; then g remote set-url gh "$REPO"; else g remote add gh "$REPO"; fi
}

fetch() {
  remote
  say "fetching $BRANCH from $REPO ..."
  g fetch --depth=50 --no-tags gh "+refs/heads/$BRANCH:refs/remotes/gh/$BRANCH" || die "fetch failed (network? GitHub reachable?)"
}

run_tests() {
  say "running tests (a minute or two) ..."
  (cd "$OP" && PYTHONPATH=$OP:/data/pytest_lib timeout 900 "$PY" -m pytest -q -p no:cacheprovider $TESTS 2>&1 | tail -8)
}

ask_reboot() {
  local auto=$1
  if [ "$auto" = 1 ]; then say "rebooting"; sudo reboot; exit 0; fi
  printf "reboot now to start the new code? [y/N]: "; read -r a
  case "$a" in y|Y) say "rebooting"; sudo reboot ;; *) say "not rebooted; the new code starts at the next reboot" ;; esac
}

cmd=${1:-status}; shift || true
TEST=0; REBOOT=0; TARGET=""
for a in "$@"; do case "$a" in --test) TEST=1 ;; --reboot) REBOOT=1 ;; *) TARGET=$a ;; esac; done

case "$cmd" in
  status)
    echo "current : $(g log --oneline -1 | cat)"
    prev=$(cat "$STATE/prev_commit" 2>/dev/null)
    echo "previous: ${prev:-none} $( [ -n "$prev" ] && g log --oneline -1 "$prev" 2>/dev/null | cut -d' ' -f2- )"
    echo "branch  : $(g branch --show-current)   remote gh: $(g remote get-url gh 2>/dev/null || echo not set)"
    if manager_running; then echo "manager : running"; else echo "manager : NOT running"; fi
    if onroad; then echo "state   : onroad"; else echo "state   : offroad"; fi
    if tmux has-session -t comma 2>/dev/null; then
      echo "--- last lines of the openpilot tmux session:"; tmux capture-pane -p -t comma -S -15 2>/dev/null | grep -v '^\s*$' | tail -10
    fi
    ;;
  log) cat "$STATE/history" 2>/dev/null || echo "no history" ;;
  check)
    need_safe; fetch
    head=$(g rev-parse "refs/remotes/gh/$BRANCH")
    echo; echo "GitHub head: $(g log --oneline -1 "$head" | cat)"
    echo "installed  : $(g log --oneline -1 | cat)"
    echo; echo "file changes vs installed:"; g diff --stat HEAD "$head" | tail -15
    echo; echo "GitHub commits (newest first):"; g log --oneline -10 "$head" | cat
    ;;
  update)
    need_safe; fetch
    target=${TARGET:-$(g rev-parse "refs/remotes/gh/$BRANCH")}
    g cat-file -e "$target^{commit}" 2>/dev/null || die "commit $target not found in the fetched history"
    cur=$(g rev-parse HEAD)
    [ "$(g rev-parse "$target")" = "$cur" ] && { say "already at $(g log --oneline -1 | cat)"; exit 0; }
    if [ -n "$(g diff --stat HEAD "$target")" ]; then g diff --stat HEAD "$target" | tail -12; else say "same files, different commit ids"; fi
    echo "$cur" > "$STATE/prev_commit"
    g update-ref refs/rav4/prev "$cur"   # keeps the old commit from being garbage-collected
    branch=$(g branch --show-current); branch=${branch:-rav4h-radar}
    g checkout -q -B "$branch" "$target" || die "checkout failed; still on $(g log --oneline -1 | cat)"
    echo "$(date '+%F %T') update   $(git -C "$OP" rev-parse --short "$cur") -> $(g rev-parse --short HEAD)" >> "$STATE/history"
    say "installed: $(g log --oneline -1 | cat)   (rollback: bash /data/gh_update.sh rollback)"
    [ "$TEST" = 1 ] && run_tests
    ask_reboot "$REBOOT"
    ;;
  rollback)
    prev=$(cat "$STATE/prev_commit" 2>/dev/null) || die "no previous commit recorded"
    g cat-file -e "$prev^{commit}" 2>/dev/null || die "previous commit $prev is gone"
    onroad && die "the car is onroad: park first"
    cur=$(g rev-parse HEAD)
    branch=$(g branch --show-current); branch=${branch:-rav4h-radar}
    # tracked changes are discarded on rollback: the point is to get back to a known-good tree
    g checkout -q -f -B "$branch" "$prev" || die "checkout failed"
    echo "$cur" > "$STATE/prev_commit"
    echo "$(date '+%F %T') rollback $(git -C "$OP" rev-parse --short "$cur") -> $(g rev-parse --short HEAD)" >> "$STATE/history"
    say "rolled back to: $(g log --oneline -1 | cat)   (running rollback again returns to the newer one)"
    ask_reboot "$REBOOT"
    ;;
  official)
    [ -d /data/openpilot_official ] || die "/data/openpilot_official does not exist"
    onroad && die "the car is onroad: park first"
    printf "swap to the official StarPilot 7.0 in /data/openpilot_official and reboot? [y/N]: "; read -r a
    case "$a" in y|Y) ;; *) say "cancelled"; exit 0 ;; esac
    stamp=$(date +%Y%m%d-%H%M%S)
    mv /data/openpilot "/data/openpilot_rav4_$stamp" && mv /data/openpilot_official /data/openpilot || die "swap failed"
    echo "$(date '+%F %T') official (branch kept in /data/openpilot_rav4_$stamp)" >> "$STATE/history"
    say "official in place; the branch is in /data/openpilot_rav4_$stamp. Rebooting."
    sudo reboot
    ;;
  *) sed -n 2,12p "$0" ;;
esac
