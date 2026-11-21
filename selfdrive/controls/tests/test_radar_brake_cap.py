import math
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.controls.lib.radar_brake_cap import RadarBrakeCap, RadarCapParams, required_accel

P = RadarCapParams()


def pt(d=30.0, v=-5.0, y=0.0, tid=3, measured=True):
  return SimpleNamespace(trackId=tid, dRel=d, yRel=y, vRel=v, measured=measured)


def settled(points_fn, v_ego=20.0, cycles=P.min_cycles, dt=0.06):
  cap = RadarBrakeCap()
  res = None
  for k in range(cycles):
    res = cap.update(k * dt, points_fn(k), v_ego)
  return cap, res


def test_formula_matches_hand_calculation():
  # v_ego 20 -> margin max(4, 0.725*20) = 14.5 m; gap 30-14.5 = 15.5; closing 5 -> -25/31
  _, res = settled(lambda k: [pt()])
  assert res.reason == "cap"
  assert res.cap == pytest.approx(-25 / 31)
  assert res.required == pytest.approx(required_accel(30.0, 5.0, 20.0, P))


@pytest.mark.parametrize("points,reason", [
  ([], "no ACC target"),
  ([pt(tid=70)], "no ACC target"),          # Mode 2 summary car only
  ([pt(measured=False)], "no ACC target"),
  ([pt(y=2.0)], "target not in lane"),
  ([pt(d=0.5)], "target out of range"),
  ([pt(d=150)], "target out of range"),
  ([pt(v=0.0)], "not closing"),
  ([pt(v=-0.3)], "not closing"),
  ([pt(v=-1.0, d=80)], "closing gently"),
  ([pt(d=math.nan)], "non-finite target"),
])
def test_no_cap_cases(points, reason):
  _, res = settled(lambda k: points)
  assert res.cap is None and res.reason == reason


def test_needs_consecutive_cycles_and_resets_on_new_target():
  cap = RadarBrakeCap()
  results = [cap.update(k * 0.06, [pt()], 20.0) for k in range(P.min_cycles)]
  assert [r.reason for r in results[:-1]] == ["target too new"] * (P.min_cycles - 1)
  assert results[-1].reason == "cap"
  assert cap.update(0.4, [pt(tid=9)], 20.0).reason == "target too new"


def test_stale_gap_resets():
  cap, res = settled(lambda k: [pt()])
  assert res.reason == "cap"
  assert cap.update(10.0, [pt()], 20.0).reason == "target too new"


def test_unavailable_resets():
  cap, _ = settled(lambda k: [pt()])
  assert cap.update(0.5, [pt()], 20.0, unavailable=True).cap is None
  assert cap.update(0.56, [pt()], 20.0).reason == "target too new"


def test_never_moving_target_ignored_but_stopped_lead_is_capped():
  # stationary object: v_lead = 20 + (-20) = 0 from the start
  _, res = settled(lambda k: [pt(v=-20.0, d=60)])
  assert res.reason == "target never seen moving"
  # a lead that was moving, then stopped
  _, res = settled(lambda k: [pt(v=-5.0 if k < 2 else -20.0, d=60)])
  assert res.reason == "cap" and res.cap == P.cap_min


def test_inside_margin_while_closing_is_full_cap():
  _, res = settled(lambda k: [pt(d=10.0, v=-2.0)])
  assert res.required == -math.inf and res.cap == P.cap_min


def test_nearest_acc_target_used():
  _, res = settled(lambda k: [pt(d=80, tid=4), pt(d=30, tid=3), pt(d=10, tid=80)])
  assert res.track_id == 3


@pytest.mark.parametrize("d", [20.0, 30.0, 50.0, 90.0])
@pytest.mark.parametrize("closing", [0.6, 2.0, 5.0, 10.0])
@pytest.mark.parametrize("v_ego", [5.0, 15.0, 30.0])
def test_bounds_and_monotonic(d, closing, v_ego):
  _, res = settled(lambda k: [pt(d=d, v=-closing)], v_ego=v_ego)
  if res.cap is not None:
    assert P.cap_min <= res.cap <= P.activate
  _, closer = settled(lambda k: [pt(d=d * 0.8, v=-closing)], v_ego=v_ego)
  others = [closer]
  if v_ego - closing * 1.5 > P.min_moving_speed:  # a faster closing that makes the lead stationary is filtered on purpose
    others.append(settled(lambda k: [pt(d=d, v=-closing * 1.5)], v_ego=v_ego)[1])
  base = res.cap if res.cap is not None else 0.0
  for other in others:
    assert (other.cap if other.cap is not None else 0.0) <= base + 1e-9
