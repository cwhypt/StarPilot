#!/usr/bin/env python3
"""rav4_hotspotd: hotspot evaluation for the RAV4 (navigation first, GPS backup).

Reads navigation, GPS and carState; logs enter/exit of every conservative action as
cloudlog.error({"event": "rav4_hotspot", ...}) so it lands in qlog errorLogMessage. Alert actions (turn
take-over, hard-to-see light, manual turn) are shown to the driver as a short notification through
hotspot_alert; control actions (Experimental, speed cap, lane-change rules) stay log-only. It controls
nothing, and runs in its own process so a failure here cannot affect planning or control.
"""
import os
import time

import openpilot.cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.rav4.hotspot_alert import PRIORITY, write_alert
from openpilot.starpilot.rav4.hotspots import (GPS_MAX_AGE_S, HOTSPOT_PATH, MPH, NAV_MAX_AGE_S, Gps, HotspotShadow,
                                               Nav, TransitionLog, load_settings, load_spots)

RATE_HZ = 5
RELOAD_S = 10.0
ALERT_SHOW_S = 6.0
REARM_NAV_S = 10.0   # an alert must be gone this long before it can show again (GPS/heading/nav flicker)
REARM_SPOT_S = 30.0


def nav_from(sm, now_ns):
  if not sm.seen['starpilotNavigation'] or now_ns - sm.logMonoTime['starpilotNavigation'] > NAV_MAX_AGE_S * 1e9:
    return None
  n = sm['starpilotNavigation'].navigation
  if not n.enabled or n.status != 'guiding':
    return None
  i = n.instruction
  return Nav(maneuver_type=i.maneuverType, modifier=i.maneuverModifier, distance_m=float(i.distanceMeters), text=i.text)


def gps_from(sm, now_ns):
  if not sm.seen['gpsLocationExternal'] or now_ns - sm.logMonoTime['gpsLocationExternal'] > GPS_MAX_AGE_S * 1e9:
    return None
  g = sm['gpsLocationExternal']
  if not getattr(g, 'hasFix', True):
    return None
  return Gps(lat=g.latitude, lon=g.longitude, bearing=g.bearingDeg, accuracy_m=g.horizontalAccuracy,
             unix_ms=int(getattr(g, 'unixTimestampMillis', 0)))


class AlertPicker:
  """Which alert action to show now: each one for ALERT_SHOW_S after it starts, highest priority first.
  A shown alert re-arms only after it has been absent for REARM_*_S, so flicker never repeats it."""
  def __init__(self):
    self.started: dict[tuple[str, str], float] = {}
    self.last_seen: dict[tuple[str, str], float] = {}
    self.last_action: dict[tuple[str, str], object] = {}

  def pick(self, t, actions, settings):
    live = []
    for a in actions:
      if a.action != "alert":
        continue
      if (a.rule.startswith("N_") and not settings["turn_alerts"]) or (not a.rule.startswith("N_") and not settings["live_alerts"]):
        continue
      live.append(a)
    for k in list(self.started):
      rearm = REARM_NAV_S if k[0].startswith("N_") else REARM_SPOT_S
      if t - self.last_seen.get(k, t) > rearm:
        self.started.pop(k, None)
        self.last_seen.pop(k, None)
        self.last_action.pop(k, None)
    for a in live:
      k = (a.rule, a.detail.get("kind", ""))
      self.started.setdefault(k, t)
      self.last_seen[k], self.last_action[k] = t, a
    # keep showing through a brief dropout inside the show window (GPS heading / nav flicker)
    showing = [(PRIORITY.index(k[1]), self.last_action[k], f"{k[0]}:{k[1]}:{self.started[k]:.1f}")
               for k in self.started if k[1] in PRIORITY and t - self.started[k] <= ALERT_SHOW_S]
    return min(showing, key=lambda x: x[0])[1:] if showing else (None, None)


def main():
  sm = messaging.SubMaster(['carState', 'starpilotNavigation', 'gpsLocationExternal'])
  shadow, log, picker = HotspotShadow(load_spots()), TransitionLog(), AlertPicker()
  settings = load_settings()
  mtime, last_check, failures, shown_key = None, 0.0, 0, None
  cloudlog.error({"event": "rav4_hotspot", "phase": "start", "spots": len(shadow.spots), "path": HOTSPOT_PATH, **settings})
  rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)
  while True:
    sm.update(0)
    try:
      now = time.monotonic()
      if now - last_check > RELOAD_S:
        last_check = now
        m = os.path.getmtime(HOTSPOT_PATH) if os.path.exists(HOTSPOT_PATH) else None
        if m != mtime:
          mtime, shadow.spots, settings = m, load_spots(), load_settings()
          cloudlog.error({"event": "rav4_hotspot", "phase": "reload", "spots": len(shadow.spots), **settings})
      now_ns = time.monotonic_ns()
      cs = sm['carState']
      nav, gps = nav_from(sm, now_ns), gps_from(sm, now_ns)
      actions = shadow.evaluate(now, nav, gps, cs.vEgo, cs.leftBlinker, cs.rightBlinker)
      context = {"mph": round(cs.vEgo / MPH, 1), "nav": nav is not None, "gps": gps is not None}
      if gps is not None:
        context.update(lat=round(gps.lat, 5), lon=round(gps.lon, 5))
      for record in log.update(now, actions, context):
        cloudlog.error(record)
      action, key = picker.pick(now, actions, settings)
      if action is not None:
        write_alert(action.detail.get("kind"), action.detail, key, now_ns=now_ns)
        if key != shown_key:
          cloudlog.error({"event": "rav4_hotspot", "phase": "alert", "rule": action.rule, **action.detail, **context})
      elif shown_key is not None:
        write_alert(None, None, "", now_ns=now_ns)
      shown_key = key
      failures = 0
    except Exception:  # must never take anything else down; log and keep going
      failures += 1
      if failures in (1, 10, 100):
        cloudlog.exception("rav4_hotspotd failure")
    rk.keep_time()


if __name__ == "__main__":
  main()
