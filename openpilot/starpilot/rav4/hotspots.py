"""RAV4 hotspot shadow evaluation: navigation rules first, GPS hotspots as backup.

This module computes which conservative action applies (e.g. request Experimental, cap speed, refuse a lane
change toward a side, warn of a manual turn) and reports enter/exit transitions. It never changes vehicle
behaviour itself: rav4_hotspotd logs every action and turns only the "alert" ones into driver notifications;
the control actions stay log-only. Stale or inaccurate navigation/GPS data means "no action".

Hotspot file (private, never in the repo): /data/rav4_hotspots.json, the `hotspots_v2.json` format:
  {"version": 2, "spots": [{"id", "name", "action", "lat", "lon", "heading", "radius_m", "heading_tol_deg",
   "cap_mph"?, "offset_ft"?, "only_without_navigation"?, "night_only"?, "enabled"}],
   "live_alerts"?: true, "turn_alerts"?: true, ...}
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

HOTSPOT_PATH = os.environ.get("RAV4_HOTSPOTS", "/data/rav4_hotspots.json")
MAX_FILE_BYTES = 256 * 1024
MAX_SPOTS = 500

NAV_MAX_AGE_S = 2.0
GPS_MAX_AGE_S = 1.5
GPS_MAX_ACCURACY_M = 15.0
SPOT_LEAD_S = 6.0            # also match a spot this many seconds before reaching it
SPOT_AHEAD_TOL_DEG = 60.0    # the spot must be roughly ahead to count before reaching it

TURN_TYPES = ("turn", "end of road", "fork", "rotary", "roundabout")
TURN_MIN_LOOKAHEAD_M = 80.0
TURN_LOOKAHEAD_S = 8.0
EXIT_LOOKAHEAD_M = 400.0
EXIT_DONE_MAX_DIST_M = 60.0  # an off-ramp instruction counts as taken only if it ended this close
AFTER_EXIT_S = 30.0
AFTER_EXIT_MIN_SPEED = 4.0   # m/s
AFTER_EXIT_CAP_MPH = 25.0
MPH = 0.44704
TAKEOVER_MODIFIERS = ("left", "right", "sharp left", "sharp right", "uturn")  # EPS limits LTA to ~95 deg of wheel
TAKEOVER_MIN_DIST_M = 5.0    # "turn in 0 m" left over at/after the turn is not worth a chime
UTURN_MIN_DIST_M = 15.0      # a reroute's "make a U-turn" sits at 0-4 m (0000001c rt147-172): not a planned U-turn
START_NOFIX_S = 180.0        # start of drive without a GPS fix yet (garage exit): conservative until the fix
START_NOFIX_CAP_MPH = 15.0
MANUAL_ALERT_MIN_SPEED = 2.0  # m/s; crawling through a parking lot needs no chime for a manual-turn spot
SUN_DAY_ELEVATION_DEG = -2.0  # above this the sun counts as up (after sunrise / before civil dusk deepens)


@dataclass(frozen=True)
class Spot:
  id: str
  name: str
  action: str
  lat: float
  lon: float
  heading: float
  radius_m: float = 60.0
  heading_tol_deg: float = 45.0
  cap_mph: float | None = None
  offset_ft: float | None = None
  only_without_navigation: bool = False
  night_only: bool = False
  enabled: bool = False


@dataclass(frozen=True)
class Nav:
  """The current navigation instruction (starpilotNavigation.navigation), already checked fresh and guiding."""
  maneuver_type: str
  modifier: str
  distance_m: float
  text: str


@dataclass(frozen=True)
class Gps:
  lat: float
  lon: float
  bearing: float
  accuracy_m: float
  unix_ms: int = 0


@dataclass(frozen=True)
class Action:
  rule: str          # N_turn / N_after_exit / N_exit / H03 ...
  action: str        # experimental / speed_cap / lane_change_extra / no_lane_change_left / manual_turn / stopline ...
  detail: dict = field(default_factory=dict)


def _finite(*values):
  return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def distance_m(lat1, lon1, lat2, lon2):
  r = 6371000.0
  p1, p2 = math.radians(lat1), math.radians(lat2)
  dp, dl = p2 - p1, math.radians(lon2 - lon1)
  h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
  return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(lat1, lon1, lat2, lon2):
  p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
  y = math.sin(dl) * math.cos(p2)
  x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
  return math.degrees(math.atan2(y, x)) % 360.0


def angle_diff(a, b):
  return abs((a - b + 180.0) % 360.0 - 180.0)


def sun_elevation_deg(lat, lon, unix_ms):
  """Approximate solar elevation (NOAA low-precision formula, < 1 deg error); None without a valid time."""
  if not _finite(lat, lon, unix_ms) or unix_ms < 1.5e12:
    return None
  d = unix_ms / 86400000.0 - 10957.5  # days since J2000.0
  g = math.radians((357.529 + 0.98560028 * d) % 360)
  q = (280.459 + 0.98564736 * d) % 360
  lam = math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
  eps = math.radians(23.439 - 0.00000036 * d)
  dec = math.asin(math.sin(eps) * math.sin(lam))
  ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360
  gmst = (280.46061837 + 360.98564736629 * d) % 360
  ha = math.radians(((gmst + lon - ra + 180) % 360) - 180)
  la = math.radians(lat)
  return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))


def load_settings(path=HOTSPOT_PATH):
  """Top-level switches of the hotspot file; defaults when missing or malformed."""
  out = {"live_alerts": True, "turn_alerts": True}
  try:
    if os.path.isfile(path) and os.path.getsize(path) <= MAX_FILE_BYTES:
      with open(path, encoding="utf-8") as f:
        doc = json.load(f)
      for key in out:
        if isinstance(doc.get(key), bool):
          out[key] = doc[key]
  except (OSError, ValueError, AttributeError):
    pass
  return out


def load_spots(path=HOTSPOT_PATH):
  """Spots from the private hotspot file; a missing or malformed file gives no spots (no action)."""
  try:
    if not os.path.isfile(path) or os.path.getsize(path) > MAX_FILE_BYTES:
      return []
    with open(path, encoding="utf-8") as f:
      doc = json.load(f)
    spots = []
    for s in doc.get("spots", [])[:MAX_SPOTS]:
      try:
        spot = Spot(id=str(s["id"]), name=str(s.get("name", "")), action=str(s.get("action") or ""),
                    lat=float(s["lat"]), lon=float(s["lon"]), heading=float(s["heading"]),
                    radius_m=float(s.get("radius_m", 60.0)), heading_tol_deg=float(s.get("heading_tol_deg", 45.0)),
                    cap_mph=None if s.get("cap_mph") is None else float(s["cap_mph"]),
                    offset_ft=None if s.get("offset_ft") is None else float(s["offset_ft"]),
                    only_without_navigation=bool(s.get("only_without_navigation", False)),
                    night_only=bool(s.get("night_only", False)), enabled=bool(s.get("enabled", False)))
      except (KeyError, TypeError, ValueError):
        continue
      if (spot.action and _finite(spot.lat, spot.lon, spot.heading, spot.radius_m, spot.heading_tol_deg) and
          -90 <= spot.lat <= 90 and -180 <= spot.lon <= 180 and 0 < spot.radius_m <= 500 and 0 < spot.heading_tol_deg <= 180):
        spots.append(spot)
    return spots
  except (OSError, ValueError, AttributeError):
    return []


def _spot_actions(spot: Spot, v: float = 0.0):
  """Map a spot's action string to the conservative actions it would take."""
  out = []
  for part in spot.action.split("+"):
    if part == "curve":
      out.append(("speed_cap", {"cap_mph": spot.cap_mph or AFTER_EXIT_CAP_MPH}))
      out.append(("experimental", {}))
    elif part == "hidden_light":
      out.append(("experimental", {}))
      out.append(("alert", {"kind": "hidden_light"}))
      if spot.cap_mph:
        out.append(("speed_cap", {"cap_mph": spot.cap_mph}))
    elif part in ("manual_turn", "manual_zone"):
      out.append(("manual_turn", {"kind": part}))
      if v >= MANUAL_ALERT_MIN_SPEED:
        out.append(("alert", {"kind": part}))
    elif part == "stopline":
      out.append(("stopline", {"offset_ft": spot.offset_ft or 0.0}))
    elif part in ("no_lane_change_left", "no_lane_change_right"):
      out.append((part, {}))
    elif part == "exit_notice":
      out.append(("alert", {"kind": "exit"}))
  return out


class HotspotShadow:
  def __init__(self, spots=()):
    self.spots = list(spots)
    self.prev_nav: Nav | None = None
    self.exit_done_t: float | None = None
    self.start_t: float | None = None
    self.had_fix = False

  def evaluate(self, t: float, nav: Nav | None, gps: Gps | None, v_ego: float, left_blinker: bool, right_blinker: bool):
    """Actions that would apply now. `nav`/`gps` must be None when stale, missing or not guiding."""
    actions: list[Action] = []
    v = v_ego if _finite(v_ego) and v_ego > 0 else 0.0
    if self.start_t is None:
      self.start_t = t
    good_gps = gps is not None and _finite(gps.lat, gps.lon, gps.bearing, gps.accuracy_m) and gps.accuracy_m <= GPS_MAX_ACCURACY_M
    self.had_fix = self.had_fix or good_gps
    if not self.had_fix and t - self.start_t <= START_NOFIX_S and v > 1.0:
      actions.append(Action("S_nofix", "experimental", {}))
      actions.append(Action("S_nofix", "speed_cap", {"cap_mph": START_NOFIX_CAP_MPH}))

    # an off-ramp instruction that ended close to its point counts as taken
    if self.prev_nav is not None and self.prev_nav.maneuver_type == "off ramp" and \
       self.prev_nav.distance_m <= EXIT_DONE_MAX_DIST_M and (nav is None or nav.text != self.prev_nav.text or
                                                              nav.maneuver_type != "off ramp"):
      self.exit_done_t = t
    self.prev_nav = nav

    if nav is not None and _finite(nav.distance_m) and nav.distance_m >= 0:
      if nav.maneuver_type in TURN_TYPES and nav.distance_m <= max(TURN_MIN_LOOKAHEAD_M, v * TURN_LOOKAHEAD_S):
        actions.append(Action("N_turn", "experimental", {"type": nav.maneuver_type, "modifier": nav.modifier,
                                                         "dist_m": round(nav.distance_m)}))
        min_dist = UTURN_MIN_DIST_M if nav.modifier == "uturn" else TAKEOVER_MIN_DIST_M
        if nav.maneuver_type == "turn" and nav.modifier in TAKEOVER_MODIFIERS and nav.distance_m >= min_dist:
          actions.append(Action("N_turn", "alert", {"kind": "turn_takeover", "modifier": nav.modifier,
                                                    "dist_m": round(nav.distance_m)}))
      is_exit = nav.maneuver_type in ("off ramp", "fork") or "exit" in nav.text.lower()
      if is_exit and nav.distance_m <= EXIT_LOOKAHEAD_M:
        side = "right" if "right" in nav.modifier else "left" if "left" in nav.modifier else None
        signal = "right" if right_blinker and not left_blinker else "left" if left_blinker and not right_blinker else None
        if signal is not None and (side is None or side == signal):
          actions.append(Action("N_exit", "lane_change_extra", {"side": signal, "dist_m": round(nav.distance_m)}))
    if self.exit_done_t is not None and t - self.exit_done_t <= AFTER_EXIT_S and v > AFTER_EXIT_MIN_SPEED:
      actions.append(Action("N_after_exit", "experimental", {"since_s": round(t - self.exit_done_t, 1)}))
      actions.append(Action("N_after_exit", "speed_cap", {"cap_mph": AFTER_EXIT_CAP_MPH}))

    if good_gps:
      elevation = sun_elevation_deg(gps.lat, gps.lon, gps.unix_ms)
      sun_up = elevation is not None and elevation > SUN_DAY_ELEVATION_DEG
      for spot in self.spots:
        if spot.only_without_navigation and nav is not None:
          continue
        if spot.night_only and sun_up:
          continue
        if angle_diff(gps.bearing, spot.heading) > spot.heading_tol_deg:
          continue
        d = distance_m(gps.lat, gps.lon, spot.lat, spot.lon)
        ahead = angle_diff(bearing_deg(gps.lat, gps.lon, spot.lat, spot.lon), gps.bearing) <= SPOT_AHEAD_TOL_DEG
        if d <= spot.radius_m or (ahead and d <= spot.radius_m + v * SPOT_LEAD_S):
          for kind, detail in _spot_actions(spot, v):
            actions.append(Action(spot.id, kind, dict(detail, dist_m=round(d), enabled=spot.enabled)))
    return actions


class TransitionLog:
  """Turns per-cycle action sets into enter/exit records (one log line per change, not per cycle)."""
  def __init__(self):
    self.active: dict[tuple[str, str], tuple[float, dict]] = {}

  def update(self, t: float, actions, context: dict):
    records = []
    now = {(a.rule, a.action): a.detail for a in actions}
    for key, detail in now.items():
      if key not in self.active:
        self.active[key] = (t, detail)
        records.append({"event": "rav4_hotspot", "phase": "enter", "rule": key[0], "action": key[1], **detail, **context})
    for key in list(self.active):
      if key not in now:
        start, detail = self.active.pop(key)
        records.append({"event": "rav4_hotspot", "phase": "exit", "rule": key[0], "action": key[1],
                        "duration_s": round(t - start, 1), **context})
    return records
