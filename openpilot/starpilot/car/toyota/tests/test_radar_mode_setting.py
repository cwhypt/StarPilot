"""The ToyotaRadarMode (ARS510 + Alpha Long) choice in Vehicle Settings reaches the startup preference owner."""
from dataclasses import replace

import pytest
from opendbc.car import gen_empty_fingerprint
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import ARS510_RADAR_CAR, CAR, ToyotaFlags
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import row_change
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


def factory(candidate=CAR.TOYOTA_RAV4_TSS2_2023, alpha_long=True):
  return CarInterface.get_params(candidate, gen_empty_fingerprint(), [], alpha_long, False, False)


def owner_for(params, cp, parked=True):
  return FeatureSettingsOwner(params, lambda _: parked, vehicle_params=lambda: cp, vehicle_fingerprint=lambda: cp.carFingerprint)


def radar_row(owner, parked=True):
  rows = owner.snapshot('vehicle', parked=parked, system_long=True, lateral_context=True, metric=False).rows
  return next((row for row in rows if row.key == 'ToyotaRadarMode'), None)


@pytest.mark.parametrize('candidate', sorted(ARS510_RADAR_CAR))
def test_choice_is_offered_and_reaches_the_next_startup(candidate):
  with OpenpilotPrefix():
    params, cp = Params(), factory(candidate)
    owner = owner_for(params, cp)
    row = radar_row(owner)
    assert row is not None and row.available and row.value == 'Off' and row.default_value == 'Off'
    request = row_change(row)
    assert request is not None and not owner.apply(request), 'applied without confirmation'
    request = replace(request, confirmation=True)
    from openpilot.starpilot.ui.vehicle_bool import VEHICLE_BOOL_KEYS, confirmation_question
    assert request.key in VEHICLE_BOOL_KEYS and 'experimental' in confirmation_question(request)
    assert owner.apply(request)
    assert not owner.apply(request), 'stale request applied twice'
    assert VehicleStartupPreferences.read(params, enabled=True).toyota_radar_alpha_long
    # the next startup really keeps the ARS510 receiver with Alpha Long
    prefs, fresh = VehicleStartupPreferences.read(params, enabled=True), factory(candidate)
    assert fresh.flags & ToyotaFlags.DISABLE_RADAR
    prefs.prepare(fresh)
    prefs.finalize(fresh)
    assert fresh.flags & ToyotaFlags.ARS510_RADAR and not fresh.radarUnavailable

    row = radar_row(owner)
    assert row.value == 'On'
    off = replace(row_change(row), confirmation=True)
    assert off.value == 'Off' and owner.apply(off)
    assert not VehicleStartupPreferences.read(params, enabled=True).toyota_radar_alpha_long


@pytest.mark.parametrize('candidate', [c for c in CAR if c not in ARS510_RADAR_CAR][:12])
def test_other_toyotas_never_show_the_choice(candidate):
  with OpenpilotPrefix():
    assert radar_row(owner_for(Params(), factory(candidate))) is None


def test_onroad_cannot_change_the_choice():
  with OpenpilotPrefix():
    row = radar_row(owner_for(Params(), factory(), parked=False), parked=False)
    assert row is not None and not row.available
