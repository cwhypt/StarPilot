"""Stop-light evidence keeps Experimental through short detector gaps (RAV4 drive 0a, t=347.8 s).

The car left a curve with a red light ahead. The curve trigger masked the stop trigger, the detector then dropped
out for ~2 s, Conditional fell back to Chill and cruise accelerated toward the light until the stop trigger came
back too late for a gentle stop.
"""
from dataclasses import replace

from openpilot.starpilot.conditional_mode import ConditionalModePolicy, ManualIntent, ModeChoice, ModeSettings, Reason
from openpilot.starpilot.conditional_mode.policy import CEM_STOP_HOLD_S
from openpilot.starpilot.conditional_mode.tests.test_policy import AUTHORITY, scene

SETTINGS = replace(ModeSettings(), cem_stop=True, cem_curves=True)
DT = 0.05


def step(policy, now, settings=SETTINGS, **kw):
  return policy.step(now, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY, scene(now, speed=10.0, **kw), settings)


def run(policy, start, duration, **kw):
  results = []
  for i in range(round(duration / DT)):
    results.append(step(policy, start + i * DT, **kw))
  return results


def test_stop_evidence_bridges_a_curve_and_a_detector_gap():
  policy = ConditionalModePolicy()
  assert all(r.requested_experimental for r in run(policy, 10.0, 1.0, stop_light_detected=True))
  # curve: the curve trigger wins, stop evidence is still seen
  assert all(r.requested_experimental for r in run(policy, 11.0, 1.0, stop_light_detected=True, curve_detected=True))
  # detector gap after the curve: stays Experimental and reports the stop
  gap = run(policy, 12.0, 2.0)
  assert all(r.requested_experimental and r.reason is Reason.CEM_STOP for r in gap)
  # evidence comes back before the hold runs out
  assert all(r.requested_experimental for r in run(policy, 14.0, 0.5, stop_light_detected=True))


def test_hold_expires_without_new_evidence():
  policy = ConditionalModePolicy()
  run(policy, 10.0, 0.5, stop_light_detected=True)
  last_evidence = 10.0 + 0.5 - DT
  during = run(policy, 10.5, CEM_STOP_HOLD_S - 0.6)
  assert all(r.requested_experimental for r in during)
  after = run(policy, last_evidence + CEM_STOP_HOLD_S + 0.5, 1.0)
  assert not any(r.requested_experimental for r in after)
  assert after[-1].reason is Reason.NO_TRIGGER


def test_hold_cleared_at_standstill_and_not_renewed_on_launch():
  policy = ConditionalModePolicy()
  run(policy, 10.0, 1.0, stop_light_detected=True)
  r = policy.step(11.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY,
                  scene(11.0, speed=0.0, standstill=True, standstill_stop_hold=True, stop_light_detected=True), SETTINGS)
  assert r.requested_experimental
  # green: the stop hold releases and the car launches; a stale detector frame during the launch suppression window
  # must not pull Experimental back for CEM_STOP_HOLD_S
  policy.step(12.0, ModeChoice.CEM, ManualIntent.NONE, AUTHORITY,
              scene(12.0, speed=0.0, standstill=True, standstill_stop_hold=False, model_should_stop=False), SETTINGS)
  step(policy, 12.1)
  step(policy, 12.15, stop_light_detected=True)
  after = run(policy, 12.2 + 0.8, 1.0)
  assert not any(r.requested_experimental for r in after)


def test_disabled_stop_condition_drops_the_hold():
  policy = ConditionalModePolicy()
  run(policy, 10.0, 0.5, stop_light_detected=True)
  r = step(policy, 10.6, settings=replace(SETTINGS, cem_stop=False))
  r = step(policy, 11.6, settings=replace(SETTINGS, cem_stop=False))
  assert not r.requested_experimental
