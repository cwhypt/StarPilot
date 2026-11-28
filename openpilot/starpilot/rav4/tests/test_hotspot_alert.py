from types import SimpleNamespace

from openpilot.starpilot.rav4.hotspot_alert import ALERT_TYPE, HotspotAlert, alert_text, write_alert
from openpilot.starpilot.rav4.hotspotd import AlertPicker
from openpilot.starpilot.rav4.hotspots import Action

RAV4 = SimpleNamespace(carFingerprint="TOYOTA_RAV4_TSS2_2023")
SETTINGS = {"live_alerts": True, "turn_alerts": True}


def test_text():
  assert alert_text("turn_takeover", {"modifier": "left", "dist_m": 82.4}) == ("Turn ahead: take over steering", "Left turn in 82 m")
  assert alert_text("turn_takeover", {"modifier": "uturn", "dist_m": 30})[1] == "U-turn in 30 m"
  assert alert_text("unknown", {}) is None


def test_reader_shows_fresh_alert_once_with_one_chime(tmp_path):
  from openpilot.selfdrive.selfdrived.events import AudibleAlert
  path = str(tmp_path / "a.json")
  r = HotspotAlert(RAV4, path)
  assert r.update(10**9) is None  # no file
  write_alert("turn_takeover", {"modifier": "right", "dist_m": 70}, "N_turn:turn_takeover:1.0", path, now_ns=2 * 10**9)
  a = r.update(2 * 10**9 + 3 * 10**8)
  assert a is not None and a.alert_type == ALERT_TYPE and a.audible_alert == AudibleAlert.prompt
  r.selected(a, 2 * 10**9 + 3 * 10**8)
  assert a.audible_alert == AudibleAlert.prompt         # still sounding when selfdriveState is published
  r.selected(a, 2 * 10**9 + 8 * 10**8)
  assert a.audible_alert == AudibleAlert.prompt
  r.selected(a, 3 * 10**9 + 4 * 10**8)
  assert a.audible_alert == AudibleAlert.none           # muted after 1 s
  assert r.update(2 * 10**9 + 6 * 10**8) is a          # same alert, no new chime
  assert r.update(4 * 10**9) is None                    # stale after 1 s
  write_alert(None, None, "", path, now_ns=5 * 10**9)
  assert r.update(5 * 10**9 + 3 * 10**8) is None        # cleared


def test_reader_other_cars_and_bad_files(tmp_path):
  path = tmp_path / "a.json"
  write_alert("hidden_light", {}, "k", str(path), now_ns=10**9)
  assert HotspotAlert(SimpleNamespace(carFingerprint="HONDA_CIVIC"), str(path)).update(10**9) is None
  path.write_text("{bad")
  assert HotspotAlert(RAV4, str(path)).update(10**9) is None


def test_picker_shows_each_alert_for_a_few_seconds_and_respects_switches():
  p = AlertPicker()
  turn = Action("N_turn", "alert", {"kind": "turn_takeover", "modifier": "left", "dist_m": 80})
  light = Action("H16", "alert", {"kind": "hidden_light"})
  assert p.pick(0, [turn, light], SETTINGS)[0] is turn       # turn first
  assert p.pick(3, [turn, light], SETTINGS)[0] is turn
  assert p.pick(7, [turn, light], SETTINGS) == (None, None)   # both shown long enough
  assert p.pick(8, [], SETTINGS) == (None, None)
  assert p.pick(9, [turn, light], SETTINGS) == (None, None)   # flicker: not again
  assert p.pick(25, [], SETTINGS) == (None, None)
  assert p.pick(26, [turn, light], SETTINGS)[0] is turn       # the turn re-armed after 10 s away
  assert p.pick(27, [light], SETTINGS)[0] is turn             # turn keeps showing through a dropout; spot needs 30 s away
  assert p.pick(33, [light], SETTINGS) == (None, None)
  assert p.pick(70, [light], SETTINGS)[0] is light
  q = AlertPicker()
  assert q.pick(0, [turn], {"live_alerts": True, "turn_alerts": False}) == (None, None)
  assert q.pick(0, [light], {"live_alerts": False, "turn_alerts": True}) == (None, None)
  assert q.pick(0, [Action("N_turn", "experimental", {})], SETTINGS) == (None, None)


def test_alert_manager_shows_it_and_clears_it(tmp_path):
  from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
  from openpilot.selfdrive.selfdrived.events import EmptyAlert
  from openpilot.selfdrive.selfdrived.events import AudibleAlert
  from openpilot.starpilot.rav4.hotspot_alert import EVENT_TYPE
  path = str(tmp_path / "a.json")
  r, am = HotspotAlert(RAV4, path), AlertManager()
  write_alert("turn_takeover", {"modifier": "left", "dist_m": 75}, "k1", path, now_ns=10**9)
  for frame in range(20):
    now = 10**9 + frame * 10**7
    a = r.update(now)
    clear = set() if a is not None else {EVENT_TYPE}
    am.add_many(frame, [a] if a is not None else [])
    am.process_alerts(frame, clear)
    r.selected(am.current_alert, now)
    if frame == 0:
      assert am.current_alert.audible_alert != AudibleAlert.none   # what selfdriveState publishes on the first frame
  assert am.current_alert.alert_text_1 == "Turn ahead: take over steering"
  write_alert(None, None, "", path, now_ns=2 * 10**9)
  for frame in range(20, 400):
    now = 2 * 10**9 + (frame - 20) * 10**7
    a = r.update(now)
    am.add_many(frame, [a] if a is not None else [])
    am.process_alerts(frame, set() if a is not None else {EVENT_TYPE})
  assert am.current_alert == EmptyAlert
