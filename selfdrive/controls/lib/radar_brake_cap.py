"""Brake-only acceleration cap from the ARS510 radar's ACC target (docs/analysis/rav4h_alpha_long_radar 23 §3B, TODO T4).

radard is vision-first: above 4 m/s a car the vision model misses never becomes leadOne, even when the radar's own
ACC target is closing on it. This computes, from that target alone, the deceleration that would stop the closing before a
margin behind it, as an upper bound on the planner's accel: min(a_target, cap) can only brake more, never less.

NOT wired into the planner. It is a pure, stateful function for offline replay first
(tools/longitudinal/replay_radar_brake_cap.py); wiring it in waits on replayed real drives.

Assumes the lead keeps its speed (Mode 1 gives no relative acceleration).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SUMMARY_TRACK_ID_MIN = 64  # Mode 2 summary cars; only the radar's ACC target (< 64) is used


@dataclass(frozen=True)
class RadarCapParams:
  max_lat: float = 1.2          # m, |yRel| of the ACC target (the radar already selects it in our path)
  min_dist: float = 0.75        # m
  max_dist: float = 120.0       # m
  min_cycles: int = 5           # consecutive radar cycles on the same target (~0.3 s at ~16 Hz)
  stale_s: float = 0.5          # no target update for this long resets the tracker
  min_closing: float = 0.5      # m/s; not closing faster than this -> no cap
  t_margin: float = 0.725       # s; stop margin = 50% of the standard 1.45 s follow time ...
  min_margin: float = 4.0       # m; ... but at least this
  min_gap: float = 1.0          # m; floor on the distance left to stop the closing in
  activate: float = -0.3        # m/s^2; a cap above this is not reported (the planner is already fine)
  cap_min: float = -2.5         # m/s^2; harder braking than this is AEB's job, not a planner cap
  min_moving_speed: float = 1.0  # m/s; a target never seen moving faster than this is ignored (stationary clutter)


@dataclass
class CapResult:
  cap: float | None              # accel upper bound (<= activate), or None = no constraint
  reason: str                    # why (for logs/replay)
  track_id: int | None = None
  d_rel: float = math.nan
  v_rel: float = math.nan
  y_rel: float = math.nan
  required: float = math.nan     # unclamped required accel


def required_accel(d_rel: float, closing: float, v_ego: float, p: RadarCapParams) -> float:
  """Constant ego accel (lead at constant speed) that brings the closing speed to 0 at the margin behind the target."""
  margin = max(p.min_margin, p.t_margin * max(v_ego, 0.0))
  gap = d_rel - margin
  if gap <= 0.0:
    return -math.inf  # already inside the margin while closing
  return -(closing ** 2) / (2.0 * max(gap, p.min_gap))


class RadarBrakeCap:
  def __init__(self, params: RadarCapParams | None = None):
    self.p = params or RadarCapParams()
    self.reset()

  def reset(self):
    self.track_id: int | None = None
    self.cycles = 0
    self.last_t: float | None = None
    self.seen_moving = False

  def update(self, t: float, points, v_ego: float, unavailable: bool = False) -> CapResult:
    """t: seconds; points: liveTracks points (trackId, dRel, yRel, vRel); v_ego: m/s; unavailable: liveTracks errors set."""
    p = self.p
    if unavailable:
      self.reset()
      return CapResult(None, "radar unavailable")
    if self.last_t is not None and t - self.last_t > p.stale_s:
      self.reset()

    acc = [pt for pt in points if pt.trackId < SUMMARY_TRACK_ID_MIN and pt.measured]
    if not acc:
      self.reset()
      return CapResult(None, "no ACC target")
    pt = min(acc, key=lambda x: x.dRel)
    tid = int(pt.trackId)
    if tid != self.track_id:
      self.track_id, self.cycles, self.seen_moving = tid, 0, False
    self.cycles += 1
    self.last_t = t
    if v_ego + pt.vRel > p.min_moving_speed:
      self.seen_moving = True

    res = CapResult(None, "", tid, pt.dRel, pt.vRel, pt.yRel)
    closing = -pt.vRel
    if not (math.isfinite(pt.dRel) and math.isfinite(pt.vRel) and math.isfinite(pt.yRel)):
      res.reason = "non-finite target"
    elif abs(pt.yRel) > p.max_lat:
      res.reason = "target not in lane"
    elif not p.min_dist <= pt.dRel <= p.max_dist:
      res.reason = "target out of range"
    elif self.cycles < p.min_cycles:
      res.reason = "target too new"
    elif not self.seen_moving:
      res.reason = "target never seen moving"
    elif closing < p.min_closing:
      res.reason = "not closing"
    else:
      res.required = required_accel(pt.dRel, closing, v_ego, p)
      if res.required > p.activate:
        res.reason = "closing gently"
      else:
        res.cap = max(p.cap_min, res.required)
        res.reason = "cap"
    return res
