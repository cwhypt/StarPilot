import pytest

from openpilot.cereal import log

from openpilot.tools.longitudinal.replay_radar_brake_cap import parse_params, replay, report


def msg(t, which):
  m = log.Event.new_message(logMonoTime=int(t * 1e9), valid=True)
  return m, m.init(which)


def drive(seconds=20.0, v_ego=20.0, closing=6.0, d0=70.0, lead_status=False, lead_track=-1, a_target=0.0,
          flags=16384, brake_at=None, start=5.0):
  """Radar ACC target (track 3) appears at `start` and closes at `closing` m/s; vision/planner as given."""
  msgs = []
  m, cp = msg(0.0, "carParams")
  cp.flags = flags
  msgs.append(m)
  for k in range(int(seconds * 100)):
    t = 0.01 * (k + 1)
    m, cs = msg(t, "carState")
    cs.vEgo, cs.brakePressed = v_ego, brake_at is not None and t >= brake_at
    msgs.append(m)
    if k % 5 == 0:
      m, lp = msg(t, "longitudinalPlan")
      lp.aTarget = a_target
      msgs.append(m)
      m, sd = msg(t, "selfdriveState")
      sd.enabled = True
      msgs.append(m)
      d = d0 - closing * (t - start)
      m, rs = msg(t, "radarState")
      rs.leadOne.present, rs.leadOne.dRel, rs.leadOne.radarTrackId = lead_status, d, lead_track
      msgs.append(m)
    if k % 6 == 0:  # ~16 Hz radar
      m, lt = msg(t, "radarTracks")
      d = d0 - closing * (t - start)
      if t >= start and d > 1.0:
        pts = lt.init("points", 1)
        pts[0].trackId, pts[0].dRel, pts[0].yRel, pts[0].vRel, pts[0].deprecated.measured = 3, d, 0.2, -closing, True
      msgs.append(m)
  return msgs


def test_vision_missed_closing_car_is_one_engaged_episode():
  r = replay(drive())
  assert len(r.episodes) == 1
  e = r.episodes[0]
  assert e.kind == "vision missed" and e.engaged and not e.driver_braked
  assert -2.5 <= e.min_cap < -0.2 and e.max_extra_brake > 0.2
  assert r.stationary == []
  assert "vision missed" in report(r)


def test_planner_already_braking_enough_is_not_an_episode():
  r = replay(drive(lead_status=True, lead_track=3, a_target=-3.0))
  assert r.episodes == []


def test_same_lead_planner_softer_is_classified_as_tuning():
  r = replay(drive(lead_status=True, lead_track=3, a_target=0.0))
  assert [e.kind for e in r.episodes] == ["same lead"]


def test_opening_gap_never_caps():
  r = replay(drive(closing=-2.0))
  assert r.episodes == [] and r.reasons.get("not closing", 0) > 0


def test_never_moving_target_is_listed_separately():
  r = replay(drive(closing=20.0, d0=120.0))  # v_lead = 0 from first sight
  assert r.episodes == []
  assert len(r.stationary) == 1 and r.stationary[0].kind == "vision missed"


def test_driver_brake_after_episode_is_flagged():
  r = replay(drive(brake_at=12.0))
  assert r.episodes and r.episodes[0].driver_braked


def test_mode_0_warning():
  assert "WARNING" in report(replay(drive(flags=0)))


def test_parse_params():
  p = parse_params(["cap_min=-2.0", "min_cycles=8"])
  assert p.cap_min == -2.0 and p.min_cycles == 8 and isinstance(p.min_cycles, int)
  with pytest.raises(SystemExit):
    parse_params(["nope=1"])
  r = replay(drive(), parse_params(["cap_min=-1.0"]))
  assert r.episodes[0].min_cap >= -1.0
