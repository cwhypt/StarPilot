import pytest

from openpilot.cereal import log
from opendbc.can import CANPacker

from openpilot.tools.longitudinal.probe_required_decel import SIG, probe, report, value

R = CANPacker("toyota_tss2_ars510")
PT = CANPacker("toyota_nodsu_pt_generated")


def pack(packer, name, bus, values):
  addr, dat, _ = packer.make_can_msg(name, bus, values)
  return addr, dat, bus


def ev(t, which):
  m = log.Event.new_message(logMonoTime=int(t * 1e9), valid=True)
  return m, m.init(which)


def can(t, frames):
  m = log.Event.new_message(logMonoTime=int(t * 1e9))
  lst = m.init("can", len(frames))
  for i, (a, d, s) in enumerate(frames):
    lst[i].address, lst[i].dat, lst[i].src = a, d, s
  return m


def drive(seconds=10.0, event=None, req=-1.9, send_state=True, car_aeb=False, alarm=False, brake_at=None, flags=16384 | 4):
  """60 ms radar cycles; `event` = (start, end) seconds where the radar's event object / REQUIRED_DECEL are active."""
  m, cp = ev(0.0, "carParams")
  cp.flags = flags
  msgs = [m]
  for k in range(int(seconds / 0.06)):
    t = 0.06 * (k + 1)
    events = [] if event is None else (event if isinstance(event, list) else [event])
    on = any(a <= t < b for a, b in events)
    frames = [pack(R, "PCS_STATE", 1, {"PCS_ALARM": int(on and alarm)})]
    frames.append(pack(R, "EVENT_OBJECT", 1, {"OBJECT_DIST": 12.0 if on else 0.0, "OBJECT_SPEED": 3.0 if on else 0.0}))
    if send_state:
      frames.append(pack(R, "EVENT_STATE", 1, {"REQUIRED_DECEL": req if on else 0.0}))
    frames.append(pack(R, "ACC_TARGET_1", 1, {"STATE": 5, "REL_SPEED": -8.0}))
    frames.append(pack(R, "ACC_TARGET_2", 1, {"PRESENT": 1, "LONG_DIST": 12.0}))
    if car_aeb:
      frames.append(pack(PT, "PRE_COLLISION", 0, {"PRECOLLISION_ACTIVE": int(on), "FORCE": -100.0 if on else 0.0}))
    msgs.append(can(t, frames))
    m, cs = ev(t, "carState")
    cs.vEgo, cs.brakePressed = 15.0, brake_at is not None and t >= brake_at
    msgs.append(m)
    m, lp = ev(t, "longitudinalPlan")
    lp.aTarget = 0.0
    msgs.append(m)
  return msgs


def test_signed_decode():
  dat = pack(R, "EVENT_STATE", 1, {"REQUIRED_DECEL": -1.52})[1]
  assert value(dat, SIG["req"]) == pytest.approx(-1.52, abs=0.095)
  dat = pack(R, "EVENT_STATE", 1, {"REQUIRED_DECEL": 2.85})[1]
  assert value(dat, SIG["req"]) == pytest.approx(2.85, abs=0.095)


def test_absent_0x196_means_c_cannot_work():
  r = probe(drive(send_state=False))
  assert r.state_frames == 0 and "0x196" not in r.rates
  assert "cannot work" in report(r)


def test_present_but_never_active():
  r = probe(drive())
  assert r.state_frames > 0 and r.active_frames == 0 and r.episodes == []
  assert r.rates["0x196"] == pytest.approx(16.7, abs=0.5)
  assert "never went active" in report(r)


def test_active_episode_is_reported_with_context():
  r = probe(drive(event=(4.0, 4.6), car_aeb=True, alarm=True, brake_at=5.0, flags=16384))
  assert len(r.episodes) == 1
  e = r.episodes[0]
  assert e.req_min == pytest.approx(-1.9, abs=0.095)
  assert e.obj_dist == pytest.approx(12.0) and e.acc_d == pytest.approx(12.0) and e.acc_v == pytest.approx(-8.0)
  assert e.pcs_alarm and e.car_aeb and e.driver_braked and not e.vision_lead
  assert 0 < r.active_frames == e.cycles and r.req_values
  text = report(r)
  assert "REQUIRED_DECEL observed: 1 episodes" in text and "car AEB" in text


def test_two_events_in_one_drive_are_two_episodes():
  r = probe(drive(seconds=12.0, event=[(3.0, 3.3), (9.0, 9.3)]))
  assert [round(e.start) for e in r.episodes] == [3, 9]
  assert not any(e.car_aeb or e.pcs_alarm or e.driver_braked for e in r.episodes)
