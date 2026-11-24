"""Toyota RSA stop sign reaches starpilotCarState.dashboardStopSign only on the observed platform and while fresh."""
import time
from types import SimpleNamespace

from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.starpilot.car.toyota.dashboard import MAX_AGE_NS, compose


def params(car):
  return CarInterface.get_params(car, {0: {}, 1: {}, 2: {}}, [], False, False, False)


def boot_ns():
  return time.clock_gettime_ns(getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC))


STATE = SimpleNamespace(canValid=True, canTimeout=False)


def run(car, sign, age_ns):
  parsed = SimpleNamespace(dashboard_stop_sign=(sign, boot_ns() - age_ns))
  stamp = time.monotonic_ns() + 1_000_000
  return compose(params(car), parsed, STATE, None, car_state_stamp=stamp)


def test_fresh_rsa_stop_sign_is_published():
  msg = run(CAR.TOYOTA_RAV4_TSS2_2023, True, 900_000_000)  # RSA1 is ~1 Hz: a 0.9 s old frame is current
  assert msg.starpilotCarState.dashboardStopSign == 1


def test_stale_or_absent_reads_as_no_sign():
  assert run(CAR.TOYOTA_RAV4_TSS2_2023, True, MAX_AGE_NS + 200_000_000).starpilotCarState.dashboardStopSign == 0
  assert run(CAR.TOYOTA_RAV4_TSS2_2023, False, 100_000_000).starpilotCarState.dashboardStopSign == 0


def test_other_toyota_platforms_publish_nothing():
  assert run(CAR.TOYOTA_RAV4_TSS2, True, 100_000_000) is None
