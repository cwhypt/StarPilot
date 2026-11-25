import json

from openpilot.starpilot.rav4.hotspots import (AFTER_EXIT_S, Gps, HotspotShadow, Nav, Spot, TransitionLog,
                                               distance_m, load_spots)

# synthetic coordinates (open ocean), never real places
LAT, LON = 10.0, -150.0
NORTH_10M = 10 / 111_195


def spot(**kw):
  base = dict(id="H01", name="test", action="curve", lat=LAT, lon=LON, heading=0.0, radius_m=60.0, cap_mph=20.0)
  base.update(kw)
  return Spot(**base)


def rules(actions):
  return {(a.rule, a.action) for a in actions}


def test_turn_ahead_requests_experimental_within_lookahead():
  s = HotspotShadow()
  assert ("N_turn", "experimental") in rules(s.evaluate(0, Nav("turn", "right", 70, "Main St"), None, 5, False, False))
  assert not s.evaluate(1, Nav("turn", "right", 300, "Main St"), None, 5, False, False)
  # 8 s of lookahead at 20 m/s
  assert ("N_turn", "experimental") in rules(s.evaluate(2, Nav("turn", "left", 150, "x"), None, 20, False, False))


def test_after_exit_window_only_when_the_ramp_was_taken():
  s = HotspotShadow()
  s.evaluate(0, Nav("off ramp", "right", 40, "Take the exit"), None, 20, False, False)
  out = s.evaluate(1, Nav("turn", "right", 300, "Gilman"), None, 15, False, False)
  assert {("N_after_exit", "experimental"), ("N_after_exit", "speed_cap")} <= rules(out)
  assert not rules(s.evaluate(1 + AFTER_EXIT_S + 1, Nav("turn", "right", 300, "Gilman"), None, 15, False, False))
  # an off-ramp instruction that disappears far from its point (reroute) is not a taken exit
  s = HotspotShadow()
  s.evaluate(0, Nav("off ramp", "right", 900, "Take the exit"), None, 20, False, False)
  assert not rules(s.evaluate(1, Nav("turn", "right", 300, "Gilman"), None, 15, False, False))


def test_exit_lane_change_needs_matching_signal():
  s = HotspotShadow()
  nav = Nav("off ramp", "right", 200, "Take exit 28B")
  assert not s.evaluate(0, nav, None, 25, False, False)
  assert not s.evaluate(0, nav, None, 25, True, False)
  assert ("N_exit", "lane_change_extra") in rules(s.evaluate(0, nav, None, 25, False, True))


def test_gps_spot_needs_accuracy_heading_and_proximity():
  s = HotspotShadow([spot()])
  near = Gps(LAT - 3 * NORTH_10M, LON, 0.0, 2.0)  # 30 m south, heading north
  assert ("H01", "speed_cap") in rules(s.evaluate(0, None, near, 10, False, False))
  assert not s.evaluate(0, None, Gps(near.lat, near.lon, 180.0, 2.0), 10, False, False)  # opposite direction
  assert not s.evaluate(0, None, Gps(near.lat, near.lon, 0.0, 25.0), 10, False, False)   # poor accuracy
  far = Gps(LAT - 20 * NORTH_10M, LON, 0.0, 2.0)  # 200 m south
  assert not s.evaluate(0, None, far, 5, False, False)
  assert ("H01", "speed_cap") in rules(s.evaluate(0, None, far, 25, False, False))  # 6 s lead at 25 m/s
  behind = Gps(LAT + 20 * NORTH_10M, LON, 0.0, 2.0)  # already past it
  assert not s.evaluate(0, None, behind, 25, False, False)


def test_fallback_spots_only_without_navigation():
  s = HotspotShadow([spot(only_without_navigation=True)])
  g = Gps(LAT, LON, 0.0, 2.0)
  assert rules(s.evaluate(0, None, g, 5, False, False))
  assert not {r for r in rules(s.evaluate(0, Nav("turn", "left", 900, "x"), g, 5, False, False)) if r[0] == "H01"}


def test_transition_log_reports_changes_only():
  log, a = TransitionLog(), HotspotShadow([spot()])
  g = Gps(LAT, LON, 0.0, 2.0)
  first = log.update(0, a.evaluate(0, None, g, 5, False, False), {})
  assert {r["phase"] for r in first} == {"enter"}
  assert log.update(1, a.evaluate(1, None, g, 5, False, False), {}) == []
  last = log.update(3, [], {})
  assert {r["phase"] for r in last} == {"exit"} and all(r["duration_s"] == 3 for r in last)


def test_load_spots_tolerates_bad_input(tmp_path):
  assert load_spots(str(tmp_path / "missing.json")) == []
  bad = tmp_path / "bad.json"
  bad.write_text("{not json")
  assert load_spots(str(bad)) == []
  good = tmp_path / "spots.json"
  good.write_text(json.dumps({"spots": [dict(id="H1", action="curve", lat=LAT, lon=LON, heading=0, cap_mph=20),
                                        dict(id="H2", action="curve", lat="x", lon=LON, heading=0),
                                        dict(id="H3", action="curve", lat=LAT, lon=LON, heading=0, radius_m=9999)]}))
  assert [s.id for s in load_spots(str(good))] == ["H1"]


def test_distance():
  assert abs(distance_m(LAT, LON, LAT + NORTH_10M, LON) - 10) < 0.1
