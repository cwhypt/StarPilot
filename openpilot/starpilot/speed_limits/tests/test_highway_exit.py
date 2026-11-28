"""Navigation-confirmed freeway exits end a held pedal speed; misread lower limits do not."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from openpilot.starpilot.speed_limits.highway_exit import HighwayExit
from openpilot.starpilot.speed_limits.runtime import Runtime
from openpilot.starpilot.speed_limits.runtime_settings import PedalOverride, parse
from openpilot.starpilot.speed_limits.speed_domain import OffsetBand, OffsetSchedule
from openpilot.starpilot.speed_limits.tests import test_runtime_replay as replay

START = 2_000_000_000


def nav(kind, modifier, distance):
  return SimpleNamespace(instruction=SimpleNamespace(maneuverType=kind, maneuverModifier=modifier, distanceMeters=distance))


def run(steps):
  detector = HighwayExit()
  return [i for i, step in enumerate(steps) if detector.update(None if step is None else nav(*step))]


def test_0000001c_exit_through_ramp_forks():
  # rt 477 off ramp ... 725.6 fork left, 734.6 fork right, 750.0 turn right
  steps = [('off ramp', 'right', 6779), ('off ramp', 'right', 23), ('fork', 'left', 207), ('fork', 'left', 12),
           ('fork', 'right', 356), ('fork', 'right', 3), ('turn', 'right', 979), ('fork', 'right', 5), ('turn', 'right', 697)]
  assert run(steps) == [6]


def test_0000001e_reroute_does_not_count_and_real_exit_does():
  # rt 438 off ramp 6603 -> reroute at 3414 m; 772 off ramp 3771 -> 932.6 turn right 415
  steps = [('off ramp', 'right', 6603), ('off ramp', 'right', 3414), None, ('fork', 'right', 5256), ('fork', 'right', 19),
           ('off ramp', 'right', 3771), ('off ramp', 'right', 24), ('turn', 'right', 415)]
  assert run(steps) == [7]


def test_freeway_connector_is_not_an_exit():
  steps = [('off ramp', 'left', 400), ('off ramp', 'left', 20), ('fork', 'left', 300), ('fork', 'left', 10), ('merge', 'left', 2000)]
  assert run(steps) == []


def test_fork_alone_is_not_an_exit():
  assert run([('fork', 'right', 300), ('fork', 'right', 10), ('turn', 'right', 600)]) == []


@pytest.fixture
def drive():
  fixture = replay.RuntimeReplayTests()
  fixture.setUp()
  return fixture.sm, fixture.cp


def tick(runtime, sm, cp, step):
  now = START + step * 50_000_000
  sm.advance(now)
  sm['slcDashboardObservation'].validUntilMonoTime = now + 1_000_000_000
  output = runtime.step(sm, cp, now_ns=now)
  assert not output.result.errors
  return output


class Exit:
  def __init__(self, at):
    self.at, self.calls = at, 0

  def update(self, nav):
    self.calls += 1
    return self.calls == self.at


def held(drive):
  sm, cp = drive
  settings = parse({'SpeedLimitController': True, 'SLCPriority1': 'Dashboard', 'SLCPriority2': 'Off', 'SLCConfirmation': 0})
  runtime = Runtime(replace(settings, offsets=OffsetSchedule((OffsetBand(0., None, 0.),)), pedal_override=PedalOverride.SPEED_REACHED))
  assert tick(runtime, sm, cp, 0).result.ceiling.speed_mps == 25.
  sm['carState'].gasPressed = True
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 26.
  sm['carControl'].longActive = False
  sm['controlsState'].longControlState = 'off'
  tick(runtime, sm, cp, 1)
  tick(runtime, sm, cp, 2)
  sm['carState'].gasPressed = False
  tick(runtime, sm, cp, 3)
  sm['carControl'].longActive = True
  sm['controlsState'].longControlState = 'pid'
  out = tick(runtime, sm, cp, 4)
  assert out.message.slcState.overrideBasis == 'persistent'
  assert out.result.ceiling.speed_mps == pytest.approx(26.)
  return runtime


def test_misread_lower_limit_keeps_the_hold(drive):
  sm, cp = drive
  runtime = held(drive)
  sm['slcDashboardObservation'].speedMps = 15.
  for step in range(5, 10):
    out = tick(runtime, sm, cp, step)
  assert out.message.slcState.acceptedSpeedLimit == 15.
  assert out.result.ceiling.speed_mps == pytest.approx(26.)


def test_navigation_exit_ends_the_hold(drive):
  sm, cp = drive
  runtime = held(drive)
  sm['carState'].vEgo = sm['carState'].vEgoCluster = 24.
  sm['slcDashboardObservation'].speedMps = 15.
  runtime.highway_exit = Exit(at=3)
  for step in range(5, 10):
    out = tick(runtime, sm, cp, step)
  assert not out.message.slcState.driverOverrideActive
  assert out.result.ceiling.speed_mps == pytest.approx(15.)
