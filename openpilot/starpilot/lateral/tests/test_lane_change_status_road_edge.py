from types import SimpleNamespace

from openpilot.starpilot.lateral.lane_change_status_wire import (Direction, LaneChangeStatus, Phase, alert_wording,
                                                                  decode, encode)


def test_road_edge_phase_round_trips_and_does_not_ask_for_a_nudge():
  status = LaneChangeStatus("session", 3, 77, 10_000_000_000, 1_000_000_000, 1_100_000_000,
                            Phase.ROAD_EDGE_BLOCKED, Direction.RIGHT, True, True)
  decoded = decode(encode(status))
  assert decoded == status
  model = SimpleNamespace(frameId=77, timestampEof=10_000_000_000, meta=SimpleNamespace(laneChangeDirection="right"))
  text = alert_wording(decoded, model, 1_050_000_000, 1_020_000_000)
  assert text == ("Lane Change Blocked", "Road edge or curb on that side")
  assert "confirm" not in text[1].lower()
