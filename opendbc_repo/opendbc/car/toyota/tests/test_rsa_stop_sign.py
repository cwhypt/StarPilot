"""Road Sign Assist stop sign (RSA1.TSGN1_AUX = 5) on the 2025 RAV4 Hybrid, decoded through the real CAN parser."""
from opendbc.can import CANPacker
from opendbc.car import Bus
from opendbc.car.toyota.carstate import RSA_STOP_SIGN_CARS
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, DBC

# raw RSA1 payloads recorded on drive 0000000a (t=66.4 s, at a stop sign) and before/after it
STOP = bytes.fromhex("00a0000000000002")
NONE = bytes.fromhex("0000000000000003")
SPEED_40 = bytes.fromhex("2400280000200004")


def interface(car):
  return CarInterface(CarInterface.get_params(car, {0: {}, 1: {}, 2: {}}, [], False, False, False))


def feed(ci, t_ns, payload):
  ci.update([(t_ns, [(0x489, payload, 2)])])
  return ci.CS.dashboard_stop_sign


def test_recorded_payloads():
  ci = interface(CAR.TOYOTA_RAV4_TSS2_2023)
  assert feed(ci, 1_000_000_000, NONE) == (False, 1_000_000_000)
  assert feed(ci, 2_000_000_000, STOP) == (True, 2_000_000_000)
  # no new RSA1 frame: the last observation and its timestamp are kept (the consumer applies the age limit)
  ci.update([(2_500_000_000, [])])
  assert ci.CS.dashboard_stop_sign == (True, 2_000_000_000)
  assert feed(ci, 3_000_000_000, SPEED_40) == (False, 3_000_000_000)


def test_packer_round_trip():
  packer = CANPacker(DBC[CAR.TOYOTA_RAV4_TSS2_2023][Bus.pt])
  addr, dat, _ = packer.make_can_msg("RSA1", 2, {"TSGN1_AUX": 5})
  assert addr == 0x489 and dat[1] == 0xA0


def test_other_platforms_ignore_the_field():
  assert CAR.TOYOTA_RAV4_TSS2 not in RSA_STOP_SIGN_CARS
  ci = interface(CAR.TOYOTA_RAV4_TSS2)
  assert feed(ci, 1_000_000_000, STOP) is None
