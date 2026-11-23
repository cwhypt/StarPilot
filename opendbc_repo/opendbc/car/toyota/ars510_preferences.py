"""Automatic supported-vehicle radar selection; never changes ECU or native safety permissions."""

from opendbc.car import structs
from opendbc.car.toyota.values import ARS510_RADAR_CAR, ToyotaFlags


def supported(cp):
  return bool(
    cp.brand == "toyota"
    and cp.carFingerprint in ARS510_RADAR_CAR
    and not cp.passive
    and not cp.dashcamOnly
    and not cp.notCar
    and len(cp.safetyConfigs) == 1
    and cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.toyota
  )


def configure_radar(cp, alpha_long_radar=False):
  """Select the ARS510 ACC-target receiver.

  Stock behaviour: with Alpha Long (DISABLE_RADAR) the radar is treated as absent. `alpha_long_radar` is an explicit
  opt-in (ToyotaRadarMode 1/2, set by hand) that keeps the receiver on, expecting the radar's private bus to keep
  transmitting after its car-bus output is disabled. Without DISABLE_RADAR it changes nothing.
  """
  if not supported(cp):
    return False
  cp.flags &= ~int(ToyotaFlags.ARS510_RADAR | ToyotaFlags.ARS510_FUSED)
  if cp.flags & ToyotaFlags.DISABLE_RADAR and not alpha_long_radar:
    cp.radarUnavailable = True
    return False
  cp.flags |= int(ToyotaFlags.ARS510_RADAR)
  cp.radarUnavailable = False
  return True


def radar_pcs_fcw(cp):
  """True when the radar's own PCS alarm (private bus) must stand in for the silenced car-bus FCW (PCS_HUD)."""
  # flag bits are per-brand: only Toyota's mean ARS510_RADAR / DISABLE_RADAR
  return bool(cp.brand == "toyota" and cp.flags & ToyotaFlags.ARS510_RADAR and cp.flags & ToyotaFlags.DISABLE_RADAR)
