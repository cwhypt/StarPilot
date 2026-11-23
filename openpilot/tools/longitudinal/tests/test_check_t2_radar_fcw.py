from types import SimpleNamespace

from cereal import custom, log
from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR

from openpilot.tools.longitudinal.check_t2_radar_fcw import analyze, collect, intervals

ARS510 = CANPacker("toyota_tss2_ars510")
PT = CANPacker("toyota_nodsu_pt_generated")
POWER_UP = bytes.fromhex("ffff0000000000")
FLAGS_T2 = 16384 | 4


def frame(packer, name, bus, values):
  addr, dat, _ = packer.make_can_msg(name, bus, values)
  return addr, dat, bus


def can_event(t, which, frames):
  m = log.Event.new_message(logMonoTime=int(t * 1e9))
  lst = m.init(which, len(frames))
  for i, (addr, dat, src) in enumerate(frames):
    lst[i].address, lst[i].dat, lst[i].src = addr, dat, src
  return m


def drive(seconds=30.0, alarm=(), flags=FLAGS_T2, fw="8821F0R03100", radar_until=None, stock_fcw=False,
          map_event=True, send_dash=True, model_fcw=(), force_stock_fcw=None, power_up=True):
  """A synthetic route: the real carstate turns the radar-bus frames into stockFcw; the rest of the stack is simulated."""
  CP = CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2_2023, gen_empty_fingerprint(), [], True, False, False,
                               SimpleNamespace(toyota_radar_mode_requested=1))
  CS = CarInterface(CP, custom.StarPilotCarParams.new_message()).CS
  cp_radar = CarInterface(CP, custom.StarPilotCarParams.new_message()).can_parsers[Bus.radar]
  in_alarm = lambda t: any(s <= t < e for s, e in alarm)  # noqa: E731

  m = log.Event.new_message(logMonoTime=0)
  cp = m.init("carParams")
  cp.flags, cp.openpilotLongitudinalControl = flags, True
  fws = cp.init("carFw", 1)
  fws[0].ecu, fws[0].fwVersion = "fwdRadar", fw.encode()
  msgs = [m]

  for k in range(int(seconds * 100)):
    t = 0.01 * (k + 1)
    frames = []
    if k % 10 == 0 and (radar_until is None or t < radar_until):
      dat = POWER_UP if (k == 0 and power_up) else frame(ARS510, "PCS_STATE", 1, {"PCS_ALARM": int(in_alarm(t))})[1]
      frames.append((0x100, dat, 1))
    if stock_fcw and k % 10 == 0:
      frames.append(frame(PT, "PCS_HUD", 0, {"FCW": int(in_alarm(t))}))
    msgs.append(can_event(t, "can", frames))

    cp_radar.update([(int(t * 1e9), [f for f in frames if f[2] == 1])])
    fcw = CS.update_radar_pcs_alarm(cp_radar) if flags & 4 else False
    if force_stock_fcw is not None and t >= force_stock_fcw:
      fcw = True
    cs = log.Event.new_message(logMonoTime=int(t * 1e9))
    cs.init("carState").stockFcw = fcw
    msgs.append(cs)

    model = any(s <= t < e for s, e in model_fcw)
    ev = log.Event.new_message(logMonoTime=int(t * 1e9))
    names = (["fcw"] if (fcw and map_event) or model else [])
    lst = ev.init("onroadEvents", len(names))
    for i, n in enumerate(names):
      lst[i].name = n
    msgs.append(ev)

    sd = log.Event.new_message(logMonoTime=int(t * 1e9))
    sd.init("selfdriveState").enabled = True
    msgs.append(sd)
    mv = log.Event.new_message(logMonoTime=int(t * 1e9))
    mv.init("modelV2").meta.hardBrakePredicted = model
    msgs.append(mv)
    if send_dash and k % 2 == 0:
      msgs.append(can_event(t, "sendcan", [frame(PT, "PCS_HUD", 0, {"FCW": int(bool(names))})]))
  return msgs


def statuses(msgs):
  checks, episodes = analyze(collect(msgs))
  return {(c.step, c.name): c.status for c in checks}, checks, episodes


def test_intervals():
  assert intervals([(0, False), (1, True), (2, True), (3, False), (4, True)]) == [(1, 3), (4, 4)]
  assert intervals([(0, True), (1, False), (2, True), (3, True), (4, False)], min_count=2) == [(2, 4)]


def test_healthy_alpha_long_drive_with_one_alarm_passes():
  st, checks, episodes = statuses(drive(alarm=[(12.0, 13.5)]))
  assert not [c for c in checks if c.status == "FAIL"], checks
  assert st[("0", "flags")] == st[("0", "fwdRadar firmware")] == "PASS"
  assert st[("1", "0x100 rate")] == st[("1", "power-up frame")] == "PASS"
  assert st[("1", "no alarm in the first 10 s")] == "PASS"
  assert st[("2", "radar-bus alarms")] == "INFO"  # a person checks the video at the listed time
  assert st[("3", "alarm vs stock FCW")] == "N/A"
  assert st[("4", "alert chain")] == st[("5", "no alarm held while 0x100 is silent")] == "PASS"
  assert len(episodes) == 1 and episodes[0]["sources"] == ["radar (T2)"]


def test_quiet_drive_passes_and_power_up_frame_does_not_alarm():
  st, checks, episodes = statuses(drive())
  assert not [c for c in checks if c.status == "FAIL"], checks
  assert st[("2", "radar-bus alarms")] == "PASS" and st[("4", "alert chain")] == "N/A"
  assert episodes == []


def test_no_pcs_state_means_t2_cannot_work():
  st, _, _ = statuses(drive(radar_until=0.0))
  assert st[("1", "0x100 on bus 1")] == "FAIL"


def test_wrong_flags_and_firmware_are_reported():
  st, _, _ = statuses(drive(flags=4, fw="8821F0R99999"))
  assert st[("0", "flags")] == "FAIL" and st[("0", "fwdRadar firmware")] == "INFO"


def test_radar_going_silent_mid_alarm_clears_in_time():
  st, checks, _ = statuses(drive(alarm=[(12.0, 20.0)], radar_until=13.0))
  assert st[("1", "0x100 gaps > 0.5 s")] in ("PASS", "INFO")
  assert st[("5", "no alarm held while 0x100 is silent")] == "PASS", checks


def test_stuck_alarm_is_caught():
  st, _, _ = statuses(drive(radar_until=15.0, force_stock_fcw=16.0))
  assert st[("5", "no alarm held while 0x100 is silent")] == "FAIL"
  assert st[("4", "alert chain")] == "FAIL"  # stockFcw without a radar-bus alarm


def test_missing_event_mapping_or_dash_icon_is_caught():
  _, checks, _ = statuses(drive(alarm=[(12.0, 13.0)], map_event=False))
  assert "no fcw event" in next(c.detail for c in checks if c.name == "alert chain")
  _, checks, _ = statuses(drive(alarm=[(12.0, 13.0)], send_dash=False))
  assert "dash icon" in next(c.detail for c in checks if c.name == "alert chain")


def test_alarm_before_startup_window_ends_fails():
  st, _, _ = statuses(drive(alarm=[(3.0, 4.0)]))
  assert st[("1", "no alarm in the first 10 s")] == "FAIL"


def test_stock_long_drive_compares_car_bus_fcw_with_radar_bus_alarm():
  st, checks, _ = statuses(drive(alarm=[(12.0, 13.0)], flags=16384, stock_fcw=True))
  assert st[("0", "flags")] == "INFO" and st[("4", "alert chain")] == "N/A"
  assert st[("3", "alarm vs stock FCW")] == "PASS", checks


def test_stock_fcw_without_radar_bus_alarm_fails_step_3():
  msgs = drive(alarm=[(12.0, 13.0)], flags=16384, stock_fcw=True)
  for m in msgs:  # radar bus never alarms
    if m.which() == "can":
      for f in m.can:
        if f.address == 0x100 and f.src == 1 and bytes(f.dat) != POWER_UP:
          f.dat = frame(ARS510, "PCS_STATE", 1, {"PCS_ALARM": 0})[1]
  st, _, _ = statuses(msgs)
  assert st[("3", "alarm vs stock FCW")] == "FAIL"


def test_fcw_sources_are_attributed():
  _, _, episodes = statuses(drive(model_fcw=[(20.0, 21.0)]))
  assert [e["sources"] for e in episodes] == [["model"]]
