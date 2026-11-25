#!/usr/bin/env python3
"""rav4_hotspotd: shadow-mode hotspot evaluation for the RAV4 (navigation first, GPS backup).

Reads navigation, GPS and carState; logs enter/exit of the conservative actions it *would* take as
cloudlog.error({"event": "rav4_hotspot", ...}) so they land in qlog errorLogMessage. It publishes nothing
and controls nothing. Runs in its own process so a failure here cannot affect planning or control.
"""
import os
import time

import openpilot.cereal.messaging as messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.starpilot.rav4.hotspots import (GPS_MAX_AGE_S, HOTSPOT_PATH, MPH, NAV_MAX_AGE_S, Gps, HotspotShadow,
                                               Nav, TransitionLog, load_spots)

RATE_HZ = 5
RELOAD_S = 10.0


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
  return Gps(lat=g.latitude, lon=g.longitude, bearing=g.bearingDeg, accuracy_m=g.horizontalAccuracy)


def main():
  sm = messaging.SubMaster(['carState', 'starpilotNavigation', 'gpsLocationExternal'])
  shadow, log = HotspotShadow(load_spots()), TransitionLog()
  mtime, last_check, failures = None, 0.0, 0
  cloudlog.error({"event": "rav4_hotspot", "phase": "start", "spots": len(shadow.spots), "path": HOTSPOT_PATH})
  rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)
  while True:
    sm.update(0)
    try:
      now = time.monotonic()
      if now - last_check > RELOAD_S:
        last_check = now
        m = os.path.getmtime(HOTSPOT_PATH) if os.path.exists(HOTSPOT_PATH) else None
        if m != mtime:
          mtime, shadow.spots = m, load_spots()
          cloudlog.error({"event": "rav4_hotspot", "phase": "reload", "spots": len(shadow.spots)})
      now_ns = time.monotonic_ns()
      cs = sm['carState']
      nav, gps = nav_from(sm, now_ns), gps_from(sm, now_ns)
      actions = shadow.evaluate(now, nav, gps, cs.vEgo, cs.leftBlinker, cs.rightBlinker)
      context = {"mph": round(cs.vEgo / MPH, 1), "nav": nav is not None, "gps": gps is not None}
      if gps is not None:
        context.update(lat=round(gps.lat, 5), lon=round(gps.lon, 5))
      for record in log.update(now, actions, context):
        cloudlog.error(record)
      failures = 0
    except Exception:  # shadow mode must never take anything else down; log and keep going
      failures += 1
      if failures in (1, 10, 100):
        cloudlog.exception("rav4_hotspotd failure")
    rk.keep_time()


if __name__ == "__main__":
  main()
