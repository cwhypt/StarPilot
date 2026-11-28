"""Control requests from rav4_hotspotd: hand-off file between the hotspot process and the planner.

Only enabled hotspot actions are published (per-spot "enabled", or a rule listed in "control_rules" of the
hotspot file). Two requests exist, both only ever more conservative:
- experimental: Conditional Experimental Mode treats it like any other CEM trigger (reason cem_hotspot);
- cap_mps: a cruise ceiling applied like the navigation turn ceiling (never above the driver's set speed, not
  while the driver presses gas or brake).
A missing, stale (> 1 s) or malformed file means no request.
"""
from __future__ import annotations

import json
import math
import os
import time

CONTROL_PATH = os.environ.get("RAV4_HOTSPOT_CONTROL", "/dev/shm/rav4_hotspot_control.json")
FINGERPRINT = "TOYOTA_RAV4_TSS2_2023"
MAX_AGE_NS = 1_000_000_000
READ_EVERY_NS = 200_000_000
MIN_CAP_MPS = 4.0   # never ask for less than ~9 mph; slower than this is the driver's job


def write_control(experimental: bool, cap_mps: float | None, rules: list[str], path: str = CONTROL_PATH,
                  now_ns: int | None = None):
  doc = {"mono_ns": int(now_ns if now_ns is not None else time.monotonic_ns()), "experimental": bool(experimental),
         "cap_mps": None if cap_mps is None else float(cap_mps), "rules": list(rules)}
  tmp = f"{path}.tmp"
  with open(tmp, "w", encoding="utf-8") as f:
    json.dump(doc, f)
  os.replace(tmp, path)


class ControlReader:
  def __init__(self, path: str = CONTROL_PATH):
    self.path = path
    self.last_read_ns = 0
    self.doc = None

  def _doc(self, now_ns):
    if now_ns - self.last_read_ns >= READ_EVERY_NS or now_ns < self.last_read_ns:
      self.last_read_ns = now_ns
      try:
        with open(self.path, encoding="utf-8") as f:
          self.doc = json.load(f)
      except (OSError, ValueError):
        self.doc = None
    doc = self.doc
    try:
      if isinstance(doc, dict) and 0 <= now_ns - int(doc.get("mono_ns", 0)) <= MAX_AGE_NS:
        return doc
    except (TypeError, ValueError):
      pass
    return None

  def experimental(self, now_ns: int, fingerprint) -> bool | None:
    """True while an enabled hotspot asks for Experimental; None when there is no fresh request."""
    if fingerprint != FINGERPRINT:
      return None
    doc = self._doc(now_ns)
    return True if doc is not None and doc.get("experimental") is True else None

  def cap_mps(self, now_ns: int, fingerprint) -> float | None:
    if fingerprint != FINGERPRINT:
      return None
    doc = self._doc(now_ns)
    cap = doc.get("cap_mps") if doc is not None else None
    if isinstance(cap, (int, float)) and not isinstance(cap, bool) and math.isfinite(cap) and cap > 0:
      return max(float(cap), MIN_CAP_MPS)
    return None


def control_from(actions, settings) -> tuple[bool, float | None, list[str]]:
  """(experimental, cap_mps, rules) from the enabled subset of the current actions."""
  allowed = set(settings.get("control_rules", ()))
  experimental, cap, rules = False, None, []
  mph = 0.44704
  for a in actions:
    if not (a.rule in allowed or (a.rule.startswith("H") and a.detail.get("enabled") is True)):
      continue
    if a.detail.get("passed") is True:  # the spot is behind the car: nothing left to be careful about
      continue
    if a.action == "experimental":
      experimental = True
      rules.append(f"{a.rule}:experimental")
    elif a.action == "speed_cap":
      value = a.detail.get("cap_mph")
      if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        cap = value * mph if cap is None else min(cap, value * mph)
        rules.append(f"{a.rule}:cap{value:g}")
  return experimental, cap, sorted(set(rules))


_PLANNER_READER = None


def planner_cap_mps(CP, sm, now_ns: int, cruise_mps: float) -> float | None:
  """Cruise ceiling for the longitudinal planner, or None. Never above the current cruise target, never while
  the driver presses gas or brake, only with openpilot longitudinal on the RAV4 2023+."""
  global _PLANNER_READER
  try:
    if getattr(CP, "carFingerprint", None) != FINGERPRINT or not CP.openpilotLongitudinalControl:
      return None
    cs = sm['carState']
    if cs.gasPressed or cs.brakePressed or not math.isfinite(cruise_mps) or cruise_mps <= 0:
      return None
    if _PLANNER_READER is None:
      _PLANNER_READER = ControlReader()
    cap = _PLANNER_READER.cap_mps(int(now_ns), CP.carFingerprint)
    return cap if cap is not None and cap < cruise_mps else None
  except Exception:
    return None
