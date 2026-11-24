"""Optional Toyota Road Sign Assist stop-sign observation; never a longitudinal control grant."""
from opendbc.car.toyota.carstate import RSA_STOP_SIGN_CARS
from openpilot.starpilot.car.mazda.dashboard import write_stop_sign

# RSA1 comes from the front camera about once a second and keeps its value while the sign is shown. Allow one missed
# frame of jitter on top of the period; an older observation reads as no sign.
MAX_AGE_NS = 1_500_000_000


def capable(cp):
  return cp.brand == 'toyota' and cp.carFingerprint in RSA_STOP_SIGN_CARS and not cp.notCar


def compose(cp, parsed, state, message, *, car_state_stamp):
  if not capable(cp):
    return message
  return write_stop_sign(parsed, state, message, car_state_stamp=car_state_stamp, max_age_ns=MAX_AGE_NS)
