#!/usr/bin/env bash
# phone_pull.sh — run ON the comma device (e.g. from Termius). Packs drives into one .tar under /data/export so a
# phone can download a single file over SFTP. Device-side counterpart of comma_pull.sh, with the same menu.
#
#   bash /data/phone_pull.sh                                   # menu
#   bash /data/phone_pull.sh --latest                          # newest drive, logs + low-res video
#   bash /data/phone_pull.sh --latest --time 5:40              # newest drive, the minute around 5:40 (+-1 segment)
#   bash /data/phone_pull.sh --route 9 --segs 4-6 --content qlog   # drive #9 in the list, segments 4..6, qlogs
#   bash /data/phone_pull.sh --routes "3 5 7"                  # several drives by number
#   bash /data/phone_pull.sh --from 4                          # drive #4 up to the newest
#   bash /data/phone_pull.sh --last 3                          # the newest 3 drives
#   bash /data/phone_pull.sh --all
#   bash /data/phone_pull.sh --list | --clean
#   DATA=~/fakecomma/data bash phone_pull.sh ...              # rehearse on a Mac (bash 3.2 / BSD tools are fine)
#
# --content: video (rlog+qlog+qcamera, default) | logs (rlog+qlog) | qlog | all (+ full-res cameras)
# --report:  also run the radar/longitudinal analysis tools on the device and put their output in the tar
# --yes:     do not ask before packing (implied by the non-menu options)
# --time / --segs only apply when exactly one drive is picked.
set -u
DATA=${DATA:-/data}
ROOT=${ROOT:-$DATA/media/0/realdata}
EXPORT=${EXPORT:-$DATA/export}
PY=/usr/local/venv/bin/python
OP=$DATA/openpilot

SEL=""; SEL_ARG=""; SEGS=""; TIME=""; CONTENT=""; REPORT=0; MODE=menu; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --latest) MODE=args; SEL=latest; shift ;;
    --route|--routes) MODE=args; SEL=pick; SEL_ARG="$2"; shift 2 ;;
    --from) MODE=args; SEL=from; SEL_ARG="$2"; shift 2 ;;
    --last) MODE=args; SEL=last; SEL_ARG="$2"; shift 2 ;;
    --all) MODE=args; SEL=all; shift ;;
    --segs) SEGS="$2"; shift 2 ;;
    --time) TIME="$2"; shift 2 ;;
    --content) CONTENT="$2"; shift 2 ;;
    --report) REPORT=1; shift ;;
    --yes|-y) YES=1; shift ;;
    --list) MODE=list; shift ;;
    --clean) MODE=clean; shift ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
done
[ "$MODE" = args ] && YES=1

# GNU (device) / BSD (macOS) helpers
mtime() { stat -L -c %Y "$1" 2>/dev/null || stat -L -f %m "$1" 2>/dev/null || echo 0; }
fsize() { wc -c < "$1" | tr -d ' '; }
fmt_time() { date -d "@$1" '+%m-%d %H:%M' 2>/dev/null || date -r "$1" '+%m-%d %H:%M' 2>/dev/null || echo '?'; }
dirbytes() { echo $(( $(du -ckL "$@" | tail -1 | cut -f1) * 1024 )); }
avail_bytes() { mkdir -p "$EXPORT"; echo $(( $(df -Pk "$EXPORT" | tail -1 | awk '{print $4}') * 1024 )); }
space() { df -h "$EXPORT" 2>/dev/null | tail -1; }
human() { awk -v b="$1" 'BEGIN{ split("B KB MB GB TB",u," "); i=1; while (b>=1024 && i<5){b/=1024;i++} printf (i==1?"%d %s":"%.1f %s"), b, u[i] }'; }
isnum() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }

if [ "$MODE" = clean ]; then
  rm -f "$EXPORT"/*.tar; echo "removed packed files"; space; exit 0
fi

cd "$ROOT" 2>/dev/null || { echo "no $ROOT"; exit 1; }
# drives ordered by their hex route counter (the device clock can be wrong), newest last
ROUTES=()
while read -r r; do [ -n "$r" ] && ROUTES+=("$r"); done < <(ls -d *--*--* 2>/dev/null | sed 's/--[0-9]*$//' | sort -u |
  while read -r r; do printf '%d %s\n' "$((16#${r%%--*}))" "$r"; done | sort -n | cut -d' ' -f2)
COUNT=${#ROUTES[@]}
[ "$COUNT" -eq 0 ] && { echo "no drives on the device"; exit 1; }

show_list() {
  echo "  #   route                      min   size      device time*"
  local i r n b
  for ((i=0; i<COUNT; i++)); do
    r=${ROUTES[$i]}; n=$(ls -d "$r"--* | wc -l | tr -d ' '); b=$(dirbytes "$r"--*)
    printf "  %-3d %-26s %4d   %-9s %s\n" "$((i+1))" "$r" "$n" "$(human "$b")" "$(fmt_time "$(mtime "$r--0")")"
  done
  echo "  (* the device clock can be wrong until it syncs; ordered by route counter, newest last)"
}

if [ "$MODE" = list ]; then show_list; exit 0; fi

# --- choose drives
if [ "$MODE" = menu ]; then
  show_list
  echo
  echo "Which drives?"
  echo "  1) the newest one (#$COUNT)                 [default]"
  echo "  2) pick one or more by number (e.g. 3 or 3 5 7)"
  echo "  3) from number N to the newest"
  echo "  4) the newest K drives"
  echo "  5) all"
  printf "choice [1]: "; read -r c
  case "${c:-1}" in
    1) SEL=latest ;;
    2) SEL=pick; printf "numbers: "; read -r SEL_ARG ;;
    3) SEL=from; printf "start at #: "; read -r SEL_ARG ;;
    4) SEL=last; printf "how many: "; read -r SEL_ARG ;;
    5) SEL=all ;;
    *) echo "invalid choice"; exit 1 ;;
  esac
fi

PICK=()
case "$SEL" in
  latest) PICK=($((COUNT-1))) ;;
  pick) for x in $SEL_ARG; do isnum "$x" || { echo "bad number: $x"; exit 1; }; PICK+=($((x-1))); done ;;
  from) isnum "$SEL_ARG" || { echo "bad start: $SEL_ARG"; exit 1; }; for ((i=SEL_ARG-1; i<COUNT; i++)); do PICK+=($i); done ;;
  last) isnum "$SEL_ARG" || { echo "bad count: $SEL_ARG"; exit 1; }; for ((i=COUNT-SEL_ARG; i<COUNT; i++)); do [ $i -ge 0 ] && PICK+=($i); done ;;
  all) for ((i=0; i<COUNT; i++)); do PICK+=($i); done ;;
  *) echo "nothing selected"; exit 1 ;;
esac
[ ${#PICK[@]} -eq 0 ] && { echo "no drives selected"; exit 1; }
for i in "${PICK[@]}"; do
  if [ "$i" -lt 0 ] || [ "$i" -ge "$COUNT" ]; then echo "no drive #$((i+1)) (there are $COUNT)"; exit 1; fi
done

# --- part of a drive (only for a single drive)
if [ "$MODE" = menu ] && [ ${#PICK[@]} -eq 1 ]; then
  printf "time in the video (m:ss, +-1 min) or segments (e.g. 4-6), empty = whole drive: "; read -r ans
  case "$ans" in *:*) TIME=$ans ;; "") ;; *) SEGS=$ans ;; esac
fi
if [ ${#PICK[@]} -gt 1 ] && { [ -n "$TIME" ] || [ -n "$SEGS" ]; }; then
  echo "--time / --segs need exactly one drive; packing the whole drives"; TIME=""; SEGS=""
fi
if [ -n "$TIME" ]; then
  m=${TIME%%:*}; s=${TIME#*:}
  { isnum "$m" && isnum "$s"; } || { echo "bad time: $TIME (use m:ss)"; exit 1; }
  t=$((10#$m * 60 + 10#$s)); c=$((t / 60)); SEGS="$((c > 0 ? c - 1 : 0))-$((c + 1))"
fi
if [ -n "$SEGS" ]; then
  case "$SEGS" in *-*) a=${SEGS%-*}; b=${SEGS#*-} ;; *) a=$SEGS; b=$SEGS ;; esac
  { isnum "$a" && isnum "$b"; } || { echo "bad segments: $SEGS (use e.g. 4-6)"; exit 1; }
fi

# --- content
if [ "$MODE" = menu ]; then
  echo
  echo "What to copy?"
  echo "  1) logs + low-res video (rlog, qlog, qcamera.ts)     ~12 MB/min  [default]"
  echo "  2) logs only (rlog, qlog)                              ~10 MB/min"
  echo "  3) qlogs only (smallest, enough for most event checks) ~1 MB/min"
  echo "  4) everything incl. full-res road + wide video         ~90 MB/min"
  printf "choice [1]: "; read -r c
  case "${c:-1}" in 1) CONTENT=video ;; 2) CONTENT=logs ;; 3) CONTENT=qlog ;; 4) CONTENT=all ;; *) echo "invalid"; exit 1 ;; esac
  printf "Also run the radar/longitudinal analysis on the device and include the report? [y/N]: "; read -r ans
  case "$ans" in y|Y|yes) REPORT=1 ;; esac
fi
CONTENT=${CONTENT:-video}
case "$CONTENT" in
  video) FILES="rlog.zst qlog.zst rlog qlog qcamera.ts" ;;
  logs) FILES="rlog.zst qlog.zst rlog qlog" ;;
  qlog) FILES="qlog.zst qlog" ;;
  all) FILES="rlog.zst qlog.zst rlog qlog qcamera.ts fcamera.hevc ecamera.hevc" ;;
  *) echo "bad --content: $CONTENT"; exit 1 ;;
esac

# --- collect files
PATHS=()
for i in "${PICK[@]}"; do
  R=${ROUTES[$i]}; NSEG=$(ls -d "$R"--* | wc -l | tr -d ' ')
  if [ -n "$SEGS" ]; then LIST=$(seq "$a" "$b"); else LIST=$(seq 0 $((NSEG - 1))); fi
  for s in $LIST; do
    [ -d "$R--$s" ] || continue
    for f in $FILES; do [ -f "$R--$s/$f" ] && PATHS+=("$R--$s/$f"); done
  done
done
[ ${#PATHS[@]} -eq 0 ] && { echo "nothing to pack (no matching files)"; exit 1; }

TOTAL=$(dirbytes "${PATHS[@]}")
FREE=$(avail_bytes)
echo
printf '%s drive(s): ' "${#PICK[@]}"; for i in "${PICK[@]}"; do printf '#%d ' $((i+1)); done
[ -n "$SEGS" ] && printf '(segments %s)' "$SEGS"
echo
echo "${#PATHS[@]} files, $(human "$TOTAL"); free space for the tar: $(human "$FREE")"
if [ "$TOTAL" -gt $((FREE - 500 * 1024 * 1024)) ]; then
  echo "not enough free space (keeps 500 MB spare); pick fewer drives or qlogs only"; exit 1
fi
if [ "$YES" = 0 ]; then
  printf "pack it? [Y/n]: "; read -r ans; case "$ans" in n|N|no) echo "cancelled"; exit 0 ;; esac
fi

# --- tar name
first=${ROUTES[${PICK[0]}]}
if [ ${#PICK[@]} -eq 1 ]; then
  TAG=$first; [ -n "$SEGS" ] && TAG="$first--seg$SEGS"
else
  # drive numbers joined with "_" (e.g. drives-3_5_7), or first-to-last for long selections
  if [ ${#PICK[@]} -le 6 ]; then
    TAG="drives"; sep="-"; for i in "${PICK[@]}"; do TAG="$TAG$sep$((i+1))"; sep="_"; done
  else
    last_i=${PICK[$((${#PICK[@]}-1))]}
    TAG="drives-$(( PICK[0] + 1 ))-to-$(( last_i + 1 ))-${#PICK[@]}x"
  fi
fi
[ "$CONTENT" != video ] && TAG="$TAG-$CONTENT"
TAR="$EXPORT/$TAG.tar"

# --- optional on-device report
REPORT_DIRS=()
if [ "$REPORT" = 1 ]; then
  for i in "${PICK[@]}"; do
    R=${ROUTES[$i]}; OUT=$EXPORT/report-$R; mkdir -p "$OUT"
    NSEG=$(ls -d "$R"--* | wc -l | tr -d ' ')
    RLOGS=$(for s in $(seq 0 $((NSEG - 1))); do ls "$ROOT/$R--$s"/rlog* 2>/dev/null | head -1; done)
    { echo "route $R"; git -C "$OP" log --oneline -1 2>/dev/null | cat
      for k in AlphaLongitudinalEnabled ToyotaRadarMode DisableUpdates; do echo "$k=$(cat "$DATA/params/d/$k" 2>/dev/null)"; done; } > "$OUT/info.txt"
    for t in check_t2_radar_fcw probe_required_decel replay_radar_brake_cap; do
      f=$OP/openpilot/tools/longitudinal/$t.py; [ -f "$f" ] || continue
      echo "analysis $R: $t ..."
      (cd "$OP" && PYTHONPATH=$OP timeout 900 "$PY" "$f" $RLOGS > "$OUT/$t.txt" 2>&1)
    done
    REPORT_DIRS+=("report-$R")
  done
fi

echo "packing $TAR ..."
if [ ${#REPORT_DIRS[@]} -gt 0 ]; then
  tar chf "$TAR" -C "$ROOT" "${PATHS[@]}" -C "$EXPORT" "${REPORT_DIRS[@]}"; rc=$?
  for d in "${REPORT_DIRS[@]}"; do rm -rf "${EXPORT:?}/$d"; done
else
  tar chf "$TAR" -C "$ROOT" "${PATHS[@]}"; rc=$?
fi
[ $rc -eq 0 ] || { echo "tar failed"; rm -f "$TAR"; exit 1; }
echo
echo "packed: $TAR ($(human "$(fsize "$TAR")"), ${#PATHS[@]} files)"
echo "download it in Termius SFTP from $EXPORT/, then run: bash $DATA/phone_pull.sh --clean"
space
