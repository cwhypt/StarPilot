"""Planner FCW for a radar-confirmed lead (ARS510 + Alpha Long opt-in); the vision (> 0.9) path is unchanged."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import fcw_lead_confirmed, should_trigger_planner_fcw


def lead(prob, radar=True, distance=15., lead_speed=10., present=True):
  msg = log.RadarState.new_message()
  msg.leadOne.present, msg.leadOne.radar, msg.leadOne.modelProb = present, radar, prob
  msg.leadOne.dRel, msg.leadOne.vLead = distance, lead_speed
  return msg.leadOne


@pytest.mark.parametrize("prob,radar,opt_in,expected", [
  (.95, False, False, True),   # vision path unchanged, with or without the opt-in
  (.95, False, True, True),
  (.7, True, False, False),    # without the opt-in a radar-matched lead below 0.9 still does not count
  (.7, True, True, True),
  (.7, False, True, False),    # vision-only lead below 0.9 does not count
  (.5, True, True, False),     # at/below the radard matching threshold
  (.0, True, True, False),     # radar-only low-speed override (modelProb 0)
])
def test_fcw_lead_confirmed(prob, radar, opt_in, expected):
  assert fcw_lead_confirmed(lead(prob, radar), opt_in) == expected


@pytest.mark.parametrize("distance,lead_speed,ego_speed,expected", [
  (15., 10., 20., True),      # closing 10 m/s, TTC 1.5 s
  (39.99, 10., 20., True),    # TTC just under 4 s
  (40., 10., 20., False),     # TTC 4 s
  (15., 19.6, 20., False),    # closing under 0.5 m/s
  (97., 25., 26.7, False),    # distant follow
])
def test_radar_confirmed_lead_needs_a_short_ttc(distance, lead_speed, ego_speed, expected):
  assert should_trigger_planner_fcw(lead(.7, True, distance, lead_speed), ego_speed, True) == expected
  assert not should_trigger_planner_fcw(lead(.7, True, distance, lead_speed), ego_speed)


def test_planner_flag_follows_the_radar_fcw_condition():
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.toyota.interface import CarInterface
  from opendbc.car.toyota.values import CAR, ToyotaFlags
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner

  with OpenpilotPrefix():
    cp = CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2_2023, gen_empty_fingerprint(), [], True, False, False)
    assert not LongitudinalPlanner(cp).mpc.radar_confirmed_fcw  # 7.0 default: radar disabled, nothing changes
    cp.flags |= int(ToyotaFlags.ARS510_RADAR)
    assert LongitudinalPlanner(cp).mpc.radar_confirmed_fcw


def test_actual_planner_applies_the_ttc_guard_to_a_radar_confirmed_lead():
  from opendbc.car import gen_empty_fingerprint
  from opendbc.car.toyota.interface import CarInterface
  from opendbc.car.toyota.values import CAR, ToyotaFlags
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  from openpilot.selfdrive.controls.tests.test_curve_runtime_loop import bus, NOW

  with OpenpilotPrefix():
    # crash_cnt is forced to 3 below, so this exercises the planner's own TTC guard; the MPC's probability gate is covered
    # by test_fcw_lead_confirmed
    for opt_in, prob, distance, expected in ((True, .7, 15., True), (True, .7, 97., False), (True, .95, 15., True)):
      cp = CarInterface.get_params(CAR.TOYOTA_RAV4_TSS2_2023, gen_empty_fingerprint(), [], True, False, False)
      if opt_in:
        cp.flags |= int(ToyotaFlags.ARS510_RADAR)
      planner = LongitudinalPlanner(cp, init_v=20.)
      sm = bus(NOW, lead_present=True, lead_distance=distance)
      radar = sm.messages['radarState'].as_builder()
      radar.leadOne.vLead, radar.leadOne.modelProb = 10., prob
      sm.messages['radarState'] = radar.as_reader()
      original = planner.mpc.update

      def predicted_collision(*args, _original=original, _planner=planner, **kwargs):
        _original(*args, **kwargs)
        _planner.mpc.crash_cnt = 3

      with patch.object(planner.mpc, 'update', side_effect=predicted_collision):
        planner.update(sm)
      assert planner.fcw == expected, (opt_in, prob, distance)
