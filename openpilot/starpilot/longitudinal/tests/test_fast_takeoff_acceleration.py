import numpy as np
import pytest

from opendbc.car import ACCEL_MAX_TAKEOFF
from opendbc.car.interfaces import ACCEL_MAX
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner, get_cruise_accel, get_max_accel
from openpilot.starpilot.longitudinal.tests.test_conditional_handoff import Frame, NOW, DRIVE
from openpilot.starpilot.longitudinal.tests.test_ioniq6_start import candidate


@pytest.mark.parametrize('speed', [0., .5, 1., 2., 3., 4., 5., 6., 8., 9.])
def test_fast_takeoffs_raise_low_speed_default_within_takeoff_limit(speed):
  assert get_max_accel(speed, True) > get_max_accel(speed)
  assert get_max_accel(speed, True) <= ACCEL_MAX_TAKEOFF


@pytest.mark.parametrize('speed', [10., 15., 25., 40., 50.])
def test_fast_takeoffs_rejoin_unchanged_normal_acceleration_by_10ms(speed):
  assert get_max_accel(speed, True) == get_max_accel(speed)


@pytest.mark.parametrize('cap', [.2, .8, 1.2])
def test_saved_lower_cap_and_cruise_ceiling_still_bound_fast_takeoff(cap):
  cp = candidate()[1]
  assert get_cruise_accel(False, 30., 0., cap, 0., cp, .05, 0., True,
                          selected_acceleration_max=cap, faster_takeoffs=True) == cap
  assert get_cruise_accel(False, .1, 0., .1, 0., cp, .05, 0., True,
                          faster_takeoffs=True) == .1


def test_toggle_keeps_braking_and_selects_experimental_acceleration_ceiling():
  cp = candidate()[1]
  for e2e in (False, True):
    for set_speed in (0., 1.):
      args = (e2e, set_speed, 3., -1., 0., cp, .05, 0., True)
      assert get_cruise_accel(*args, faster_takeoffs=True) == get_cruise_accel(*args)
  args = (True, 30., 0., 2., 0., cp, .05, 0., True)
  assert get_cruise_accel(*args, faster_takeoffs=True) > get_cruise_accel(*args)
  for enabled, ceiling in ((False, ACCEL_MAX), (True, ACCEL_MAX_TAKEOFF)):
    accel = 0.
    for _ in range(50):
      accel = get_cruise_accel(True, 30., 0., accel, 0., cp, .05, 0., True, faster_takeoffs=enabled)
      assert accel <= ceiling
    assert accel == ceiling


def test_real_ioniq_planner_accelerates_faster_after_release_but_keeps_model_stop():
  cp = candidate()[1]
  normal, faster = (LongitudinalPlanner(cp) for _ in range(2))
  outputs = []
  for tick in range(65):
    now = NOW + tick * 50_000_000
    sm = Frame()
    sm.logMonoTime = dict.fromkeys(sm, now - 5_000_000)
    sm.recv_time = dict.fromkeys(sm, (now - 5_000_000) / 1e9)
    sm['carState'].vEgo = 0.
    sm['carState'].standstill = True
    sm['carState'].vCruise = 72.
    sm['selfdriveState'].experimentalMode = tick < 8
    sm['modelV2'].action.shouldStop = tick < 8
    sm['modelV2'].action.desiredAcceleration = -.04 if tick < 8 else 2.1
    for planner, enabled in ((normal, False), (faster, True)):
      planner.update(sm, faster_lead_takeoff=enabled, takeoff_drive_id=DRIVE, now_ns=now, drive_id=DRIVE)
      assert planner.mpc.solution_status == 0
      assert planner.output_a_target <= (ACCEL_MAX_TAKEOFF if enabled else ACCEL_MAX)
      if tick < 8:
        assert planner.output_should_stop
        assert planner.output_a_target <= 0.
    outputs.append((float(normal.output_a_target), float(faster.output_a_target)))
  assert outputs[-1][1] > outputs[-1][0] + .35
  assert outputs[-1] == pytest.approx((1.6, 3.5))
  assert not faster.output_should_stop
  assert np.isfinite(outputs).all()


def test_fast_takeoff_keeps_acceleration_after_first_few_mph():
  cp = candidate()[1]
  for speed in (3., 4., 5., 6., 8.):
    normal = faster = 0.
    for _ in range(40):
      normal = get_cruise_accel(False, 30., speed, normal, 0., cp, .05, 0., True)
      faster = get_cruise_accel(False, 30., speed, faster, 0., cp, .05, 0., True, faster_takeoffs=True)
    assert faster > normal + .15
    assert faster <= ACCEL_MAX_TAKEOFF


@pytest.mark.parametrize('speed', [3., 5., 8.])
def test_extended_takeoff_still_obeys_selected_cap_set_speed_and_cornering(speed):
  cp = candidate()[1]
  args = (False, 30., speed, 2., 0., cp, .05, 0., True)
  assert get_cruise_accel(False, 30., speed, .7, 0., cp, .05, 0., True, selected_acceleration_max=.7, faster_takeoffs=True) <= .7
  assert get_cruise_accel(False, speed + .1, speed, 0., 0., cp, .05, 0., True, faster_takeoffs=True) <= (speed + .1) - speed
  straight = get_cruise_accel(*args, faster_takeoffs=True)
  curve = get_cruise_accel(False, 30., speed, 2., 180., cp, .05, 0., True, faster_takeoffs=True)
  assert curve <= straight


def run_planner(enabled, ticks, speed, standstill):
  cp = candidate()[1]
  planner = LongitudinalPlanner(cp, init_v=speed)
  out = []
  for tick in range(ticks):
    now = NOW + tick * 50_000_000
    sm = Frame()
    sm.logMonoTime = dict.fromkeys(sm, now - 5_000_000)
    sm.recv_time = dict.fromkeys(sm, (now - 5_000_000) / 1e9)
    sm['carState'].vEgo = 0. if standstill(tick) else speed
    sm['carState'].standstill = standstill(tick)
    sm['carState'].vCruise = 72.
    sm['selfdriveState'].experimentalMode = False
    sm['modelV2'].action.shouldStop = False
    planner.update(sm, faster_lead_takeoff=enabled, takeoff_drive_id=DRIVE, now_ns=now, drive_id=DRIVE)
    out.append(float(planner.output_a_target))
  return planner, out


def test_rolling_at_low_speed_without_a_stop_is_not_a_takeoff():
  # e.g. finishing a turn at ~6 mph: no standstill this drive, so the normal low-speed ceiling applies
  _, normal = run_planner(False, 60, 2.7, lambda t: False)
  planner, faster = run_planner(True, 60, 2.7, lambda t: False)
  assert planner.takeoff_window_s == 0.
  assert max(faster) == pytest.approx(max(normal), abs=1e-6)


def test_takeoff_window_opens_at_standstill_and_closes_after_five_seconds():
  from openpilot.selfdrive.controls.lib.longitudinal_planner import TAKEOFF_WINDOW_S
  planner, _ = run_planner(True, 10, 2.7, lambda t: t < 5)  # stopped, then 5 ticks (0.25 s) rolling
  assert 0. < planner.takeoff_window_s < TAKEOFF_WINDOW_S
  ticks = int(TAKEOFF_WINDOW_S / 0.05) + 10
  planner, _ = run_planner(True, ticks, 2.7, lambda t: t < 5)
  assert planner.takeoff_window_s == 0.
