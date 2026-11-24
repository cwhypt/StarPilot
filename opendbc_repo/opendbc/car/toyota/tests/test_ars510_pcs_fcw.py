from pathlib import Path

import pytest

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.toyota.ars510_preferences import radar_pcs_fcw
from opendbc.car.toyota.carstate import ARS510_DBC, PCS_STATE_TIMEOUT_FRAMES
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import ARS510_RADAR_CAR, CAR, ToyotaFlags
from openpilot.common.params import Params
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences

PCS_STATE = 0x100
POWER_UP_FRAME = bytes.fromhex("ffff0000000000")
DT_NANOS = 10_000_000  # carstate runs at 100 Hz


class FilesystemParams:
  def __init__(self, path):
    self.path = path

  def get_default_value(self, key):
    return Params().get_default_value(key)

  def get_param_path(self, key):
    return str(self.path / key)


def get_params(tmp_path, candidate=CAR.TOYOTA_RAV4_TSS2_2023, alpha_long=True, mode=1, can_filter=False):
  """Car params as card builds them: factory params, then the startup preference owner (ToyotaRadarMode opt-in)."""
  fingerprint = gen_empty_fingerprint()
  if can_filter:
    fingerprint[0][0x2aa] = 8
  cp = CarInterface.get_params(candidate, fingerprint, [], alpha_long, False, False)
  params = FilesystemParams(tmp_path)
  Path(params.get_param_path("ToyotaRadarMode")).write_bytes(str(mode).encode())
  owner = VehicleStartupPreferences.read(params, enabled=True)
  owner.prepare(cp)
  owner.finalize(cp)
  return cp


def pcs_frame(alarm):
  return CANPacker(ARS510_DBC).make_can_msg("PCS_STATE", 1, {"PCS_ALARM": alarm})[1]


def make_interface(CP):
  return CarInterface(CP)


class Drive:
  def __init__(self, CP):
    CI = make_interface(CP)
    self.CS, self.cp_radar = CI.CS, CI.can_parsers[Bus.radar]
    self.t = 0

  def step(self, frames=()):
    # the radar-bus half of CarState.update: stockFcw = car-bus FCW (silenced here) or this
    self.t += DT_NANOS
    self.cp_radar.update([(self.t, list(frames))])
    return self.CS.update_radar_pcs_alarm(self.cp_radar)

  def steps(self, n, frames=()):
    return [self.step(frames) for _ in range(n)]


@pytest.mark.parametrize("alpha_long,mode,can_filter,expected", [
  (True, 1, False, True),
  (True, 2, False, True),
  (True, 0, False, False),   # radar mode off: no change
  (False, 1, False, False),  # stock long: the car-bus FCW is still there
  (True, 1, True, False),    # radar filter: radar not disabled, car-bus FCW is still there
])
def test_radar_pcs_parser_only_with_ars510_and_disabled_radar(tmp_path, alpha_long, mode, can_filter, expected):
  CP = get_params(tmp_path, alpha_long=alpha_long, mode=mode, can_filter=can_filter)
  assert radar_pcs_fcw(CP) == expected
  assert (Bus.radar in make_interface(CP).can_parsers) == expected


@pytest.mark.parametrize("candidate", [c for c in CAR if c not in ARS510_RADAR_CAR])
def test_other_toyotas_unchanged(tmp_path, candidate):
  CP = get_params(tmp_path, candidate)
  assert not radar_pcs_fcw(CP)
  assert Bus.radar not in make_interface(CP).can_parsers


def test_power_up_frame_is_ignored(tmp_path):
  drive = Drive(get_params(tmp_path))
  assert drive.step([(PCS_STATE, POWER_UP_FRAME, 1)]) is False
  assert drive.steps(9) == [False] * 9


def test_alarm_starts_and_ends_with_the_radar(tmp_path):
  drive = Drive(get_params(tmp_path))
  assert drive.step([(PCS_STATE, pcs_frame(0), 1)]) is False
  assert drive.step([(PCS_STATE, pcs_frame(1), 1)]) is True
  assert all(drive.steps(9))  # held between 10 Hz frames
  assert drive.step([(PCS_STATE, pcs_frame(0), 1)]) is False


def test_held_alarm_clears_when_the_radar_goes_silent(tmp_path):
  drive = Drive(get_params(tmp_path))
  assert drive.step([(PCS_STATE, pcs_frame(1), 1)]) is True
  held = drive.steps(PCS_STATE_TIMEOUT_FRAMES)
  assert all(held[:-1]) and held[-1] is False
  assert drive.step() is False


def test_frames_on_other_buses_are_ignored(tmp_path):
  drive = Drive(get_params(tmp_path))
  assert drive.step([(PCS_STATE, pcs_frame(1), 0), (PCS_STATE, pcs_frame(1), 2)]) is False


def test_missing_radar_bus_does_not_make_can_invalid(tmp_path):
  CP = get_params(tmp_path)
  radar = make_interface(CP).can_parsers[Bus.radar]
  radar.update([(DT_NANOS, [(0x2c1, bytes(8), 0)])])
  assert radar.can_valid and not radar.bus_timeout


def test_default_without_opt_in_is_unchanged(tmp_path):
  """ToyotaRadarMode 0 (default) with Alpha Long: radar stays disabled exactly as in stock 7.0."""
  CP = get_params(tmp_path, mode=0)
  assert CP.flags & ToyotaFlags.DISABLE_RADAR and CP.radarUnavailable
  assert not CP.flags & (ToyotaFlags.ARS510_RADAR | ToyotaFlags.ARS510_FUSED)
  assert not radar_pcs_fcw(CP)


def test_opt_in_enables_target_receiver_and_pcs_alarm(tmp_path):
  CP = get_params(tmp_path, mode=1)
  assert CP.flags & ToyotaFlags.DISABLE_RADAR and CP.flags & ToyotaFlags.ARS510_RADAR
  assert not CP.radarUnavailable and not CP.flags & ToyotaFlags.ARS510_FUSED
  assert radar_pcs_fcw(CP)


def test_carstate_reports_the_radar_alarm_as_stock_fcw(tmp_path):
  CI = make_interface(get_params(tmp_path))
  results = []
  for t, frames in enumerate([[(PCS_STATE, POWER_UP_FRAME, 1)], [(PCS_STATE, pcs_frame(1), 1)], [(PCS_STATE, pcs_frame(0), 1)]]):
    CI.can_parsers[Bus.radar].update([((t + 1) * DT_NANOS, frames)])
    results.append(CI.CS.update_radar_pcs_alarm(CI.can_parsers[Bus.radar]))
  assert results == [False, True, False]


def test_radar_bus_silence_after_15_s_and_recovery(tmp_path):
  from opendbc.car.toyota.carstate import RADAR_SILENT_FRAMES
  drive = Drive(get_params(tmp_path))
  drive.t += DT_NANOS
  drive.cp_radar.update([(drive.t, [(PCS_STATE, pcs_frame(0), 1)])])
  assert drive.CS.update_radar_bus_silence(drive.cp_radar) is False
  silent = []
  for _ in range(RADAR_SILENT_FRAMES):
    drive.t += DT_NANOS
    drive.cp_radar.update([(drive.t, [])])
    silent.append(drive.CS.update_radar_bus_silence(drive.cp_radar))
  assert not any(silent[:-1]) and silent[-1]
  drive.t += DT_NANOS
  drive.cp_radar.update([(drive.t, [(PCS_STATE, pcs_frame(0), 1)])])
  assert drive.CS.update_radar_bus_silence(drive.cp_radar) is False


def test_required_decel_event_is_logged_once_with_its_minimum(tmp_path, monkeypatch):
  from opendbc.car.toyota import carstate
  logged = []
  monkeypatch.setattr(carstate.carlog, "error", logged.append)
  drive = Drive(get_params(tmp_path))
  packer = CANPacker(ARS510_DBC)

  def cycle(dist, req):
    drive.t += DT_NANOS
    frames = [packer.make_can_msg("EVENT_OBJECT", 1, {"OBJECT_DIST": dist}), packer.make_can_msg("EVENT_STATE", 1, {"REQUIRED_DECEL": req})]
    drive.cp_radar.update([(drive.t, [(a, d, b) for a, d, b in frames])])
    drive.CS.update_required_decel_log(drive.cp_radar, 15.0)

  cycle(0, 0)
  for dist, req in ((20., -1.9), (18., -3.8), (16., -2.85)):
    cycle(dist, req)
  assert logged == []
  cycle(0, 0)
  assert len(logged) == 1
  ev = logged[0]
  assert ev["event"] == "ars510_required_decel" and ev["min_required_decel"] == pytest.approx(-3.8, abs=0.1)
  assert ev["min_object_dist"] == pytest.approx(16., abs=0.05) and ev["duration_s"] == 0.03


def test_radar_no_data_alert_is_shown_once_for_ten_seconds(tmp_path):
  from opendbc.car import structs
  from openpilot.cereal import log
  from openpilot.selfdrive.car.car_events import CarEvents, RADAR_NO_DATA_ALERT_FRAMES
  EventName = log.OnroadEvent.EventName

  def names(events, cs):
    return events.update(cs, cs, structs.CarControl()).names

  silent = structs.CarState(canValid=True, vEgo=10., gearShifter='drive', radarBusSilent=True)
  events = CarEvents(get_params(tmp_path))
  shown = [EventName.radarNoData in names(events, silent) for _ in range(RADAR_NO_DATA_ALERT_FRAMES + 50)]
  assert all(shown[:RADAR_NO_DATA_ALERT_FRAMES]) and not any(shown[RADAR_NO_DATA_ALERT_FRAMES:])
  # without the opt-in the field is never set and the alert never shows
  assert EventName.radarNoData not in names(CarEvents(get_params(tmp_path, mode=0)), silent)


def test_startup_silence_before_the_first_frame_gets_a_longer_window(tmp_path):
  from opendbc.car.toyota.carstate import RADAR_NEVER_SEEN_FRAMES, RADAR_SILENT_FRAMES
  drive = Drive(get_params(tmp_path))
  silent = []
  for _ in range(RADAR_NEVER_SEEN_FRAMES):
    drive.t += DT_NANOS
    drive.cp_radar.update([(drive.t, [])])
    silent.append(drive.CS.update_radar_bus_silence(drive.cp_radar))
  assert not any(silent[:RADAR_SILENT_FRAMES * 2]) and not any(silent[:-1]) and silent[-1]


def test_idle_required_decel_code_is_not_an_event(tmp_path, monkeypatch):
  from opendbc.car.toyota import carstate
  logged = []
  monkeypatch.setattr(carstate.carlog, "error", logged.append)
  drive = Drive(get_params(tmp_path))
  packer = CANPacker(ARS510_DBC)
  for _ in range(300):  # what the radar sends almost all the time under Alpha Long: ACC car in EVENT_OBJECT, code -1
    drive.t += DT_NANOS
    frames = [packer.make_can_msg("EVENT_OBJECT", 1, {"OBJECT_DIST": 40.}), packer.make_can_msg("EVENT_STATE", 1, {"REQUIRED_DECEL": -0.095})]
    drive.cp_radar.update([(drive.t, frames)])
    drive.CS.update_required_decel_log(drive.cp_radar, 15.0)
  assert drive.CS.required_decel_event is None and logged == []
