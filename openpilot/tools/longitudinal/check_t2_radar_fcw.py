#!/usr/bin/env python3
"""Check T2 (ARS510 radar-bus PCS alarm -> FCW alert) on a RAV4 route.

The steps follow docs/analysis/rav4h_alpha_long_radar/24_t2_radar_pcs_fcw_implementation.md section 6:
  0 config, 1 0x100 present on the radar bus, 2 no unexplained alarms, 3 radar-bus alarm == stock FCW (stock-long logs),
  4 alarm -> stockFcw -> fcw event -> dash FCW, 5 no stuck alarm while the radar is silent; plus where every fcw came from.

  openpilot/tools/longitudinal/check_t2_radar_fcw.py <route or rlog> [more ...] [--json]

Exit status 1 if any check FAILs. INFO lines need a person to look at the route at the times listed.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field

from opendbc.can.dbc import DBC
from opendbc.can.parser import get_raw_value

ARS510_RADAR = 16384
DISABLE_RADAR = 4
EXPECTED_RADAR_FW = b"8821F0R03100"

PCS_STATE, EVENT_STATE, PCS_HUD = 0x100, 0x196, 0x411
RADAR_BUS, CAR_BUS = 1, 0
PCS_RATE_HZ = (8.0, 12.0)  # PCS_STATE is 10 Hz
GAP_S = 0.5                # carstate drops a held alarm after 0.5 s without PCS_STATE
CLEAR_MARGIN_S = 0.15      # allowed slack on top of GAP_S / one 10 Hz frame
MATCH_S = 0.3              # car-bus FCW vs radar-bus alarm start/end
STARTUP_S = 10.0

_ars510 = DBC("toyota_tss2_ars510").addr_to_msg
_pt = DBC("toyota_nodsu_pt_generated").addr_to_msg
SIG_PCS_ALARM = _ars510[PCS_STATE].sigs["PCS_ALARM"]
SIG_POWER_UP = _ars510[PCS_STATE].sigs["POWER_UP"]
SIG_RADAR_FAULT = _ars510[EVENT_STATE].sigs["RADAR_FAULT"]
SIG_FCW = _pt[PCS_HUD].sigs["FCW"]


@dataclass
class Capture:
  flags: int | None = None
  op_long: bool | None = None
  radar_fw: list[str] = field(default_factory=list)
  pcs: list[tuple[float, int, int, str]] = field(default_factory=list)   # t, PCS_ALARM, POWER_UP, raw hex
  fault: list[tuple[float, bool]] = field(default_factory=list)          # 0x196 RADAR_FAULT
  car_fcw: list[tuple[float, bool]] = field(default_factory=list)        # 0x411 FCW received on bus 0 (radar, stock long)
  op_fcw: list[tuple[float, bool]] = field(default_factory=list)         # 0x411 FCW openpilot sent on bus 0
  stock_fcw: list[tuple[float, bool]] = field(default_factory=list)      # carState.stockFcw
  fcw_event: list[tuple[float, bool]] = field(default_factory=list)      # onroadEvents has fcw
  radar_unavailable: list[tuple[float, bool]] = field(default_factory=list)
  enabled: list[tuple[float, bool]] = field(default_factory=list)
  model_fcw: list[tuple[float, bool]] = field(default_factory=list)
  planner_fcw: list[tuple[float, bool]] = field(default_factory=list)


@dataclass
class Check:
  step: str
  status: str  # PASS / FAIL / INFO / N/A
  name: str
  detail: str = ""


def collect(msgs) -> Capture:
  cap = Capture()
  for m in msgs:
    which = m.which()
    t = m.logMonoTime * 1e-9
    if which == "carParams":
      cp = m.carParams
      cap.flags, cap.op_long = cp.flags, cp.openpilotLongitudinalControl
      cap.radar_fw = sorted({bytes(fw.fwVersion).decode(errors="replace") for fw in cp.carFw if str(fw.ecu) == "fwdRadar"})
    elif which == "can":
      for f in m.can:
        if f.src == RADAR_BUS and f.address == PCS_STATE:
          dat = bytes(f.dat)
          cap.pcs.append((t, get_raw_value(dat, SIG_PCS_ALARM), get_raw_value(dat, SIG_POWER_UP), dat.hex()))
        elif f.src == RADAR_BUS and f.address == EVENT_STATE and len(f.dat) >= 2:
          cap.fault.append((t, bool(get_raw_value(bytes(f.dat), SIG_RADAR_FAULT))))
        elif f.src == CAR_BUS and f.address == PCS_HUD:
          cap.car_fcw.append((t, bool(get_raw_value(bytes(f.dat), SIG_FCW))))
    elif which == "sendcan":
      for f in m.sendcan:
        if f.src == CAR_BUS and f.address == PCS_HUD:
          cap.op_fcw.append((t, bool(get_raw_value(bytes(f.dat), SIG_FCW))))
    elif which == "carState":
      cap.stock_fcw.append((t, m.carState.stockFcw))
    elif which == "onroadEvents":
      names = {str(e.name) for e in m.onroadEvents}
      cap.fcw_event.append((t, "fcw" in names))
      cap.radar_unavailable.append((t, "radarTempUnavailable" in names))
    elif which == "selfdriveState":
      cap.enabled.append((t, m.selfdriveState.enabled))
    elif which == "modelV2":
      cap.model_fcw.append((t, m.modelV2.meta.hardBrakePredicted))
    elif which == "longitudinalPlan":
      cap.planner_fcw.append((t, m.longitudinalPlan.fcw))
  return cap


def intervals(samples, min_count: int = 1) -> list[tuple[float, float]]:
  """(start, end) of runs of True; end is the first False sample (or the last sample). Runs shorter than min_count samples are dropped."""
  out, start, n, last_t = [], None, 0, None
  for t, v in samples:
    if v and start is None:
      start, n = t, 0
    if v:
      n += 1
    elif start is not None:
      if n >= min_count:
        out.append((start, t))
      start = None
    last_t = t
  if start is not None and n >= min_count:
    out.append((start, last_t))
  return out


def overlaps(a, b, slack=0.0) -> bool:
  return a[0] - slack <= b[1] and b[0] - slack <= a[1]


def total(ivs) -> float:
  return sum(e - s for s, e in ivs)


def fmt(t: float, t0: float) -> str:
  return f"{t - t0:.1f}s"


def analyze(cap: Capture) -> tuple[list[Check], list[dict]]:
  checks: list[Check] = []
  add = lambda *a: checks.append(Check(*a))  # noqa: E731
  times = [s[0] for s in cap.stock_fcw + cap.pcs[:1] + cap.enabled[:1]]
  t0 = min(times) if times else 0.0
  alarm = [(t, bool(a) and p == 0) for t, a, p, _ in cap.pcs]
  alarm_iv = intervals(alarm)
  stock_iv = intervals(cap.stock_fcw)
  t2_active = cap.flags is not None and cap.flags & ARS510_RADAR and cap.flags & DISABLE_RADAR

  # 0 config
  if cap.flags is None:
    add("0", "FAIL", "carParams", "no carParams in these logs (include the route's first segment)")
  else:
    missing = [n for n, b in (("ARS510_RADAR", ARS510_RADAR), ("DISABLE_RADAR", DISABLE_RADAR)) if not cap.flags & b]
    if not missing:
      add("0", "PASS", "flags", "ARS510_RADAR + DISABLE_RADAR: T2 is active")
    else:
      add("0", "INFO" if cap.flags & ARS510_RADAR else "FAIL", "flags",
          f"missing {', '.join(missing)}: T2 is off in this drive"
          + (" (stock long / filter: use it for step 3)" if missing == ["DISABLE_RADAR"] else " (ToyotaRadarMode must be 1 or 2)"))
    fw_ok = any(EXPECTED_RADAR_FW.decode() in v for v in cap.radar_fw)
    add("0", "PASS" if fw_ok else "INFO", "fwdRadar firmware",
        f"{cap.radar_fw or 'not reported'}" + ("" if fw_ok else f"; DBC semantics recorded on {EXPECTED_RADAR_FW.decode()}"))

  # 1 PCS_STATE present on the radar bus
  if not cap.pcs:
    add("1", "FAIL", "0x100 on bus 1", "no PCS_STATE frames: the radar does not send it in this drive, so T2 can never alert"
        + (" (under Alpha Long: T2 and option C are moot on this car)" if t2_active else ""))
  else:
    ts = [p[0] for p in cap.pcs]
    span = ts[-1] - ts[0]
    rate = (len(ts) - 1) / span if span > 0 else 0.0
    gaps = [(a, b - a) for a, b in zip(ts, ts[1:], strict=False) if b - a > GAP_S]
    add("1", "PASS" if PCS_RATE_HZ[0] <= rate <= PCS_RATE_HZ[1] else "FAIL", "0x100 rate",
        f"{rate:.1f} Hz over {span:.0f} s ({len(ts)} frames)")
    add("1", "PASS" if not gaps else "INFO", "0x100 gaps > 0.5 s",
        "none" if not gaps else "; ".join(f"{fmt(a, t0)} for {d:.1f}s" for a, d in gaps[:10]))
    first = cap.pcs[0]
    if first[2] == 0xFF:
      add("1", "PASS", "power-up frame", f"first frame {first[3]} (FF FF) is ignored")
    else:
      add("1", "INFO", "power-up frame", f"first frame {first[3]}: not FF FF (log did not start at power-up, or the radar differs)")
  early = [t for t, v in cap.stock_fcw if v and t - t0 < STARTUP_S]
  add("1", "FAIL" if early else "PASS", f"no alarm in the first {STARTUP_S:.0f} s",
      f"stockFcw at {fmt(early[0], t0)}" if early else "none")

  # 2 alarms over the drive
  if alarm_iv:
    add("2", "INFO", "radar-bus alarms", f"{len(alarm_iv)}: " + "; ".join(f"{fmt(s, t0)}-{fmt(e, t0)}" for s, e in alarm_iv[:20])
        + " -- check the video: a real risk each time? if not, it is a false alarm (set ToyotaRadarMode=0)")
  else:
    add("2", "PASS" if cap.pcs else "N/A", "radar-bus alarms", "none")
  en_iv = intervals(cap.enabled)
  if cap.pcs and en_iv:
    n = sum(1 for t, *_ in cap.pcs if any(s <= t <= e for s, e in en_iv))
    rate = n / total(en_iv) if total(en_iv) > 0 else 0.0
    add("2", "PASS" if PCS_RATE_HZ[0] <= rate <= PCS_RATE_HZ[1] else "FAIL", "0x100 rate while engaged",
        f"{rate:.1f} Hz over {total(en_iv):.0f} s engaged")

  # 3 radar-bus alarm == stock FCW (needs the radar's own 0x411, i.e. stock long or a filter)
  car_iv = intervals(cap.car_fcw)
  if not cap.car_fcw:
    add("3", "N/A", "alarm vs stock FCW", "no 0x411 from the radar on bus 0 (normal under Alpha Long); run on a stock-long drive")
  elif not car_iv and not alarm_iv:
    add("3", "INFO", "alarm vs stock FCW", "no stock FCW and no radar-bus alarm in this drive: nothing to compare yet")
  else:
    unmatched = [iv for iv in car_iv if not any(abs(iv[0] - a[0]) <= MATCH_S and abs(iv[1] - a[1]) <= MATCH_S for a in alarm_iv)]
    extra = [a for a in alarm_iv if not any(overlaps(a, iv, MATCH_S) for iv in car_iv)]
    detail = f"{len(car_iv)} stock FCW, {len(alarm_iv)} radar-bus alarms"
    if unmatched:
      detail += "; stock FCW without a matching alarm at " + ", ".join(fmt(s, t0) for s, _ in unmatched)
    if extra:
      detail += "; alarm without stock FCW at " + ", ".join(fmt(s, t0) for s, _ in extra)
    add("3", "FAIL" if unmatched or extra else "PASS", "alarm vs stock FCW", detail)

  # 4 alarm -> stockFcw -> fcw event -> dash FCW (only meaningful while T2 is active)
  if not t2_active:
    add("4", "N/A", "alert chain", "T2 off in this drive (see step 0)")
  elif not alarm_iv and not stock_iv:
    add("4", "N/A", "alert chain", "no alarm in this drive")
  else:
    ev_iv, op_iv = intervals(cap.fcw_event), intervals(cap.op_fcw)
    problems = []
    for a in alarm_iv:
      s = next((iv for iv in stock_iv if overlaps(a, iv, CLEAR_MARGIN_S)), None)
      if s is None:
        problems.append(f"{fmt(a[0], t0)}: radar alarm but no stockFcw (parser/gating)")
        continue
      if not any(overlaps(s, iv, CLEAR_MARGIN_S) for iv in ev_iv):
        problems.append(f"{fmt(s[0], t0)}: stockFcw but no fcw event (event mapping)")
      if not any(overlaps(s, iv, CLEAR_MARGIN_S) for iv in op_iv):
        problems.append(f"{fmt(s[0], t0)}: no 0x411 FCW=1 sent (dash icon)")
      if s[1] - a[1] > CLEAR_MARGIN_S:
        problems.append(f"{fmt(s[0], t0)}: stockFcw cleared {s[1] - a[1]:.2f}s after the alarm ended")
    for s in stock_iv:
      if not any(overlaps(s, a, CLEAR_MARGIN_S) for a in alarm_iv):
        problems.append(f"{fmt(s[0], t0)}: stockFcw without a radar-bus alarm")
    add("4", "FAIL" if problems else "PASS", "alert chain",
        "; ".join(problems) if problems else f"{len(alarm_iv)} alarm(s): stockFcw, fcw event and dash FCW all followed")

  # 5 no stuck alarm while the radar is silent
  pcs_t = [p[0] for p in cap.pcs]
  stuck, j = [], 0
  for t, v in cap.stock_fcw:
    while j < len(pcs_t) and pcs_t[j] <= t:
      j += 1
    last = pcs_t[j - 1] if j else None
    if v and (last is None or t - last > GAP_S + CLEAR_MARGIN_S):
      stuck.append(t)
  if cap.stock_fcw:
    add("5", "FAIL" if stuck else "PASS", "no alarm held while 0x100 is silent",
        f"held at {fmt(stuck[0], t0)} ({len(stuck)} samples)" if stuck else "ok")
  fault_iv = intervals(cap.fault, min_count=2)
  ru_iv = intervals(cap.radar_unavailable)
  if fault_iv or ru_iv:
    add("5", "INFO", "radar faults", f"RADAR_FAULT held {len(fault_iv)}x, radarTempUnavailable {len(ru_iv)}x"
        + (" at " + ", ".join(fmt(s, t0) for s, _ in (fault_iv or ru_iv)[:10])))

  # where every fcw alert came from
  sources = {"radar (T2)": stock_iv, "model": intervals(cap.model_fcw), "planner": intervals(cap.planner_fcw)}
  episodes = [{"start": round(s - t0, 2), "end": round(e - t0, 2),
               "sources": [k for k, ivs in sources.items() if any(overlaps((s, e), iv, CLEAR_MARGIN_S) for iv in ivs)] or ["unknown"]}
              for s, e in intervals(cap.fcw_event)]
  return checks, episodes


def report(checks: list[Check], episodes: list[dict]) -> str:
  lines = [f"[{c.status:4}] step {c.step}  {c.name}: {c.detail}" for c in checks]
  lines.append(f"fcw alerts: {len(episodes)}")
  lines += [f"  {e['start']}s-{e['end']}s  from {', '.join(e['sources'])}" for e in episodes]
  return "\n".join(lines)


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("logs", nargs="+", help="route / segment identifiers or rlog paths (include segment 0 for carParams)")
  parser.add_argument("--json", action="store_true", help="print JSON instead of text")
  args = parser.parse_args()

  from openpilot.tools.lib.logreader import LogReader
  msgs = [m for ident in args.logs for m in LogReader(ident, sort_by_time=True)]
  checks, episodes = analyze(collect(msgs))
  if args.json:
    print(json.dumps({"checks": [asdict(c) for c in checks], "fcw_alerts": episodes}, indent=2))
  else:
    print(report(checks, episodes))
  return 1 if any(c.status == "FAIL" for c in checks) else 0


if __name__ == "__main__":
  sys.exit(main())
