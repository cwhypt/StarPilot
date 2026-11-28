"""Same-frame adjacent width for automatic lane changes."""

import bisect
import math
from statistics import median

from openpilot.selfdrive.modeld.constants import ModelConstants

# A real adjacent lane leaves at least most of a lane between its inner line and the
# road edge (median 3.5-7 m on recorded drives). Medians, gore strips and curbs beside
# the lane measured 0.5-1.7 m. 2.5 m sits between the two with margin either side.
ROAD_EDGE_MIN_CLEARANCE_M = 2.5
# While navigation guides toward the signaled side, a newly opened lane (ramp add lane, right-turn pocket)
# reads only 1.3-2.6 m (0000001e rt951-955). Curbs/walls seen in the same guided situations read 0.2-1.4 m
# (00000013 rt343-351, 00000014 rt970-977), so 1.8 m still refuses them.
ROAD_EDGE_GUIDED_MIN_CLEARANCE_M = 1.8


def _points(line):
  xs, ys = list(line.x), list(line.y)
  if (len(xs) != ModelConstants.IDX_N or len(ys) != ModelConstants.IDX_N or
      not all(math.isfinite(value) for value in xs + ys) or
      any(b <= a for a, b in zip(xs, xs[1:], strict=False))):
    return None
  return xs, ys


def _at(points, x):
  xs, ys = points
  if x <= xs[0]:
    return ys[0]
  if x >= xs[-1]:
    return ys[-1]
  index = bisect.bisect_left(xs, x)
  ratio = (x - xs[index - 1]) / (xs[index] - xs[index - 1])
  return ys[index - 1] + ratio * (ys[index] - ys[index - 1])


def adjacent_lane_available(model, direction: int, minimum_width_m: float) -> bool:
  """Compare median adjacent width with the saved minimum."""
  try:
    if not math.isfinite(minimum_width_m) or not 0 <= minimum_width_m <= 4.572 or direction not in (-1, 1):
      return False
    inner, outer, edge = ((1, 0, 0) if direction == -1 else (2, 3, 1))
    if (len(model.laneLines) != 4 or len(model.roadEdges) != 2 or len(model.laneLineProbs) != 4 or
        len(model.laneLineStds) != 4 or len(model.roadEdgeStds) != 2):
      return False
    if (not all(math.isfinite(value) and 0 <= value <= 1 for value in model.laneLineProbs) or
        not all(math.isfinite(value) and value >= 0 for value in list(model.laneLineStds) + list(model.roadEdgeStds))):
      return False
    inner_points = _points(model.laneLines[inner])
    outer_points = _points(model.laneLines[outer])
    edge_points = _points(model.roadEdges[edge])
    if inner_points is None or outer_points is None or edge_points is None:
      return False
    xs, ys = inner_points
    lane_distances = [abs(y - _at(outer_points, x)) for x, y in zip(xs, ys, strict=True)]
    edge_distances = [abs(y - _at(edge_points, x)) for x, y in zip(xs, ys, strict=True)]
    if not all(math.isfinite(value) for value in lane_distances + edge_distances):
      return False
    width = median(lane_distances)
    edge_clearance = median(edge_distances)
    # A road edge inside the adjacent lane means there is no lane there, whatever the
    # saved minimum width (the default minimum of 0.0 m used to let this through).
    if edge_clearance < width or edge_clearance < ROAD_EDGE_MIN_CLEARANCE_M:
      return False
    return width >= minimum_width_m
  except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
    return False


def road_edge_too_close(model, direction: int, minimum_clearance_m: float = ROAD_EDGE_MIN_CLEARANCE_M) -> bool:
  """True when the model sees the road edge too close beyond the lane line on the signaled side.

  Used for every lane change, driver-nudged included: a median, curb or gore area
  there is not a lane. Missing or malformed model data does not block (fails open),
  so this only ever adds a refusal when the model positively sees the edge."""
  try:
    if direction not in (-1, 1) or len(model.laneLines) != 4 or len(model.roadEdges) != 2:
      return False
    inner, edge = (1, 0) if direction == -1 else (2, 1)
    inner_points = _points(model.laneLines[inner])
    edge_points = _points(model.roadEdges[edge])
    if inner_points is None or edge_points is None:
      return False
    xs, ys = inner_points
    distances = [abs(y - _at(edge_points, x)) for x, y in zip(xs, ys, strict=True)]
    if not all(math.isfinite(value) for value in distances):
      return False
    return median(distances) < minimum_clearance_m
  except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
    return False


TARGET_LINE_MIN_PROB = 0.3


def target_line_distance(model, direction: int) -> float | None:
  """Lateral distance (m) from the car's center to the lane line on the lane-change side (-1 left, 1 right).

  Positive while the center has not reached the line. None when the line is unknown or weak, so callers
  must not act on it (fails safe for the blind-spot abort: no abort without a known position)."""
  try:
    if direction not in (-1, 1) or len(model.laneLines) != 4:
      return None
    index = 1 if direction == -1 else 2
    if model.laneLineProbs[index] < TARGET_LINE_MIN_PROB:
      return None
    y = float(model.laneLines[index].y[0])
    distance = -y if direction == -1 else y
    return distance if math.isfinite(distance) else None
  except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
    return None
