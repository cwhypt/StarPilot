from types import SimpleNamespace

from openpilot.starpilot.rav4 import hotspot_control as hc
from openpilot.starpilot.rav4.hotspot_control import ControlReader, control_from, write_control
from openpilot.starpilot.rav4.hotspots import Action

RAV4 = "TOYOTA_RAV4_TSS2_2023"
S = {"control_rules": ["S_nofix"]}


def test_only_enabled_spots_and_listed_rules_control():
  acts = [Action("H16", "experimental", {"enabled": True}), Action("H16", "speed_cap", {"cap_mph": 20, "enabled": True}),
          Action("H03", "speed_cap", {"cap_mph": 30, "enabled": False}), Action("N_turn", "experimental", {}),
          Action("S_nofix", "speed_cap", {"cap_mph": 15.0}), Action("H16", "alert", {"kind": "hidden_light", "enabled": True})]
  exp, cap, rules = control_from(acts, S)
  assert exp is True and abs(cap - 15 * 0.44704) < 1e-6
  assert rules == ["H16:cap20", "H16:experimental", "S_nofix:cap15"]
  assert control_from([Action("N_turn", "experimental", {}), Action("H03", "speed_cap", {"cap_mph": 30})], S) == (False, None, [])
  assert control_from([Action("N_turn", "experimental", {})], {"control_rules": ["N_turn"]})[0] is True


def test_reader_freshness_fingerprint_and_floor(tmp_path):
  path = str(tmp_path / "c.json")
  r = ControlReader(path)
  assert r.experimental(10**9, RAV4) is None and r.cap_mps(10**9, RAV4) is None   # no file
  write_control(True, 2.0, ["x"], path, now_ns=2 * 10**9)
  r = ControlReader(path)
  assert r.experimental(2 * 10**9 + 10**8, RAV4) is True
  assert r.cap_mps(2 * 10**9 + 10**8, RAV4) == hc.MIN_CAP_MPS                     # floored
  assert r.experimental(2 * 10**9 + 10**8, "HONDA_CIVIC") is None
  assert r.experimental(4 * 10**9, RAV4) is None                                   # stale
  write_control(False, None, [], path, now_ns=5 * 10**9)
  r = ControlReader(path)
  assert r.experimental(5 * 10**9, RAV4) is None and r.cap_mps(5 * 10**9, RAV4) is None
  (tmp_path / "c.json").write_text("{bad")
  assert ControlReader(path).cap_mps(5 * 10**9, RAV4) is None


def test_planner_cap_gating(tmp_path, monkeypatch):
  path = str(tmp_path / "c.json")
  monkeypatch.setattr(hc, "_PLANNER_READER", ControlReader(path))
  cp = SimpleNamespace(carFingerprint=RAV4, openpilotLongitudinalControl=True)
  sm = {"carState": SimpleNamespace(gasPressed=False, brakePressed=False)}
  import time
  write_control(False, 9.0, ["H16:cap20"], path, now_ns=time.monotonic_ns())
  now = time.monotonic_ns()
  assert hc.planner_cap_mps(cp, sm, now, 15.0) == 9.0
  assert hc.planner_cap_mps(cp, sm, now, 8.0) is None                              # never raises the target
  assert hc.planner_cap_mps(cp, {"carState": SimpleNamespace(gasPressed=True, brakePressed=False)}, now, 15.0) is None
  assert hc.planner_cap_mps(SimpleNamespace(carFingerprint=RAV4, openpilotLongitudinalControl=False), sm, now, 15.0) is None
  assert hc.planner_cap_mps(SimpleNamespace(carFingerprint="X", openpilotLongitudinalControl=True), sm, now, 15.0) is None


def test_passed_spots_stop_controlling():
  acts = [Action("H16", "speed_cap", {"cap_mph": 20, "enabled": True, "passed": True}),
          Action("H16", "experimental", {"enabled": True, "passed": True})]
  assert control_from(acts, S) == (False, None, [])
