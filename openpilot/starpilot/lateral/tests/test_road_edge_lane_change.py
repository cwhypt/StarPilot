"""Lane changes toward a road edge just past the lane line (drive 0000000b: median, curb strike)."""
from types import SimpleNamespace

from openpilot.cereal import log
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.starpilot.lateral.adjacent_lane_evidence import (
  ROAD_EDGE_MIN_CLEARANCE_M, adjacent_lane_available, road_edge_too_close,
)
from openpilot.starpilot.lateral.lane_change_preferences import LaneChangePolicy

LCS = log.LaneChangeState


def line(y):
  return SimpleNamespace(x=list(ModelConstants.X_IDXS), y=[y] * ModelConstants.IDX_N)


def model(left_edge=-6.2, right_edge=6.2):
  return SimpleNamespace(laneLines=[line(-5.5), line(-1.8), line(1.8), line(5.5)],
                         laneLineProbs=[0.95, 0.95, 0.95, 0.95], laneLineStds=[0.1] * 4,
                         roadEdges=[line(left_edge), line(right_edge)], roadEdgeStds=[0.5] * 2)


def test_recorded_clearances():
  # drive b bad starts: 0.92 / 1.35 / 0.79 m. drive d good starts: 3.56 / 5.49 / 7.05 m.
  for clearance in (0.54, 0.79, 0.92, 1.35, 1.72):
    assert road_edge_too_close(model(left_edge=-1.8 - clearance), -1)
    assert road_edge_too_close(model(right_edge=1.8 + clearance), 1)
  for clearance in (3.56, 5.49, 7.05):
    assert not road_edge_too_close(model(left_edge=-1.8 - clearance), -1)
    assert not road_edge_too_close(model(right_edge=1.8 + clearance), 1)


def test_only_the_signaled_side_matters():
  frame = model(left_edge=-2.5)
  assert road_edge_too_close(frame, -1)
  assert not road_edge_too_close(frame, 1)
  assert not road_edge_too_close(frame, 0)


def test_bad_model_data_does_not_block():
  frame = model(left_edge=-2.5)
  frame.roadEdges[0].y[3] = float("nan")
  assert not road_edge_too_close(frame, -1)
  frame = model(left_edge=-2.5)
  frame.roadEdges.pop()
  assert not road_edge_too_close(frame, -1)


def test_auto_evidence_refuses_close_edge_with_zero_minimum_width():
  assert adjacent_lane_available(model(), -1, 0.0)
  assert not adjacent_lane_available(model(left_edge=-1.8 - (ROAD_EDGE_MIN_CLEARANCE_M - 0.1)), -1, 0.0)


def carstate(*, left=True, torque=0., pressed=False):
  return SimpleNamespace(vEgo=15., leftBlinker=left, rightBlinker=False, steeringPressed=pressed,
                         steeringTorque=torque, leftBlindspot=False, rightBlindspot=False)


def run(dh, cs, steps, **kw):
  for _ in range(steps):
    dh.update(cs, True, 0.5, **kw)


def test_driver_nudge_is_held_while_edge_is_close_then_allowed():
  dh = DesireHelper(LaneChangePolicy())
  run(dh, carstate(), 2)
  assert dh.lane_change_state == LCS.preLaneChange
  run(dh, carstate(torque=150., pressed=True), 5, road_edge_blocked=True)
  assert dh.lane_change_state == LCS.preLaneChange
  assert dh.auto_status == "laneUnavailable"
  run(dh, carstate(torque=150., pressed=True), 1, road_edge_blocked=False)
  assert dh.lane_change_state == LCS.laneChangeStarting


def test_auto_lane_change_is_held_while_edge_is_close():
  dh = DesireHelper(LaneChangePolicy(auto_lane_change=True, auto_delay_s=0.0))
  run(dh, carstate(), 1, auto_evidence=True, engaged=True)
  run(dh, carstate(), 30, auto_evidence=True, engaged=True, road_edge_blocked=True)
  assert dh.lane_change_state == LCS.preLaneChange
  run(dh, carstate(), 2, auto_evidence=True, engaged=True)
  assert dh.lane_change_state == LCS.laneChangeStarting


def test_lane_change_waits_until_the_turn_is_finished():
  # 00000013 rt157.7: right signal while still turning right; the driver's turning torque was taken as the nudge
  dh = DesireHelper(LaneChangePolicy())
  cs = carstate(left=False, torque=-160., pressed=True)
  cs.rightBlinker = True
  cs.steeringAngleDeg = -34.
  run(dh, cs, 1)
  run(dh, cs, 10)
  assert dh.lane_change_state == LCS.preLaneChange
  assert dh.auto_status == "laneUnavailable"
  cs.steeringAngleDeg = -8.
  run(dh, cs, 1)
  assert dh.lane_change_state == LCS.laneChangeStarting
