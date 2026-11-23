#!/usr/bin/env python3
"""Probe the ARS510 radar's REQUIRED_DECEL (0x196 EVENT_STATE) and its event object (0x195) in rlogs.

Option C (docs/analysis/rav4h_alpha_long_radar 23 §3C, TODO T3) would brake on the radar's own required deceleration.
Before implementing anything, this answers from logs only, with no code on the car and no behavior change:

  1. are 0x195 / 0x196 sent on the radar bus at all in this drive (under Alpha Long the radar's car-bus TX is disabled)?
  2. how often is the event active, and what does REQUIRED_DECEL look like (sign, range, duration)?
  3. what was happening at the time: ACC target, vision lead, planner aTarget, the radar-bus PCS alarm, the driver's brake,
     the option-B cap, and (stock-long drives) the car-bus PCS / AEB request (0x283 / 0x344 / 0x411)?

rlogs carry every received CAN frame of every bus, so this works whatever ToyotaRadarMode is; the B-cap column needs
ToyotaRadarMode 1 or 2 (liveTracks).

  openpilot/tools/longitudinal/probe_required_decel.py <route or rlog> [more ...] [--json]
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass, field

from opendbc.can.dbc import DBC, Signal
from opendbc.can.parser import get_raw_value
from openpilot.selfdrive.controls.lib.radar_brake_cap import RadarBrakeCap

ARS510_RADAR, DISABLE_RADAR = 16384, 4
RADAR_BUS, CAR_BUS = 1, 0
EVENT_OBJECT, EVENT_STATE, PCS_STATE = 0x195, 0x196, 0x100
ACC_TARGET_1, ACC_TARGET_2 = 0x235, 0x237
PRE_COLLISION, PRE_COLLISION_2, PCS_HUD = 0x283, 0x344, 0x411
EPISODE_JOIN_S = 0.5
CONTEXT_S = 2.0  # look this far after an episode for the driver's brake / AEB

_r = DBC("toyota_tss2_ars510").addr_to_msg
_pt = DBC("toyota_nodsu_pt_generated").addr_to_msg
SIG = {
  "req": _r[EVENT_STATE].sigs["REQUIRED_DECEL"], "fault": _r[EVENT_STATE].sigs["RADAR_FAULT"],
  "obj_dist": _r[EVENT_OBJECT].sigs["OBJECT_DIST"], "obj_speed": _r[EVENT_OBJECT].sigs["OBJECT_SPEED"],
  "obj_accel": _r[EVENT_OBJECT].sigs["OBJECT_ACCEL"], "travel": _r[EVENT_OBJECT].sigs["EGO_TRAVEL_TO_TTC"],
  "alarm": _r[PCS_STATE].sigs["PCS_ALARM"], "power_up": _r[PCS_STATE].sigs["POWER_UP"],
  "acc_state": _r[ACC_TARGET_1].sigs["STATE"], "rel_speed": _r[ACC_TARGET_1].sigs["REL_SPEED"],
  "long_dist": _r[ACC_TARGET_2].sigs["LONG_DIST"], "present": _r[ACC_TARGET_2].sigs["PRESENT"],
  "pcs_active": _pt[PRE_COLLISION].sigs["PRECOLLISION_ACTIVE"], "pcs_force": _pt[PRE_COLLISION].sigs["FORCE"],
  "pcs2_decel": _pt[PRE_COLLISION_2].sigs["DSS1GDRV"], "fcw": _pt[PCS_HUD].sigs["FCW"],
}


def value(dat: bytes, sig: Signal) -> float:
  raw = get_raw_value(dat, sig)
  if sig.is_signed and raw >= 1 << (sig.size - 1):
    raw -= 1 << sig.size
  return raw * sig.factor + sig.offset


@dataclass
class Sample:
  t: float
  req: float
  req_raw: int
  obj_dist: float
  obj_speed: float
  obj_accel: float
  travel: float
  v_ego: float
  acc_d: float
  acc_v: float
  lead: bool
  a_target: float
  b_cap: float | None
  alarm: bool


@dataclass
class Episode:
  start: float
  end: float
  cycles: int
  req_min: float
  req_max: float
  obj_dist: float
  obj_speed: float
  travel: float
  v_ego: float
  acc_d: float
  acc_v: float
  vision_lead: bool
  a_target: float
  b_cap: float | None
  pcs_alarm: bool
  driver_braked: bool
  car_aeb: bool
  car_fcw: bool


@dataclass
class Result:
  flags: int | None = None
  duration_s: float = 0.0
  moving_s: float = 0.0
  rates: dict[str, float] = field(default_factory=dict)
  state_frames: int = 0
  active_frames: int = 0
  active_moving_frames: int = 0
  moving_frames: int = 0
  fault_frames: int = 0
  req_values: dict[str, int] = field(default_factory=dict)  # raw REQUIRED_DECEL code -> count, active frames
  idle_req_values: dict[str, int] = field(default_factory=dict)
  episodes: list[Episode] = field(default_factory=list)


def probe(msgs) -> Result:
  res = Result()
  counts: dict[str, int] = {}
  first_t = last_t = None
  v_ego, brake, a_target, lead = 0.0, False, math.nan, False
  acc_state = acc_present = 0
  acc_d = acc_v = math.nan
  obj = {"obj_dist": 0.0, "obj_speed": math.nan, "obj_accel": math.nan, "travel": math.nan}
  alarm = False
  b = RadarBrakeCap()
  b_cap: float | None = None
  brakes: list[tuple[float, bool]] = []
  car_aeb: list[tuple[float, bool]] = []
  car_fcw: list[tuple[float, bool]] = []
  hits: list[Sample] = []
  last_carstate_t = None
  for m in msgs:
    which = m.which()
    t = m.logMonoTime * 1e-9
    first_t = t if first_t is None else first_t
    t -= first_t
    last_t = t
    if which == "carParams":
      res.flags = m.carParams.flags
    elif which == "carState":
      v_ego, brake = m.carState.vEgo, m.carState.brakePressed
      brakes.append((t, brake))
      if last_carstate_t is not None and v_ego > 1.0:
        res.moving_s += min(t - last_carstate_t, 0.1)
      last_carstate_t = t
    elif which == "longitudinalPlan":
      a_target = m.longitudinalPlan.aTarget
    elif which == "radarState":
      lead = m.radarState.leadOne.present
    elif which in ("radarTracks", "liveTracks"):  # 7.0 renamed liveTracks to radarTracks
      lt = getattr(m, which)
      b_cap = b.update(t, lt.points, v_ego, not m.valid or any(lt.errors.to_dict().values())).cap
    elif which == "can":
      for f in m.can:
        src, addr, dat = f.src, f.address, bytes(f.dat)
        if src == RADAR_BUS:
          key = {EVENT_OBJECT: "0x195", EVENT_STATE: "0x196", PCS_STATE: "0x100", ACC_TARGET_2: "0x237"}.get(addr)
          if key:
            counts[key] = counts.get(key, 0) + 1
          if addr == EVENT_OBJECT and len(dat) == 8:
            obj = {k: value(dat, SIG[k]) for k in obj}
          elif addr == PCS_STATE and len(dat) >= 2:
            alarm = bool(value(dat, SIG["alarm"])) and value(dat, SIG["power_up"]) == 0
          elif addr == ACC_TARGET_1 and len(dat) == 8:
            acc_state, acc_v = int(value(dat, SIG["acc_state"])), value(dat, SIG["rel_speed"])
          elif addr == ACC_TARGET_2 and len(dat) == 8:
            acc_present, acc_d = int(value(dat, SIG["present"])), value(dat, SIG["long_dist"])
          elif addr == EVENT_STATE and len(dat) >= 2:
            res.state_frames += 1
            req_raw = get_raw_value(dat, SIG["req"])
            req = value(dat, SIG["req"])
            res.fault_frames += int(value(dat, SIG["fault"]) > 0)
            moving = v_ego > 1.0
            res.moving_frames += int(moving)
            active = obj["obj_dist"] > 0 or req_raw != 0
            bucket = res.req_values if active else res.idle_req_values
            bucket[str(req_raw)] = bucket.get(str(req_raw), 0) + 1
            if active:
              res.active_frames += 1
              res.active_moving_frames += int(moving)
              target = acc_state == 5 and acc_present
              hits.append(Sample(t, req, req_raw, obj["obj_dist"], obj["obj_speed"], obj["obj_accel"], obj["travel"], v_ego,
                                 acc_d if target else math.nan, acc_v if target else math.nan, lead, a_target, b_cap, alarm))
        elif src == CAR_BUS:
          if addr == PRE_COLLISION and len(dat) >= 6:
            car_aeb.append((t, bool(value(dat, SIG["pcs_active"])) and value(dat, SIG["pcs_force"]) < 0))
          elif addr == PRE_COLLISION_2 and len(dat) >= 2:
            car_aeb.append((t, value(dat, SIG["pcs2_decel"]) < 0))
          elif addr == PCS_HUD and len(dat) >= 1:
            car_fcw.append((t, bool(value(dat, SIG["fcw"]))))
  res.duration_s = last_t or 0.0
  res.rates = {k: round(n / res.duration_s, 1) if res.duration_s else 0.0 for k, n in sorted(counts.items())}

  def any_true(series, a, z):
    return any(v and a <= t <= z for t, v in series)

  run: list[Sample] = []

  def flush():
    if not run:
      return
    worst = max(run, key=lambda s: abs(s.req))
    s0, s1 = run[0].t, run[-1].t
    caps = [s.b_cap for s in run if s.b_cap is not None]
    res.episodes.append(Episode(s0, s1, len(run), min(s.req for s in run), max(s.req for s in run), worst.obj_dist,
                                worst.obj_speed, worst.travel, worst.v_ego, worst.acc_d, worst.acc_v,
                                any(s.lead for s in run), worst.a_target, min(caps) if caps else None,
                                any(s.alarm for s in run), any_true(brakes, s0, s1 + CONTEXT_S),
                                any_true(car_aeb, s0, s1 + CONTEXT_S), any_true(car_fcw, s0, s1 + CONTEXT_S)))
    run.clear()

  for s in hits:
    if run and s.t - run[-1].t > EPISODE_JOIN_S:
      flush()
    run.append(s)
  flush()
  return res


def verdict(r: Result) -> str:
  if not r.state_frames:
    return "0x196 not on the radar bus in this drive: option C cannot work here (unless this is not the radar bus)"
  if not r.active_frames:
    return "0x196 present, but the event never went active: no REQUIRED_DECEL observed yet (needs a short-TTC moment)"
  return f"REQUIRED_DECEL observed: {len(r.episodes)} episodes -- review each with the video before planning C"


def report(r: Result) -> str:
  f = r.flags
  mode = ("unknown (no carParams)" if f is None else
          f"radar disabled (Alpha Long, no filter)={bool(f & DISABLE_RADAR)}, ToyotaRadarMode 1/2={bool(f & ARS510_RADAR)}")
  pct = 100 * r.active_moving_frames / r.moving_frames if r.moving_frames else 0.0
  lines = [
    f"drive {r.duration_s / 60:.1f} min, moving {r.moving_s / 60:.1f} min; {mode}",
    "radar-bus rates (Hz): " + (", ".join(f"{k} {v}" for k, v in r.rates.items()) or "none"),
    f"0x196 frames {r.state_frames}, fault frames {r.fault_frames}; event active {r.active_frames} "
    + f"({pct:.2f}% of moving cycles; DBC note: ~0.5%)",
    "REQUIRED_DECEL raw codes while active: " + (", ".join(f"{k}:{v}" for k, v in sorted(r.req_values.items(), key=lambda kv: int(kv[0]))) or "-"),
    "REQUIRED_DECEL raw codes while idle: " + (", ".join(f"{k}:{v}" for k, v in sorted(r.idle_req_values.items(), key=lambda kv: int(kv[0]))) or "-"),
    "verdict: " + verdict(r),
  ]
  for e in r.episodes:
    cap = f"{e.b_cap:5.2f}" if e.b_cap is not None else "  -  "
    flags = [n for n, v in (("PCS alarm", e.pcs_alarm), ("driver braked", e.driver_braked), ("car AEB", e.car_aeb),
                            ("car FCW", e.car_fcw), ("vision lead", e.vision_lead)) if v]
    lines.append(f"  {e.start:8.1f}s +{e.end - e.start:.2f}s ({e.cycles} cyc) req {e.req_min:6.2f}..{e.req_max:6.2f} "
                 + f"obj d {e.obj_dist:5.1f} v {e.obj_speed:5.1f} travel {e.travel:5.1f} | ego {e.v_ego:4.1f} "
                 + f"acc d {e.acc_d:5.1f} vRel {e.acc_v:5.1f} | plan {e.a_target:5.2f} B cap {cap} | {', '.join(flags) or '-'}")
  return "\n".join(lines)


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("logs", nargs="+", help="route / segment identifiers or rlog paths (rlogs, not qlogs)")
  parser.add_argument("--json", action="store_true")
  args = parser.parse_args()

  from openpilot.tools.lib.logreader import LogReader
  r = probe(m for ident in args.logs for m in LogReader(ident, sort_by_time=True))
  print(json.dumps(asdict(r), indent=2) if args.json else report(r))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
