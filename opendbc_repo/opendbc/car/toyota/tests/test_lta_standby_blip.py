from opendbc.car.toyota.carstate import LTA_STANDBY_BLIP_FRAMES, lta_temporary_fault


def run(states, torques):
  frames, out = 0, []
  for state, tq in zip(states, torques, strict=True):
    fault, frames = lta_temporary_fault(state, tq, frames)
    out.append(fault)
  return out


def test_short_standby_while_driver_steers_is_not_a_fault():
  # drive 0000000d rt 185.68: LTA 1 -> 0 for ~4 frames under ~110 driver torque
  assert run([1, 0, 0, 0, 0, 1], [-112, -112, -109, -95, -109, -109]) == [False] * 6


def test_long_standby_while_driver_steers_is_still_a_fault():
  n = LTA_STANDBY_BLIP_FRAMES + 5
  out = run([0] * n, [-150] * n)
  assert not any(out[:LTA_STANDBY_BLIP_FRAMES]) and all(out[LTA_STANDBY_BLIP_FRAMES:])


def test_standby_without_driver_is_a_fault():
  assert run([0, 0], [10, -30]) == [True, True]


def test_driver_press_after_long_standby_does_not_hide_it():
  n = LTA_STANDBY_BLIP_FRAMES + 1
  assert run([0] * n + [0], [0] * n + [150])[-1]


def test_other_temporary_faults_are_never_debounced():
  for state in (9, 11, 21, 25):
    assert run([state], [150]) == [True]


def test_active_states_are_not_faults():
  assert run([1, 5], [0, 150]) == [False, False]
