"""Blind spot during a lane change, and steering that is a turn rather than a nudge (drive 0000001c)."""
from types import SimpleNamespace

import pytest

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.starpilot.lateral.adjacent_lane_evidence import target_line_distance
from openpilot.starpilot.lateral.lane_change_preferences import LaneChangePolicy

LCS = log.LaneChangeState


def carstate(*, left=True, right=False, torque=0., pressed=False, angle=0., lbsm=False, rbsm=False, v=28.):
  return SimpleNamespace(vEgo=v, leftBlinker=left, rightBlinker=right, steeringPressed=pressed, steeringTorque=torque,
                         steeringAngleDeg=angle, leftBlindspot=lbsm, rightBlindspot=rbsm)


def started(direction='left'):
  dh = DesireHelper(LaneChangePolicy())
  left = direction == 'left'
  dh.update(carstate(left=left, right=not left), True, 0.5)
  dh.update(carstate(left=left, right=not left, torque=150. if left else -150., pressed=True), True, 0.5)
  assert dh.lane_change_state == LCS.laneChangeStarting
  return dh, left


def step(dh, n, line, **kw):
  for _ in range(n):
    dh.update(carstate(**kw), True, 0.5, target_line_m=line)


def test_blindspot_before_the_line_aborts_after_debounce():
  dh, _ = started()
  step(dh, 3, 1.3, lbsm=True)  # 0.15 s: debounce not met
  assert dh.lane_change_state == LCS.laneChangeStarting
  step(dh, 1, 1.3, lbsm=True)
  assert dh.lane_change_state == LCS.preLaneChange
  assert dh.bsm_aborted and dh.desire == log.Desire.none


def test_blindspot_flicker_does_not_abort():
  dh, _ = started()
  for _ in range(10):
    step(dh, 2, 1.0, lbsm=True)
    step(dh, 1, 1.0)
  assert dh.lane_change_state == LCS.laneChangeStarting


def test_blindspot_after_crossing_finishes_the_change():
  dh, _ = started()
  step(dh, 5, 0.6)
  step(dh, 1, 0.25)        # center reaches the line
  step(dh, 2, 2.9)         # lines re-index to the new lane
  step(dh, 10, 2.8, lbsm=True)
  assert dh.lane_change_state == LCS.laneChangeStarting
  assert dh.bsm_during_change and not dh.bsm_aborted


def test_unknown_line_position_does_not_abort():
  dh, _ = started()
  step(dh, 10, None, lbsm=True)
  assert dh.lane_change_state == LCS.laneChangeStarting


def test_other_side_blindspot_is_ignored():
  dh, _ = started()
  step(dh, 10, 1.3, rbsm=True)
  assert dh.lane_change_state == LCS.laneChangeStarting


def test_restart_waits_for_clear_blindspot_then_nudge():
  dh, _ = started()
  step(dh, 4, 1.3, lbsm=True)
  assert dh.lane_change_state == LCS.preLaneChange
  step(dh, 5, 1.6, lbsm=True, torque=150., pressed=True)
  assert dh.lane_change_state == LCS.preLaneChange
  step(dh, 1, 1.6, torque=150., pressed=True)
  assert dh.lane_change_state == LCS.laneChangeStarting


def test_abort_with_one_per_signal_allows_a_retry():
  dh = DesireHelper(LaneChangePolicy(one_per_signal=True))
  dh.update(carstate(), True, 0.5)
  dh.update(carstate(torque=150., pressed=True), True, 0.5)
  assert dh.lane_change_state == LCS.laneChangeStarting
  step(dh, 4, 1.3, lbsm=True)
  assert dh.lane_change_state == LCS.preLaneChange
  step(dh, 1, 1.6, torque=150., pressed=True)
  assert dh.lane_change_state == LCS.laneChangeStarting


def test_recorded_0000001c_left_change_would_abort_at_540_7():
  # rt, left-line distance from 0000001c rlog (modelV2 laneLines[1].y[0], negated); left BSM on from 540.5
  rows = [(538.27, 1.79), (538.77, 1.78), (539.27, 1.72), (539.77, 1.58), (540.27, 1.30), (540.52, 1.28),
          (540.77, 1.08), (541.02, 0.91), (541.52, 0.61), (542.02, 0.33), (542.27, 0.25), (542.77, 2.99)]
  dh, _ = started()
  t, aborted_at, i = 538.27, None, 0
  while t < 543.0 and aborted_at is None:
    while i + 1 < len(rows) and rows[i + 1][0] <= t:
      i += 1
    dh.update(carstate(lbsm=t >= 540.5), True, 0.5, target_line_m=rows[i][1])
    if dh.bsm_aborted:
      aborted_at = t
    t += 0.05
  assert aborted_at == pytest.approx(540.67, abs=0.06)


@pytest.mark.parametrize('angle,starts', [(-34., True), (-45., True), (-62., False), (-80., False)])
def test_turning_wheel_is_not_a_nudge(angle, starts):
  # 0000001c rt754.9/759.9/765.0: right signal on an off ramp at -62..-69 deg started lane changes;
  # 00000013 rt157.7 nudge on a bend at -34 deg is a real confirmation
  dh = DesireHelper(LaneChangePolicy())
  dh.update(carstate(left=False, right=True, angle=angle), True, 0.5)
  dh.update(carstate(left=False, right=True, angle=angle, torque=-150., pressed=True), True, 0.5)
  assert (dh.lane_change_state == LCS.laneChangeStarting) == starts


def line(y):
  return SimpleNamespace(x=list(ModelConstants.X_IDXS), y=[y] * ModelConstants.IDX_N)


def test_target_line_distance():
  model = SimpleNamespace(laneLines=[line(-5.5), line(-1.3), line(2.2), line(5.5)], laneLineProbs=[0.9] * 4)
  assert target_line_distance(model, -1) == pytest.approx(1.3)
  assert target_line_distance(model, 1) == pytest.approx(2.2)
  model.laneLineProbs[1] = 0.1
  assert target_line_distance(model, -1) is None
  assert target_line_distance(model, 0) is None
  assert target_line_distance(SimpleNamespace(laneLines=[]), 1) is None
