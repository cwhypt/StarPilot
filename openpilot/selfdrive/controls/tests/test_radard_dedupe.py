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
  radar = RadarD(dedupe_radar=True)
  run(radar, [MONO, MONO])
  src = source(MONO)
  radar.update(src, src['radarTracks'])
  assert radar.radar_state.leadOne.present and radar.radar_state.leadOne.dRel == 30.


def test_new_track_is_always_initialised():
  radar = RadarD(dedupe_radar=True)
  run(radar, [MONO])
  src = Sources(MONO, points=((17, 30., 0., -2.), (18, 40., 0., -1.)))
  src.data['radarTracks'].init('errors')
  radar.update(src, src['radarTracks'])  # same record as before, but track 18 has never been seen
  assert radar.tracks[18].cnt == 1 and radar.tracks[17].cnt == 1
