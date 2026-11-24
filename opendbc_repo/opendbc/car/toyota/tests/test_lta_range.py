"""LTA is released while the wheel is beyond the EPS angle range (tight turns) or the driver is clearly overriding it,
and taken back once the wheel has settled and the driver lets go."""
import pytest

from opendbc.car.toyota.carcontroller import (LTA_RELEASE_MARGIN, LTA_RESUME_FRAMES, LTA_RESUME_MARGIN, LTA_RESUME_MAX_RATE,
                                              lta_angle_in_range)
from opendbc.car.toyota.tests.test_auto_hold import decode, setup_hold, step_hold
from opendbc.car.toyota.values import CAR, CarControllerParams, STEER_THRESHOLD

MAX = 94.9461


def test_hysteresis():
  assert lta_angle_in_range(MAX + LTA_RELEASE_MARGIN - 1, 0, True, 0, MAX) == (True, 0)
  assert lta_angle_in_range(-(MAX + LTA_RELEASE_MARGIN + 1), 0, True, 0, MAX) == (False, 0)
  # back inside the release threshold but not yet inside the resume band: still released
  assert lta_angle_in_range(MAX, 0, False, 0, MAX) == (False, 0)
  # inside the resume band but still unwinding fast: still released, counter reset
  assert lta_angle_in_range(MAX - LTA_RESUME_MARGIN - 1, LTA_RESUME_MAX_RATE + 1, False, 5, MAX) == (False, 0)
  in_range, frames = False, 0
  for _ in range(LTA_RESUME_FRAMES - 1):
    in_range, frames = lta_angle_in_range(40., 20., in_range, frames, MAX)
    assert not in_range
  assert lta_angle_in_range(40., 20., in_range, frames, MAX)[0]


def test_driver_override_releases_and_blocks_resume():
  assert lta_angle_in_range(30., 120., True, 0, MAX, driver_override=True) == (False, 0)
  # driver still holding the wheel: no resume even when settled inside the range
  assert lta_angle_in_range(30., 0., False, LTA_RESUME_FRAMES, MAX, driver_pressing=True) == (False, 0)


def lta_frames(controller, command, state, cp):
  out = []
  for _ in range(2):  # STEERING_LTA goes out every other frame
    out += [decode(cp, f, 'STEERING_LTA') for f in step_hold(controller, command, state) if f[0] == 0x191]
  assert len(out) == 1
  return out[0]


@pytest.mark.parametrize("car", [CAR.TOYOTA_RAV4_TSS2_2023])
def test_controller_releases_lta_out_of_range_and_follows_the_wheel(car):
  cp, controller, command, state = setup_hold(car, enabled=False)
  state.out.standstill = state.out.brakePressed = False
  state.out.vEgo = state.out.vEgoRaw = 5.
  command.enabled = command.latActive = True
  command.actuators.steeringAngleDeg = 80.

  state.out.steeringAngleDeg = 60.
  lta = lta_frames(controller, command, state, cp)
  assert lta['STEER_REQUEST'] == 1

  # driver turns the wheel to 250 deg in a tight turn: LTA released, command clamped to the wheel (EPS limit)
  state.out.steeringAngleDeg, state.out.steeringRateDeg = 250., 0.
  lta = lta_frames(controller, command, state, cp)
  assert lta['STEER_REQUEST'] == 0 and lta['STEER_REQUEST_2'] == 0 and lta['TORQUE_WIND_DOWN'] == 0
  assert lta['STEER_ANGLE_CMD'] == pytest.approx(CarControllerParams(cp).ANGLE_LIMITS.STEER_ANGLE_MAX, abs=0.1)

  # unwinding fast through 120 deg: still released (this is where the EPS used to fault)
  state.out.steeringAngleDeg, state.out.steeringRateDeg = 120., -200.
  assert lta_frames(controller, command, state, cp)['STEER_REQUEST'] == 0

  # settled at 30 deg: taken back after the resume delay, starting from the wheel angle
  state.out.steeringAngleDeg, state.out.steeringRateDeg = 30., 0.
  requests = [lta_frames(controller, command, state, cp) for _ in range(LTA_RESUME_FRAMES // 2 + 2)]
  assert requests[0]['STEER_REQUEST'] == 0 and requests[-1]['STEER_REQUEST'] == 1
  first = next(r for r in requests if r['STEER_REQUEST'])
  assert abs(first['STEER_ANGLE_CMD'] - 30.) < 1.


@pytest.mark.parametrize("car", [CAR.TOYOTA_RAV4_TSS2_2023])
def test_controller_releases_lta_when_driver_overpowers_it(car):
  cp, controller, command, state = setup_hold(car, enabled=False)
  state.out.standstill = state.out.brakePressed = False
  state.out.vEgo = state.out.vEgoRaw = 4.
  command.enabled = command.latActive = True
  command.actuators.steeringAngleDeg = 20.
  state.out.steeringAngleDeg = 20.
  for _ in range(100):  # let the rate-limited command reach the wheel angle
    lta = lta_frames(controller, command, state, cp)
  assert lta['STEER_REQUEST'] == 1 and abs(lta['STEER_ANGLE_CMD'] - 20.) < 1.

  # light hands-on resistance close to the command (lane change nudge): LTA stays active
  state.out.steeringTorque, state.out.steeringAngleDeg = STEER_THRESHOLD + 50, 25.
  assert lta_frames(controller, command, state, cp)['STEER_REQUEST'] == 1

  # driver flicks the wheel away from the command (recorded fault pattern): released, command follows the wheel
  state.out.steeringTorque, state.out.steeringAngleDeg, state.out.steeringRateDeg = STEER_THRESHOLD + 50, 45., 130.
  lta = lta_frames(controller, command, state, cp)
  assert lta['STEER_REQUEST'] == 0
  assert abs(lta['STEER_ANGLE_CMD'] - 45.) < 1.5

  # wheel settled but still held: stays released
  state.out.steeringRateDeg = 0.
  for _ in range(LTA_RESUME_FRAMES):
    assert lta_frames(controller, command, state, cp)['STEER_REQUEST'] == 0

  # driver lets go: taken back after the resume delay, from the wheel angle
  state.out.steeringTorque = 0.
  requests = [lta_frames(controller, command, state, cp) for _ in range(LTA_RESUME_FRAMES // 2 + 2)]
  assert requests[0]['STEER_REQUEST'] == 0 and requests[-1]['STEER_REQUEST'] == 1
  first = next(r for r in requests if r['STEER_REQUEST'])
  assert abs(first['STEER_ANGLE_CMD'] - 45.) < 1.5
