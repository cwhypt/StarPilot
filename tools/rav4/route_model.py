#!/usr/bin/env python3
"""Which driving model each drive actually ran: the modeld.loaded identity modeld repeats every 5 s.

usage: route_model.py <seg0 qlog> [<seg1 qlog> ...]   (segment order; rlogs work too)
Prints one line per change: rt (s from the first log) modelId variant [fallbackReason].
"""
import json
import sys

from openpilot.tools.lib.logreader import _LogFileReader


def main(paths):
  t0, last, n = None, None, 0
  for path in paths:
    try:
      reader = _LogFileReader(path)
    except Exception as e:
      print(f"# {path}: unreadable ({e})")
      continue
    for ev in reader:
      try:
        which = ev.which()
      except Exception:
        continue
      if t0 is None:
        t0 = ev.logMonoTime
      if which not in ("logMessage", "errorLogMessage"):
        continue
      raw = getattr(ev, which)
      if "modeld.loaded" not in raw and "identity-unavailable" not in raw:
        continue
      try:
        msg = json.loads(raw).get("msg", {})
      except (ValueError, AttributeError):
        continue
      key = (msg.get("event"), msg.get("modelId"), msg.get("variant"), msg.get("fallbackReason"))
      if key != last:
        last, n = key, n + 1
        print(f"{(ev.logMonoTime - t0) / 1e9:8.1f}  {key[1] or '-'}  {key[2] or '-'}" +
              (f"  fallback={key[3]}" if key[3] else "") + ("" if key[0] == "modeld.loaded" else f"  ({key[0]})"))
  if n == 0:
    print("no model identity found")


if __name__ == "__main__":
  main(sys.argv[1:])
