#!/usr/bin/env bash
# phone_pull.sh — run ON the comma device (e.g. from a Termius snippet). Packs drives into one .tar under
# /data/export so a phone can download a single file over SFTP. Device-side counterpart of comma_pull.sh.
#
#   bash /data/openpilot/tools/rav4/phone_pull.sh                 # menu
#   bash .../phone_pull.sh --latest                               # newest drive, logs + low-res video
#   bash .../phone_pull.sh --latest --time 5:40                   # newest drive, the minute around 5:40 (+-1 segment)
#   bash .../phone_pull.sh --route 9 --segs 4-6 --content qlog    # drive #9 in the list, segments 4..6, qlogs only
#   bash .../phone_pull.sh --list | --clean | --report
#   DATA=~/fakecomma/data bash phone_pull.sh ...                 # rehearse on a Mac (bash 3.2 / BSD tools are fine)
#
# --content: video (rlog+qlog+qcamera, default) | logs (rlog+qlog) | qlog | all (+ full-res cameras)
# --report:  also run the radar/longitudinal analysis tools on the device and put their output in the tar
set -u
DATA=${DATA:-/data}
ROOT=${ROOT:-$DATA/media/0/realdata}
EXPORT=${EXPORT:-$DATA/export}
PY=/usr/local/venv/bin/python
OP=$DATA/openpilot

ROUTE_NUM=""; SEGS=""; TIME=""; CONTENT=""; REPORT=0; MODE=menu
while [ $# -gt 0 ]; do
  case "$1" in
    --latest) MODE=args; ROUTE_NUM=latest; shift ;;
    --route) MODE=args; ROUTE_NUM="$2"; shift 2 ;;
    --segs) SEGS="$2"; shift 2 ;;
    --time) TIME="$2"; shift 2 ;;
    --content) CONTENT="$2"; shift 2 ;;
    --report) REPORT=1; shift ;;
    --list) MODE=list; shift ;;
    --clean) MODE=clean; shift ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
done

# GNU (device) / BSD (macOS) helpers
mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0; }
fsize() { wc -c < "$1" | tr -d ' '; }
fmt_time() { date -d "@$1" '+%m-%d %H:%M' 2>/dev/null || date -r "$1" '+%m-%d %H:%M' 2>/dev/null || echo '?'; }
dirbytes() { echo $(( $(du -ckL "$@" | tail -1 | cut -f1) * 1024 )); }
space() { df -h "$EXPORT" 2>/dev/null | tail -1; }

human() { awk -v b="$1" 'BEGIN{ split("B KB MB GB TB",u," "); i=1; while (b>=1024 && i<5){b/=1024;i++} printf (i==1?"%d %s":"%.1f %s"), b, u[i] }'; }

if [ "$MODE" = clean ]; then
  rm -f "$EXPORT"/*.tar; echo "removed packed files"; space; exit 0
fi

cd "$ROOT" 2>/dev/null || { echo "no $ROOT"; exit 1; }
# routes ordered by their hex counter (the device clock can be wrong), newest last
ROUTES=()
while read -r r; do [ -n "$r" ] && ROUTES+=("$r"); done < <(ls -d *--*--* 2>/dev/null | sed 's/--[0-9]*$//' | sort -u |
  while read -r r; do printf '%d %s\n' "$((16#${r%%--*}))" "$r"; done | sort -n | cut -d' ' -f2)
COUNT=${#ROUTES[@]}
[ "$COUNT" -eq 0 ] && { echo "no drives on the device"; exit 1; }

show_list() {
  echo "  #   route                      min   size      device time"
  local i r n b
  for ((i=0; i<COUNT; i++)); do
    r=${ROUTES[$i]}; n=$(ls -d "$r"--* | wc -l | tr -d ' '); b=$(dirbytes "$r"--*)
    printf "  %-3d %-26s %4d   %-9s %s\n" "$((i+1))" "$r" "$n" "$(human "$b")" "$(fmt_time "$(mtime "$r--0")")"
  done
}

if [ "$MODE" = list ]; then show_list; exit 0; fi

if [ "$MODE" = menu ]; then
  show_list
  printf "\ndrive # [%d = newest]: " "$COUNT"; read -r ROUTE_NUM; ROUTE_NUM=${ROUTE_NUM:-$COUNT}
  printf "time in the video (m:ss, +-1 min) or segments (e.g. 4-6), empty = whole drive: "; read -r ans
  case "$ans" in *:*) TIME=$ans ;; "") ;; *) SEGS=$ans ;; esac
  echo "content: 1) logs + low-res video [default]  2) logs only  3) qlogs only  4) everything"
  printf "choice [1]: "; read -r c
  case "${c:-1}" in 1) CONTENT=video ;; 2) CONTENT=logs ;; 3) CONTENT=qlog ;; 4) CONTENT=all ;; *) echo "invalid"; exit 1 ;; esac
  printf "run the analysis report too? [y/N]: "; read -r a; case "$a" in y|Y) REPORT=1 ;; esac
fi

[ "$ROUTE_NUM" = latest ] && ROUTE_NUM=$COUNT
case "$ROUTE_NUM" in ''|*[!0-9]*) echo "bad drive number"; exit 1 ;; esac
[ "$ROUTE_NUM" -ge 1 ] && [ "$ROUTE_NUM" -le "$COUNT" ] || { echo "no drive #$ROUTE_NUM"; exit 1; }
R=${ROUTES[$((ROUTE_NUM-1))]}
NSEG=$(ls -d "$R"--* | wc -l | tr -d ' ')

if [ -n "$TIME" ]; then
  m=${TIME%%:*}; s=${TIME#*:}; t=$((10#$m * 60 + 10#$s)); c=$((t / 60))
  SEGS="$((c > 0 ? c - 1 : 0))-$((c + 1))"
fi
if [ -n "$SEGS" ]; then
  a=${SEGS%-*}; b=${SEGS#*-}; LIST=$(seq "$a" "$b")
else
  LIST=$(seq 0 $((NSEG - 1)))
fi

case "${CONTENT:-video}" in
  video) FILES="rlog.zst qlog.zst rlog qlog qcamera.ts" ;;
  logs) FILES="rlog.zst qlog.zst rlog qlog" ;;
  qlog) FILES="qlog.zst qlog" ;;
  all) FILES="rlog.zst qlog.zst rlog qlog qcamera.ts fcamera.hevc ecamera.hevc" ;;
  *) echo "bad --content"; exit 1 ;;
esac

PATHS=()
for s in $LIST; do
  [ -d "$R--$s" ] || continue
  for f in $FILES; do [ -f "$R--$s/$f" ] && PATHS+=("$R--$s/$f"); done
done
[ ${#PATHS[@]} -eq 0 ] && { echo "nothing to pack for $R segments $(echo $LIST | tr '\n' ' ')"; exit 1; }

mkdir -p "$EXPORT"
TAG=$R; [ -n "$SEGS" ] && TAG="$R--seg$SEGS"
TAR="$EXPORT/$TAG.tar"

if [ "$REPORT" = 1 ]; then
  OUT=/data/drive_reports/$R; mkdir -p "$OUT"
  RLOGS=$(for s in $LIST; do ls "$ROOT/$R--$s"/rlog* 2>/dev/null | head -1; done)
  { echo "route $R segments $(echo $LIST | tr '\n' ' ')"; git -C "$OP" log --oneline -1 | cat
    for k in AlphaLongitudinalEnabled ToyotaRadarMode DisableUpdates; do echo "$k=$(cat /data/params/d/$k 2>/dev/null)"; done; } > "$OUT/info.txt"
  for t in check_t2_radar_fcw probe_required_decel replay_radar_brake_cap; do
    f=$OP/openpilot/tools/longitudinal/$t.py; [ -f "$f" ] || continue
    echo "analysis: $t ..."
    (cd "$OP" && PYTHONPATH=$OP timeout 900 "$PY" "$f" $RLOGS > "$OUT/$t.txt" 2>&1)
  done
  cp -r "$OUT" "$EXPORT/report-$R"
  PATHS_REPORT=(-C "$EXPORT" "report-$R")
else
  PATHS_REPORT=()
fi

tar chf "$TAR" -C "$ROOT" "${PATHS[@]}" ${PATHS_REPORT[@]+"${PATHS_REPORT[@]}"} && rm -rf "$EXPORT/report-$R"
echo
echo "packed: $TAR ($(human "$(fsize "$TAR")"), ${#PATHS[@]} files, drive #$ROUTE_NUM $R)"
echo "download it in Termius SFTP from $EXPORT/, then run the clean snippet."
space
