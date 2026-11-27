"""Driver notifications from rav4_hotspotd: hand-off file between the hotspot process and selfdrived.

rav4_hotspotd writes the alert it wants shown (a few seconds after an alert action starts) to ALERT_PATH;
selfdrived reads it (rate limited) and shows a small notification with one prompt chime. Notification only:
nothing here changes steering, gas or brake, and a missing, stale or malformed file shows nothing.
"""
from __future__ import annotations

import json
import os
import time

ALERT_PATH = os.environ.get("RAV4_HOTSPOT_ALERT", "/dev/shm/rav4_hotspot_alert.json")
FINGERPRINT = "TOYOTA_RAV4_TSS2_2023"
MAX_AGE_NS = 1_000_000_000
READ_EVERY_NS = 200_000_000
ALERT_TYPE = "rav4Hotspot/notification"
EVENT_TYPE = "rav4HotspotNotification"

TEXT = {
  "turn_takeover": ("Turn ahead: take over steering", "{side} turn in {dist} m"),
  "hidden_light": ("Hard-to-see light ahead", "Be ready to brake"),
  "manual_turn": ("Manual turn ahead", "Take over steering"),
  "manual_zone": ("Manual section ahead", "Take over steering"),
  "exit": ("Exit ahead", "Signal to change lanes"),
}
PRIORITY = ("turn_takeover", "manual_turn", "manual_zone", "hidden_light", "exit")


def alert_text(kind: str, detail: dict) -> tuple[str, str] | None:
  if kind not in TEXT:
    return None
  top, bottom = TEXT[kind]
  modifier = str(detail.get("modifier") or "")
  try:
    dist = max(int(detail.get("dist_m", 0)), 0)
  except (TypeError, ValueError):
    dist = 0
  if kind == "turn_takeover" and modifier == "uturn":
    return top, f"U-turn in {dist} m"
  return top, bottom.format(side=modifier.capitalize() or "Next", dist=dist)


def write_alert(kind: str | None, detail: dict | None, key: str, path: str = ALERT_PATH, now_ns: int | None = None):
  """Publish (or clear, with kind=None) the alert to show; atomic replace so readers never see half a file."""
  doc = {"mono_ns": int(now_ns if now_ns is not None else time.monotonic_ns()), "kind": kind, "key": key,
         "detail": detail or {}}
  tmp = f"{path}.tmp"
  with open(tmp, "w", encoding="utf-8") as f:
    json.dump(doc, f)
  os.replace(tmp, path)


class HotspotAlert:
  """selfdrived side: returns the Alert to add this frame, or None."""
  def __init__(self, cp, path: str = ALERT_PATH):
    self.supported = getattr(cp, "carFingerprint", None) == FINGERPRINT
    self.path = path
    self.last_read_ns = 0
    self.doc = None
    self.alert = None
    self.alert_key = None
    self.chimed_key = None

  def _read(self, now_ns):
    if now_ns - self.last_read_ns < READ_EVERY_NS:
      return
    self.last_read_ns = now_ns
    try:
      with open(self.path, encoding="utf-8") as f:
        self.doc = json.load(f)
    except (OSError, ValueError):
      self.doc = None

  def update(self, now_ns: int):
    if not self.supported:
      return None
    try:
      self._read(now_ns)
      doc = self.doc
      if not isinstance(doc, dict) or not doc.get("kind") or not 0 <= now_ns - int(doc.get("mono_ns", 0)) <= MAX_AGE_NS:
        self.alert, self.alert_key = None, None
        return None
      text = alert_text(str(doc["kind"]), doc.get("detail") if isinstance(doc.get("detail"), dict) else {})
      if text is None:
        return None
      key = str(doc.get("key", ""))
      if key != self.alert_key or self.alert is None:
        from openpilot.selfdrive.selfdrived.events import Alert, AlertSize, AlertStatus, AudibleAlert, Priority, VisualAlert
        sound = AudibleAlert.none if key == self.chimed_key else AudibleAlert.prompt
        self.alert = Alert(text[0], text[1], AlertStatus.normal, AlertSize.mid, Priority.LOW, VisualAlert.none, sound, 1.)
        self.alert.alert_type, self.alert.event_type = ALERT_TYPE, EVENT_TYPE
        self.alert_key = key
      else:
        self.alert.alert_text_2 = text[1]  # keep the distance current
      return self.alert
    except Exception:  # a notification must never take selfdrived down
      self.alert, self.alert_key = None, None
      return None

  def selected(self, current):
    """After the AlertManager picked: chime once per hotspot alert, then stay silent for it."""
    try:
      if self.alert is not None and current is not None and current.alert_type == ALERT_TYPE:
        from openpilot.selfdrive.selfdrived.events import AudibleAlert
        self.chimed_key = self.alert_key
        self.alert.audible_alert = AudibleAlert.none
    except Exception:
      pass
