"""A gas tap at a red light creeps forward instead of releasing the stop (RAV4 drive 8, t=1156 s)."""
from dataclasses import replace

from openpilot.starpilot.conditional_mode import ConditionalModePolicy, ManualIntent, ModeChoice, ModeSettings, Reason
from openpilot.starpilot.conditional_mode.tests.test_policy import AUTHORITY, scene

SETTINGS = replace(ModeSettings(), cem_stop=True)


def step(policy, now, **kw):
  return policy.step(now, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(now, **kw), SETTINGS)


def held_at_light(policy, now=10.0):
  r = step(policy, now, speed=0.0, standstill=True, standstill_stop_hold=True, stop_light_detected=True, model_should_stop=True)
  assert r.requested_experimental and r.reason is Reason.CEM_STOP


def test_gas_tap_at_red_keeps_experimental_until_model_releases():
  policy = ConditionalModePolicy()
  held_at_light(policy)
  # gas tap: the stop detector drops its hold, the model still asks to stop
  r = step(policy, 10.05, speed=0.0, standstill=True, standstill_stop_hold=False, model_should_stop=True)
  assert r.requested_experimental and r.reason is Reason.CEM_STOP
  # creeping forward with the gas, then rolling after it is released: still Experimental
  for i in range(40):
    r = step(policy, 10.1 + i * 0.05, speed=1.5, model_should_stop=True)
    assert r.requested_experimental and r.reason is Reason.CEM_STOP
  # stopped again closer to the line, light still red
  r = step(policy, 12.2, speed=0.0, standstill=True, standstill_stop_hold=False, model_should_stop=True)
  assert r.requested_experimental
  # light turns green: the model stops asking to stop -> ordinary release
  r = step(policy, 12.25, speed=0.0, standstill=True, standstill_stop_hold=False, model_should_stop=False)
  assert not r.requested_experimental


def test_release_with_model_go_is_unchanged():
  policy = ConditionalModePolicy()
  held_at_light(policy)
  r = step(policy, 10.05, speed=0.0, standstill=True, standstill_stop_hold=False, model_should_stop=False)
  assert not r.requested_experimental


def test_stop_sign_keeps_accelerator_means_go():
  policy = ConditionalModePolicy()
  r = step(policy, 10.0, speed=0.0, standstill=True, standstill_stop_hold=True, stop_sign_confirmed=True, model_should_stop=True)
  assert r.requested_experimental
  r = step(policy, 10.05, speed=0.0, standstill=True, standstill_stop_hold=False, model_should_stop=True)
  assert not r.requested_experimental


def test_unknown_model_stop_keeps_old_release():
  policy = ConditionalModePolicy()
  held_at_light(policy)
  r = step(policy, 10.05, speed=0.0, standstill=True, standstill_stop_hold=False, model_should_stop=None)
  assert not r.requested_experimental


def test_disabling_stop_condition_ends_creep():
  policy = ConditionalModePolicy()
  held_at_light(policy)
  step(policy, 10.05, speed=0.0, standstill=True, standstill_stop_hold=False, model_should_stop=True)
  r = policy.step(10.1, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(10.1, speed=1.0, model_should_stop=True),
                  replace(SETTINGS, cem_stop=False))
  assert r.reason is not Reason.CEM_STOP
