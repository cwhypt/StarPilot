#!/usr/bin/env bash
# analyze_drives.sh — run ON THE COMPUTER (macOS or Linux) after downloading phone_pull tars.
# Unpacks each tar into a folder beside it and writes report-<route>/ with:
#   model.txt                    which driving model the drive ran (tools/rav4/route_model.py, from qlogs)
#   check_t2_radar_fcw.txt       radar FCW events            } openpilot/tools/longitudinal, from rlogs
#   probe_required_decel.txt     radar REQUIRED_DECEL         } (falls back to qlogs when the tar has no rlogs,
#   replay_radar_brake_cap.txt   radar brake cap replay       }  which leaves the CAN-based checks mostly empty)
# These used to run on the device (phone_pull --report) and took a long time there.
#
#   bash tools/rav4/analyze_drives.sh ~/Downloads/0000000f--825904688e.tar
#   bash tools/rav4/analyze_drives.sh ~/Downloads/*.tar
#   bash tools/rav4/analyze_drives.sh ~/Downloads/comma_routes/drives-17_18     # an already unpacked folder
#
# Needs python3 with pycapnp and zstandard:  python3 -m pip install --user pycapnp zstandard
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PY=${PY:-python3}
[ $# -gt 0 ] || { sed -n 2,14p "$0"; exit 1; }
"$PY" -c "import capnp, zstandard" 2>/dev/null || {
  echo "python3 needs pycapnp and zstandard:  $PY -m pip install --user pycapnp zstandard"; exit 1; }

run() {  # $1 = output file, rest = command
  local out=$1; shift
  (cd "$REPO" && PYTHONPATH="$REPO:$REPO/opendbc_repo" "$PY" "$@") > "$out" 2>&1
}

for src in "$@"; do
  case "$src" in
    *.tar) dir=${src%.tar}; mkdir -p "$dir"; echo "unpacking $src -> $dir"; tar xf "$src" -C "$dir" || { echo "  bad tar"; continue; } ;;
    *) dir=$src ;;
  esac
  [ -d "$dir" ] || { echo "not found: $src"; continue; }
  routes=$(find "$dir" -maxdepth 2 -type d -name '*--*--*' | sed 's#.*/##; s/--[0-9]*$//' | sort -u)
  [ -n "$routes" ] || { echo "  no drive segments in $dir"; continue; }
  for R in $routes; do
    base=$(find "$dir" -maxdepth 2 -type d -name "$R--*" | head -1); base=$(dirname "$base")
    segs=$(ls -d "$base/$R"--* | sed 's/.*--//' | sort -n)
    pick() { for s in $segs; do ls "$base/$R--$s"/$1* 2>/dev/null | head -1; done; }
    QLOGS=$(pick qlog); RLOGS=$(pick rlog); LOGS=${RLOGS:-$QLOGS}
    OUT="$base/report-$R"; mkdir -p "$OUT"
    echo "$R ($(echo "$segs" | wc -l | tr -d ' ') segments, $( [ -n "$RLOGS" ] && echo rlogs || echo 'qlogs only'))"
    [ -n "$QLOGS" ] && run "$OUT/model.txt" tools/rav4/route_model.py $QLOGS && sed 's/^/  model: /' "$OUT/model.txt" | head -3
    for t in check_t2_radar_fcw probe_required_decel replay_radar_brake_cap; do
      f=openpilot/tools/longitudinal/$t.py; [ -f "$REPO/$f" ] || continue
      run "$OUT/$t.txt" "$f" $LOGS; echo "  $t: $(tail -1 "$OUT/$t.txt" | cut -c1-110)"
    done
    echo "  -> $OUT"
  done
done
