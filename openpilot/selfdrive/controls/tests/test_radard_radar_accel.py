"""ARS510: the lead's acceleration comes from the radar's REL_ACCEL (+ ego accel), low-passed, instead of the Kalman derivative."""
import math

import pytest

from openpilot.selfdrive.controls.radard import RADAR_ACCEL_DT, RADAR_ACCEL_RC, RadarD
from openpilot.selfdrive.controls.tests.test_adjacent_radar_transport import MONO, Sources


def source(k, a_rel, a_ego=0.5, v_ego=2.0):
  src = Sources(MONO + k, points=((3, 10., 0., -1.),))
  src.data['radarTracks'].init('errors')
  src.data['radarTracks'].points[0].deprecated.aRel = a_rel
  src.data['carState'].vEgo = v_ego
  src.data['carState'].aEgo = a_ego
  src.recv_frame['carState'] = k + 1
  return src


def feed(radar, k, a_rel, **kw):
  src = source(k, a_rel, **kw)
  radar.update(src, src['radarTracks'])
  return radar.tracks[3].aLeadK


def test_off_keeps_the_kalman_estimate():
  radar = RadarD(dedupe_radar=True)
  for k in range(5):
    a = feed(radar, k, -3.0)
  assert abs(a) < 0.2  # constant vLead: Kalman acceleration stays ~0, aRel ignored


def test_on_uses_radar_relative_plus_ego_accel_low_passed():
  radar = RadarD(dedupe_radar=True, radar_accel=True)
  assert feed(radar, 0, -3.0) == pytest.approx(-2.5)  # first record initialises: -3.0 + 0.5
  alpha = RADAR_ACCEL_DT / (RADAR_ACCEL_RC + RADAR_ACCEL_DT)
  assert feed(radar, 1, -1.5) == pytest.approx(-2.5 + alpha * (1.0), abs=1e-6)
  # a repeated radar record (dedupe) does not step the filter again
  src = source(1, -1.5)
  radar.update(src, src['radarTracks'])
  assert radar.tracks[3].aLeadK == pytest.approx(-2.5 + alpha, abs=1e-6)
  # published lead (low-speed radar lead path) carries it
  assert radar.radar_state.leadOne.present
  assert radar.radar_state.leadOne.aLeadK == pytest.approx(-2.5 + alpha, abs=1e-6)


def test_on_falls_back_to_kalman_without_a_radar_value():
  radar = RadarD(dedupe_radar=True, radar_accel=True)
  for k in range(5):
    a = feed(radar, k, math.nan)
  assert abs(a) < 0.2
