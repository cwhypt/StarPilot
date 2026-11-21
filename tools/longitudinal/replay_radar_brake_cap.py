#!/usr/bin/env python3
"""Replay the brake-only radar cap (selfdrive/controls/lib/radar_brake_cap.py) on rlogs, without touching the planner.

For every liveTracks message it runs the cap on the radar's ACC target and compares it with what the planner actually
asked for (longitudinalPlan.aTarget). Where the cap is lower by more than --min-extra it would have braked harder: those
samples are grouped into episodes and classified by what radard/vision had at the time:

  vision missed       leadOne.status was False: the gap this cap is meant to close
  vision other lead   leadOne was another object (different radar track and >25%/5 m away)
  same lead           leadOne was this car, the planner just braked less (tuning difference, not a perception gap)

It also lists closing targets ignored only because they were never seen moving (stationary filter), to decide that rule.

  tools/longitudinal/replay_radar_brake_cap.py <route or rlog> [more ...] [--json] [--param cap_min=-2.0 ...]

Needs a drive with ToyotaRadarMode 1 or 2 (liveTracks carries the ACC target). Use rlogs, not qlogs.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field, fields, replace

from openpilot.selfdrive.controls.lib.radar_brake_cap import RadarBrakeCap, RadarCapParams, required_accel

EPISODE_JOIN_S = 0.5
DRIVER_BRAKE_WINDOW_S = 2.0


@dataclass
class Sample:
  t: float
  cap: float | None
  reason: str
  track_id: int | None
  d_rel: float
  v_rel: float
  v_ego: float
  a_target: float
  enabled: bool
  lead_status: bool
  lead_d: float
  lead_track: int
  required: float


@dataclass
class Episode:
  start: float
  end: float
  kind: str
  engaged: bool
  min_cap: float
  a_target_at_min: float
  max_extra_brake: float
  v_ego: float
  d_rel: float
  v_rel: float
  driver_braked: bool


@dataclass
class Result:
  radar_s: float = 0.0
  engaged_s: float = 0.0
  target_s: float = 0.0
  episodes: list[Episode] = field(default_factory=list)
  stationary: list[Episode] = field(default_factory=list)
  reasons: dict[str, int] = field(default_factory=dict)
  ars510: bool | None = None


def classify(s: Sample) -> str:
  if not s.lead_status:
    return "vision missed"
  same = s.lead_track == s.track_id or abs(s.lead_d - s.d_rel) <= max(5.0, 0.25 * s.d_rel)
  return "same lead" if same else "vision other lead"


def group(samples: list[Sample], brakes: list[tuple[float, bool]], value) -> list[Episode]:
  out: list[Episode] = []
  run: list[Sample] = []

  def flush():
    if not run:
      return
    worst = min(run, key=value)
    kinds = [classify(s) for s in run]
    kind = max(set(kinds), key=kinds.count)
    end = run[-1].t
    braked = any(v and run[0].t <= t <= end + DRIVER_BRAKE_WINDOW_S for t, v in brakes)
    out.append(Episode(run[0].t, end, kind, run[0].enabled, value(worst), worst.a_target, worst.a_target - value(worst),
                       worst.v_ego, worst.d_rel, worst.v_rel, braked))
    run.clear()

  for s in samples:
    if run and s.t - run[-1].t > EPISODE_JOIN_S:
      flush()
    run.append(s)
  flush()
  return out


def replay(msgs, params: RadarCapParams | None = None, min_extra: float = 0.2) -> Result:
  p = params or RadarCapParams()
  cap = RadarBrakeCap(p)
  res = Result()
  v_ego, brake, a_target, enabled = 0.0, False, 0.0, False
  lead = (False, math.nan, -1)
  brakes: list[tuple[float, bool]] = []
  hits: list[Sample] = []
  still: list[Sample] = []
  last_t = None
  t0 = None
  for m in msgs:
    which = m.which()
    t = m.logMonoTime * 1e-9
    if t0 is None:
      t0 = t
    t -= t0
    if which == "carParams":
      res.ars510 = bool(m.carParams.flags & 16384)
    elif which == "carState":
      v_ego, brake = m.carState.vEgo, m.carState.brakePressed
      brakes.append((t, brake))
    elif which == "longitudinalPlan":
      a_target = m.longitudinalPlan.aTarget
    elif which == "selfdriveState":
      enabled = m.selfdriveState.enabled
    elif which == "radarState":
      lo = m.radarState.leadOne
      lead = (lo.status, lo.dRel, lo.radarTrackId)
    elif which == "liveTracks":
      lt = m.liveTracks
      unavailable = not m.valid or any(lt.errors.to_dict().values())
      r = cap.update(t, lt.points, v_ego, unavailable)
      res.reasons[r.reason] = res.reasons.get(r.reason, 0) + 1
      dt = min(t - last_t, EPISODE_JOIN_S) if last_t is not None else 0.0
      last_t = t
      res.radar_s += dt
      res.engaged_s += dt if enabled else 0.0
      res.target_s += dt if r.track_id is not None else 0.0
      s = Sample(t, r.cap, r.reason, r.track_id, r.d_rel, r.v_rel, v_ego, a_target, enabled, *lead, r.required)
      if r.cap is not None and r.cap < a_target - min_extra:
        hits.append(s)
      elif r.reason == "target never seen moving" and -r.v_rel >= p.min_closing:
        s.required = max(p.cap_min, required_accel(r.d_rel, -r.v_rel, v_ego, p))
        if s.required <= p.activate and s.required < a_target - min_extra:
          still.append(s)
  res.episodes = group(hits, brakes, lambda s: s.cap)
  res.stationary = group(still, brakes, lambda s: s.required)
  return res


def per_hour(n: int, seconds: float) -> str:
  return f"{n / (seconds / 3600):.1f}/h" if seconds > 0 else "-"


def report(r: Result) -> str:
  lines = []
  if r.ars510 is False:
    lines.append("WARNING: carParams has no ARS510_RADAR flag (ToyotaRadarMode 0?): liveTracks has no ACC target")
  lines.append(f"radar data {r.radar_s / 60:.1f} min, engaged {r.engaged_s / 60:.1f} min, ACC target present {r.target_s / 60:.1f} min")
  kinds = ["vision missed", "vision other lead", "same lead"]
  engaged = [e for e in r.episodes if e.engaged]
  lines.append(f"cap would brake harder: {len(r.episodes)} episodes ({len(engaged)} engaged, {per_hour(len(engaged), r.engaged_s)} engaged)")
  for k in kinds:
    n = sum(1 for e in r.episodes if e.kind == k)
    lines.append(f"  {k:18} {n}")
  for e in r.episodes:
    lines.append(f"  {e.start:8.1f}s-{e.end:.1f}s {e.kind:18} {'engaged' if e.engaged else 'not engaged':11} " +
                 f"cap {e.min_cap:5.2f} vs plan {e.a_target_at_min:5.2f} (extra {e.max_extra_brake:4.2f}) " +
                 f"v {e.v_ego:4.1f} d {e.d_rel:5.1f} vRel {e.v_rel:5.1f}{'  driver braked' if e.driver_braked else ''}")
  lines.append(f"ignored as never-moving while closing: {len(r.stationary)} episodes")
  for e in r.stationary:
    lines.append(f"  {e.start:8.1f}s-{e.end:.1f}s {e.kind:18} would be {e.min_cap:5.2f} vs plan {e.a_target_at_min:5.2f} " +
                 f"v {e.v_ego:4.1f} d {e.d_rel:5.1f}{'  driver braked' if e.driver_braked else ''}")
  lines.append("reasons: " + ", ".join(f"{k} {v}" for k, v in sorted(r.reasons.items(), key=lambda kv: -kv[1])))
  return "\n".join(lines)


def parse_params(items: list[str]) -> RadarCapParams:
  p = RadarCapParams()
  types = {f.name: f.type for f in fields(RadarCapParams)}
  for item in items:
    key, _, value = item.partition("=")
    if key not in types:
      raise SystemExit(f"unknown param {key}; one of {', '.join(types)}")
    p = replace(p, **{key: int(value) if types[key] in (int, "int") else float(value)})
  return p


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("logs", nargs="+", help="route / segment identifiers or rlog paths")
  parser.add_argument("--json", action="store_true")
  parser.add_argument("--min-extra", type=float, default=0.2, help="m/s^2 the cap must be below aTarget to count")
  parser.add_argument("--param", action="append", default=[], help="override a RadarCapParams field, e.g. cap_min=-2.0")
  args = parser.parse_args()

  from openpilot.tools.lib.logreader import LogReader
  msgs = (m for ident in args.logs for m in LogReader(ident, sort_by_time=True))
  r = replay(msgs, parse_params(args.param), args.min_extra)
  print(json.dumps(asdict(r), indent=2) if args.json else report(r))


if __name__ == "__main__":
  main()
