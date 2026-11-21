from types import SimpleNamespace

import pytest

from cereal import custom

from opendbc.can import CANPacker
from opendbc.car import Bus, gen_empty_fingerprint
from opendbc.car.toyota.carstate import ARS510_DBC, PCS_STATE_TIMEOUT_FRAMES, radar_pcs_fcw
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR

PCS_STATE = 0x100
POWER_UP_FRAME = bytes.fromhex("ffff0000000000")
DT_NANOS = 10_000_000  # carstate runs at 100 Hz


def get_params(candidate=CAR.TOYOTA_RAV4_TSS2_2023, alpha_long=True, mode=1, can_filter=False):
  fingerprint = gen_empty_fingerprint()
  if can_filter:
    fingerprint[0][0x2aa] = 8
  return CarInterface.get_params(candidate, fingerprint, [], alpha_long, False, False,
                                 SimpleNamespace(toyota_radar_mode_requested=mode))


def pcs_frame(alarm):
  return CANPacker(ARS510_DBC).make_can_msg("PCS_STATE", 1, {"PCS_ALARM": alarm})[1]


def make_interface(CP):
  return CarInterface(CP, custom.StarPilotCarParams.new_message())


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
def test_radar_pcs_parser_only_with_ars510_and_disabled_radar(alpha_long, mode, can_filter, expected):
  CP = get_params(alpha_long=alpha_long, mode=mode, can_filter=can_filter)
  assert radar_pcs_fcw(CP) == expected
  assert (Bus.radar in make_interface(CP).can_parsers) == expected


@pytest.mark.parametrize("candidate", [c for c in CAR if c not in (CAR.TOYOTA_RAV4_TSS2_2022, CAR.TOYOTA_RAV4_TSS2_2023)])
def test_other_toyotas_unchanged(candidate):
  CP = get_params(candidate)
  assert not radar_pcs_fcw(CP)
  assert Bus.radar not in make_interface(CP).can_parsers


def test_power_up_frame_is_ignored():
  drive = Drive(get_params())
  assert drive.step([(PCS_STATE, POWER_UP_FRAME, 1)]) is False
  assert drive.steps(9) == [False] * 9


def test_alarm_starts_and_ends_with_the_radar():
  drive = Drive(get_params())
  assert drive.step([(PCS_STATE, pcs_frame(0), 1)]) is False
  assert drive.step([(PCS_STATE, pcs_frame(1), 1)]) is True
  assert all(drive.steps(9))  # held between 10 Hz frames
  assert drive.step([(PCS_STATE, pcs_frame(0), 1)]) is False


def test_held_alarm_clears_when_the_radar_goes_silent():
  drive = Drive(get_params())
  assert drive.step([(PCS_STATE, pcs_frame(1), 1)]) is True
  held = drive.steps(PCS_STATE_TIMEOUT_FRAMES)
  assert all(held[:-1]) and held[-1] is False
  assert drive.step() is False


def test_frames_on_other_buses_are_ignored():
  drive = Drive(get_params())
  assert drive.step([(PCS_STATE, pcs_frame(1), 0), (PCS_STATE, pcs_frame(1), 2)]) is False


def test_missing_radar_bus_does_not_make_can_invalid():
  CP = get_params()
  radar = make_interface(CP).can_parsers[Bus.radar]
  radar.update([(DT_NANOS, [(0x2c1, bytes(8), 0)])])
  assert radar.can_valid and not radar.bus_timeout


def test_carstate_reports_the_radar_alarm_as_stock_fcw():
  toggles = SimpleNamespace(cluster_offset=1.0, sng_hack=False, lock_doors=False, unlock_doors=False, vEgoStopping=0.5)
  CI = make_interface(get_params())
  results = []
  for t, frames in enumerate([[(PCS_STATE, POWER_UP_FRAME, 1)], [(PCS_STATE, pcs_frame(1), 1)], [(PCS_STATE, pcs_frame(0), 1)]]):
    results.append(CI.update([((t + 1) * DT_NANOS, frames)], toggles)[0].stockFcw)
  assert results == [False, True, False]
