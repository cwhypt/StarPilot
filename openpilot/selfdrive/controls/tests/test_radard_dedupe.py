"""ARS510 publishes ~16 Hz while radard runs at 20 Hz: a repeated radar record must not be fed to the Kalman filters twice."""
from openpilot.selfdrive.controls.radard import RadarD
from openpilot.selfdrive.controls.tests.test_adjacent_radar_transport import MONO, Sources


def source(radar_stamp=MONO):
  src = Sources(MONO, points=((17, 30., 0., -2.),))
  src.data['radarTracks'].init('errors')
  src.logMonoTime['radarTracks'] = radar_stamp
  return src


def run(radar, stamps):
  counts = []
  for stamp in stamps:
    src = source(stamp)
    radar.update(src, src['radarTracks'])
    counts.append(radar.tracks[17].cnt)
  return counts


def test_default_feeds_every_radard_cycle():
  assert run(RadarD(), [MONO, MONO, MONO + 1, MONO + 1]) == [1, 2, 3, 4]


def test_dedupe_feeds_each_radar_record_once():
  radar = RadarD(dedupe_radar=True)
  assert run(radar, [MONO, MONO, MONO + 1, MONO + 1, MONO + 1, MONO + 2]) == [1, 1, 2, 2, 2, 3]


def test_dedupe_still_publishes_the_track_on_a_repeated_record():
  # at low speed a radar point straight ahead becomes leadOne without vision (radard's low-speed override)
  radar = RadarD(dedupe_radar=True)
  for _ in range(3):  # the same radar record three radard cycles in a row
    src = Sources(MONO, points=((17, 10., 0., -1.),))
    src.data['radarTracks'].init('errors')
    src.data['carState'].vEgo = 2.
    radar.update(src, src['radarTracks'])
    assert radar.radar_state.leadOne.present and radar.radar_state.leadOne.dRel == 10.
  assert radar.tracks[17].cnt == 1


def test_new_track_is_always_initialised():
  radar = RadarD(dedupe_radar=True)
  run(radar, [MONO])
  src = Sources(MONO, points=((17, 30., 0., -2.), (18, 40., 0., -1.)))
  src.data['radarTracks'].init('errors')
  radar.update(src, src['radarTracks'])  # same record as before, but track 18 has never been seen
  assert radar.tracks[18].cnt == 1 and radar.tracks[17].cnt == 1


def test_ars510_logs_a_brake_cap_episode_only_when_the_cap_fires(monkeypatch):
  from openpilot.selfdrive.controls import radard
  logged = []
  monkeypatch.setattr(radard.cloudlog, "error", logged.append)
  radar = RadarD(dedupe_radar=True)
  for k in range(10):  # ACC target 30 m ahead closing at 8 m/s while ego does 15 m/s
    src = Sources(MONO + k, points=((3, 30. - 0.5 * k, 0., -8.),))
    src.data['radarTracks'].init('errors')
    src.data['radarTracks'].points[0].deprecated.measured = True
    radar.update(src, src['radarTracks'])
  src = Sources(MONO + 20)
  src.data['radarTracks'].init('errors')
  radar.update(src, src['radarTracks'])
  assert len(logged) == 1 and logged[0]["event"] == "radar_brake_cap" and logged[0]["min_cap"] < -0.3


def test_other_cars_never_log_a_brake_cap(monkeypatch):
  from openpilot.selfdrive.controls import radard
  logged = []
  monkeypatch.setattr(radard.cloudlog, "error", logged.append)
  radar = RadarD()
  for k in range(10):
    src = Sources(MONO + k, points=((3, 30. - 0.5 * k, 0., -8.),))
    src.data['radarTracks'].init('errors')
    radar.update(src, src['radarTracks'])
  assert radar.brake_cap is None and logged == []
