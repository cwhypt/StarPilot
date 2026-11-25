import copy
import math

from opendbc.can import CANDefine, CANParser
from opendbc.car.toyota import (
  stock_aol, prius_longitudinal, dsu_bypass, pedal_composite, pedal, legacy_sdsu, advert_filter, legacy_advert_filter, prius_sdsu, prius_advert_filter
)
from opendbc.car.toyota.lexus_is import LexusIsCANParser
from opendbc.car import Bus, DT_CTRL, create_button_events, structs
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.common.filter_simple import FirstOrderFilter
from opendbc.car.interfaces import CarStateBase
from opendbc.car.dashboard_speed_limit import Tracker as LimitTracker, parser_expiry, toyota_sign
from opendbc.can.dbc import DBC as CANDBC
from opendbc.car.toyota.prius_longitudinal import configured as prius_filter_configured
from opendbc.car.toyota.rav4h_filter import Rav4hCANParser, configured as rav4h_filter_configured
from opendbc.car.toyota.values import ToyotaFlags, CAR, DBC, STEER_THRESHOLD, EPS_SCALE, uses_toyota_auto_hold_aeb

# platforms whose Road Sign Assist stop sign field (RSA1.TSGN1_AUX) has been observed on recorded drives
RSA_STOP_SIGN_CARS = frozenset({CAR.TOYOTA_RAV4_TSS2_2023})
from opendbc.car.toyota.ars510_preferences import radar_pcs_fcw
from opendbc.car.carlog import carlog

ButtonType = structs.CarState.ButtonEvent.Type
SteerControlType = structs.CarParams.SteerControlType

# These steering fault definitions seem to be common across LKA (torque) and LTA (angle):
# - high steer rate fault: goes to 21 or 25 for 1 frame, then 9 for 2 seconds
# - lka/lta msg drop out: goes to 9 then 11 for a combined total of 2 seconds, then 3.
#     if using the other control command, goes directly to 3 after 1.5 seconds
# - initializing: LTA can report 0 as long as STEER_TORQUE_SENSOR->STEER_ANGLE_INITIALIZING is 1,
#     and is a catch-all for LKA
TEMP_STEER_FAULTS = (0, 9, 11, 21, 25)
# - lka/lta msg drop out: 3 (recoverable)
# - prolonged high driver torque: 17 (permanent)
PERM_STEER_FAULTS = (3, 17)
# - while the driver overpowers LTA in a tight low-speed turn the EPS can drop LTA_STATE
#   to 0 for a frame or two (seen 40 ms on a RAV4 2025 hybrid with ~110 driver torque).
#   Ride that out instead of reporting a fault: the driver is steering, and with AOL a
#   temporary fault ends in an immediate "TAKE CONTROL" disengagement.
LTA_STANDBY_BLIP_FRAMES = 100  # carState frames (100 Hz): 1 s (owner choice; was 0.25 s); longer stays a fault
LTA_STANDBY_DRIVER_TORQUE = 50  # half of STEER_THRESHOLD: the driver's hands are still on it, not just past override


def lta_temporary_fault(lta_state, driver_torque, standby_frames):
  """(fault, standby_frames) for one carState frame of EPS_STATUS->LTA_STATE."""
  standby_frames = standby_frames + 1 if lta_state == 0 else 0
  blip = (lta_state == 0 and abs(driver_torque) > LTA_STANDBY_DRIVER_TORQUE and
          standby_frames <= LTA_STANDBY_BLIP_FRAMES)
  return lta_state in TEMP_STEER_FAULTS and not blip, standby_frames


ARS510_DBC = "toyota_tss2_ars510"
PCS_STATE_TIMEOUT_FRAMES = 50  # PCS_STATE is 10 Hz; drop a held alarm after 0.5 s without a frame
# carstate runs at 100 Hz. The radar takes several seconds to boot, so only call the bus silent after 15 s without a frame.
RADAR_SILENT_FRAMES = 1500
# Before the first radar frame of a drive allow longer: on a recorded RAV4 drive (Alpha Long) the radar bus stayed quiet
# for over 15 s after card started while the radar was being disabled, then came up normally.
RADAR_NEVER_SEEN_FRAMES = 6000
# REQUIRED_DECEL code -1 (-0.095 m/s^2) is this radar's idle value (99.4 % of a recorded drive); real requests were <= -0.67
REQUIRED_DECEL_IDLE = -0.1
RADAR_BUS_MESSAGES = ("PCS_STATE", "ACC_TARGET_3", "EVENT_OBJECT", "EVENT_STATE")


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)
    self.radar_pcs_alarm = False
    self.lta_standby_frames = 0
    self.radar_pcs_age = PCS_STATE_TIMEOUT_FRAMES
    self.radar_silent_frames = 0
    self.radar_frames_seen = 0
    self.required_decel_event = None  # dict while the radar's short-TTC event is active
    can_define = CANDefine(DBC[CP.carFingerprint][Bus.pt])
    original_prius = prius_sdsu.configured(CP) or prius_advert_filter.configured(CP)
    self.eps_torque_scale = ((CP.safetyConfigs[0].safetyParam & 0xFF) / 100. if prius_advert_filter.configured(CP) else
                             .73 if prius_filter_configured(CP) and not original_prius else EPS_SCALE[CP.carFingerprint] / 100.)
    self.cluster_speed_hyst_gap = CV.KPH_TO_MS / 2.
    self.cluster_min_speed = CV.KPH_TO_MS / 2.

    if CP.flags & ToyotaFlags.SECOC.value:
      self.shifter_values = can_define.dv["GEAR_PACKET_HYBRID"]["GEAR"]
    else:
      self.shifter_values = can_define.dv["GEAR_PACKET"]["GEAR"]

    # On cars with cp.vl["STEER_TORQUE_SENSOR"]["STEER_ANGLE"]
    # the signal is zeroed to where the steering angle is at start.
    # Need to apply an offset as soon as the steering angle measurements are both received
    self.accurate_steer_angle_seen = False
    self.angle_offset = FirstOrderFilter(None, 60.0, DT_CTRL, initialized=False)
    self.dashboard_limit = LimitTracker()
    self.dashboard_stop_sign = None

    self.pedal_ready = False
    self.lkas_button = 0
    self.distance_button = 0

    self.pcm_follow_distance = 0

    self.acc_type = 1
    self.pcm_acc_status = 0
    self.lkas_hud = {}
    self.gvc = 0.0
    self.secoc_synchronization = None
    self.pre_collision_2 = {}
    self.sdsu_state = 0
    self.zss_lateral_active = False
    self.zss_previous_active = False
    self.zss_needs_offset = False
    self.zss_offset = 0.0
    self.zss_last_timestamp = 0

  def update(self, can_parsers) -> structs.CarState:
    cp = can_parsers[Bus.pt]
    cp_cam = can_parsers[Bus.cam]

    if self.CP.flags & ToyotaFlags.AUTO_BRAKE_HOLD and uses_toyota_auto_hold_aeb(self.CP):
      self.pre_collision_2 = copy.copy(cp_cam.vl["PRE_COLLISION_2"])

    ret = structs.CarState()
    bypass = dsu_bypass.configured(self.CP)
    cp_acc = cp_cam if bypass or (self.CP.flags & ToyotaFlags.TSS2) and not (self.CP.flags & ToyotaFlags.RADAR_ACC) else cp

    if not self.CP.flags & ToyotaFlags.SECOC.value:
      self.gvc = cp.vl["VSC1S07"]["GVC"]

    ret.doorOpen = any([cp.vl["BODY_CONTROL_STATE"]["DOOR_OPEN_FL"], cp.vl["BODY_CONTROL_STATE"]["DOOR_OPEN_FR"],
                        cp.vl["BODY_CONTROL_STATE"]["DOOR_OPEN_RL"], cp.vl["BODY_CONTROL_STATE"]["DOOR_OPEN_RR"]])
    ret.seatbeltUnlatched = cp.vl["BODY_CONTROL_STATE"]["SEATBELT_DRIVER_UNLATCHED"] != 0
    ret.parkingBrake = cp.vl["BODY_CONTROL_STATE"]["PARKING_BRAKE"] == 1

    ret.brakePressed = cp.vl["BRAKE_MODULE"]["BRAKE_PRESSED"] != 0
    ret.brakeHoldActive = cp.vl["ESP_CONTROL"]["BRAKE_HOLD_ACTIVE"] == 1

    if self.CP.flags & ToyotaFlags.SECOC.value:
      self.secoc_synchronization = copy.copy(cp.vl["SECOC_SYNCHRONIZATION"])
      ret.gasPressed = cp.vl["GAS_PEDAL"]["GAS_PEDAL_USER"] > 0
      can_gear = int(cp.vl["GEAR_PACKET_HYBRID"]["GEAR"])
    else:
      ret.gasPressed = cp.vl["PCM_CRUISE"]["GAS_RELEASED"] == 0
      can_gear = int(cp.vl["GEAR_PACKET"]["GEAR"])
      if not self.CP.flags & ToyotaFlags.DISABLE_RADAR.value:
        ret.stockAeb = bool(cp_acc.vl["PRE_COLLISION"]["PRECOLLISION_ACTIVE"] and cp_acc.vl["PRE_COLLISION"]["FORCE"] < -1e-5)

    self.parse_wheel_speeds(ret,
      cp.vl["WHEEL_SPEEDS"]["WHEEL_SPEED_FL"],
      cp.vl["WHEEL_SPEEDS"]["WHEEL_SPEED_FR"],
      cp.vl["WHEEL_SPEEDS"]["WHEEL_SPEED_RL"],
      cp.vl["WHEEL_SPEEDS"]["WHEEL_SPEED_RR"],
    )
    ret.vEgoCluster = ret.vEgo * 1.015  # minimum of all the cars

    ret.standstill = abs(ret.vEgoRaw) < 1e-3

    ret.vehicleSensorsInvalid = any(cp.vl["WHEEL_SPEEDS"][f"WHEEL_SPEED_{whl}_FAULT"]
                                    for whl in ("FL", "FR", "RL", "RR"))

    ret.steeringAngleDeg = cp.vl["STEER_ANGLE_SENSOR"]["STEER_ANGLE"] + cp.vl["STEER_ANGLE_SENSOR"]["STEER_FRACTION"]
    ret.steeringRateDeg = cp.vl["STEER_ANGLE_SENSOR"]["STEER_RATE"]
    torque_sensor_angle_deg = cp.vl["STEER_TORQUE_SENSOR"]["STEER_ANGLE"]

    # On some cars, the angle measurement is non-zero while initializing
    if abs(torque_sensor_angle_deg) > 1e-3 and not bool(cp.vl["STEER_TORQUE_SENSOR"]["STEER_ANGLE_INITIALIZING"]):
      self.accurate_steer_angle_seen = True

    if self.accurate_steer_angle_seen:
      # Offset seems to be invalid for large steering angles and high angle rates
      if abs(ret.steeringAngleDeg) < 90 and abs(ret.steeringRateDeg) < 100 and cp.can_valid:
        self.angle_offset.update(torque_sensor_angle_deg - ret.steeringAngleDeg)

      if self.angle_offset.initialized:
        ret.steeringAngleOffsetDeg = self.angle_offset.x
        ret.steeringAngleDeg = torque_sensor_angle_deg - self.angle_offset.x

    ret.gearShifter = self.parse_gear_shifter(self.shifter_values.get(can_gear, None))
    ret.leftBlinker = cp.vl["BLINKERS_STATE"]["TURN_SIGNALS"] == 1
    ret.rightBlinker = cp.vl["BLINKERS_STATE"]["TURN_SIGNALS"] == 2

    ret.steeringTorque = cp.vl["STEER_TORQUE_SENSOR"]["STEER_TORQUE_DRIVER"]
    ret.steeringTorqueEps = cp.vl["STEER_TORQUE_SENSOR"]["STEER_TORQUE_EPS"] * self.eps_torque_scale
    # we could use the override bit from dbc, but it's triggered at too high torque values
    ret.steeringPressed = abs(ret.steeringTorque) > STEER_THRESHOLD

    # Check EPS LKA/LTA fault status
    ret.steerFaultTemporary = cp.vl["EPS_STATUS"]["LKA_STATE"] in TEMP_STEER_FAULTS
    ret.steerFaultPermanent = cp.vl["EPS_STATUS"]["LKA_STATE"] in PERM_STEER_FAULTS

    if advert_filter.configured(self.CP):
      stamp, expiry = parser_expiry(cp, 'EPS_STATUS', 'LKA_STATE')
      expiry = min(expiry, stamp + 400_000_000)
      ret.steerFaultTemporary |= not (cp.eps_valid and stamp > 0 and stamp <= cp._last_update_nanos <= expiry)

    if self.CP.steerControlType == SteerControlType.angle:
      lta_fault, self.lta_standby_frames = lta_temporary_fault(cp.vl["EPS_STATUS"]["LTA_STATE"], ret.steeringTorque,
                                                               self.lta_standby_frames)
      ret.steerFaultTemporary = ret.steerFaultTemporary or lta_fault
      ret.steerFaultPermanent = ret.steerFaultPermanent or cp.vl["EPS_STATUS"]["LTA_STATE"] in PERM_STEER_FAULTS

      # Lane Tracing Assist control is unavailable (EPS_STATUS->LTA_STATE=0) until
      # the more accurate angle sensor signal is initialized
      if not self.accurate_steer_angle_seen:
        ret.vehicleSensorsInvalid = True

    if self.CP.flags & ToyotaFlags.UNSUPPORTED_DSU:
      # TODO: find the bit likely in DSU_CRUISE that describes an ACC fault. one may also exist in CLUTCH
      ret.cruiseState.available = cp.vl["DSU_CRUISE"]["MAIN_ON"] != 0
      if (self.CP.carFingerprint == CAR.LEXUS_IS or
          (legacy_sdsu.configured(self.CP) or legacy_advert_filter.configured(self.CP)) and self.CP.carFingerprint in legacy_sdsu.ALT_MAIN_CARS):
        stamp = cp.ts_nanos["DSU_CRUISE"]["MAIN_ON"]
        ret.cruiseState.available &= bool(cp.main_valid and stamp > 0 and 0 <= cp._last_update_nanos - stamp <= 303_031_000)
      ret.cruiseState.speed = cp.vl["DSU_CRUISE"]["SET_SPEED"] * CV.KPH_TO_MS
      cluster_set_speed = cp.vl["PCM_CRUISE_ALT"]["UI_SET_SPEED"]
    else:
      ret.accFaulted = cp.vl["PCM_CRUISE_2"]["ACC_FAULTED"] != 0
      ret.carFaultedNonCritical = cp.vl["PCM_CRUISE_SM"]["TEMP_ACC_FAULTED"] != 0
      ret.cruiseState.available = cp.vl["PCM_CRUISE_2"]["MAIN_ON"] != 0
      if (pedal.configured(self.CP) and not pedal_composite.configured(self.CP)) or advert_filter.configured(self.CP):
        stamp = cp.ts_nanos["PCM_CRUISE_2"]["MAIN_ON"]
        ret.cruiseState.available &= bool(cp.main_valid and stamp > 0 and 0 <= cp._last_update_nanos - stamp <= 303_031_000)
      ret.cruiseState.speed = cp.vl["PCM_CRUISE_2"]["SET_SPEED"] * CV.KPH_TO_MS
      cluster_set_speed = cp.vl["PCM_CRUISE_SM"]["UI_SET_SPEED"]

    if bypass:
      main = 'DSU_CRUISE' if self.CP.carFingerprint in dsu_bypass.ALT_MAIN_CARS else 'PCM_CRUISE_2'
      stamp = cp.ts_nanos[main]['MAIN_ON']
      ret.cruiseState.available &= bool(cp.main_valid and stamp > 0 and 0 <= cp._last_update_nanos - stamp <= 303_031_000)
      stamp = cp.ts_nanos['EPS_STATUS']['LKA_STATE']
      ret.steerFaultTemporary |= not (cp.eps_valid and stamp > 0 and 0 <= cp._last_update_nanos - stamp <= 400_000_000)
      stamp = cp_cam.ts_nanos['ACC_CONTROL']['ACC_TYPE']
      ret.accFaulted |= not (cp_cam.acc_valid and stamp > 0 and 0 <= cp_cam._last_update_nanos - stamp <= 303_031_000)
      ret.accFaulted |= bool(cp_acc.vl['ACC_CONTROL']['ACC_MALFUNCTION'] or cp_acc.vl['ACC_CONTROL']['RADAR_DIRTY'] or cp.ambiguous or cp_cam.ambiguous)
      self.acc_type = cp_acc.vl['ACC_CONTROL']['ACC_TYPE']
      ret.stockFcw = bool(cp_acc.vl['PCS_HUD']['FCW'])

    # UI_SET_SPEED is always non-zero when main is on, hide until first enable
    is_metric = cp.vl["BODY_CONTROL_STATE_2"]["UNITS"] in (1, 2)
    if ret.cruiseState.speed != 0:
      conversion_factor = CV.KPH_TO_MS if is_metric else CV.MPH_TO_MS
      ret.cruiseState.speedCluster = cluster_set_speed * conversion_factor

    if self.CP.flags & ToyotaFlags.TSS2 and not self.CP.flags & ToyotaFlags.DISABLE_RADAR.value:
      # The radar filter intercepts this message. Preserve stock PCS/AEB
      # observations without making intercepted ACC_CONTROL a required input.
      if not self.CP.flags & ToyotaFlags.RADAR_CAN_FILTER and not rav4h_filter_configured(self.CP):
        self.acc_type = cp_acc.vl["ACC_CONTROL"]["ACC_TYPE"]
      ret.stockFcw = bool(cp_acc.vl["PCS_HUD"]["FCW"])

    if Bus.radar in can_parsers:
      cp_radar = can_parsers[Bus.radar]
      ret.stockFcw = ret.stockFcw or self.update_radar_pcs_alarm(cp_radar)
      ret.radarBusSilent = self.update_radar_bus_silence(cp_radar)
      self.update_required_decel_log(cp_radar, ret.vEgo)

    # some TSS2 cars have low speed lockout permanently set, so ignore on those cars
    # these cars are identified by an ACC_TYPE value of 2.
    # TODO: it is possible to avoid the lockout and gain stop and go if you
    # send your own ACC_CONTROL msg on startup with ACC_TYPE set to 1
    if (not (self.CP.flags & ToyotaFlags.TSS2) and not (self.CP.flags & ToyotaFlags.UNSUPPORTED_DSU)) or \
       (self.CP.flags & ToyotaFlags.TSS2 and self.acc_type == 1):
      if self.CP.openpilotLongitudinalControl:
        ret.accFaulted = ret.accFaulted or cp.vl["PCM_CRUISE_2"]["LOW_SPEED_LOCKOUT"] == 2

    pcm_acc_status = cp.vl["PCM_CRUISE"]["CRUISE_STATE"]
    previous_pcm_acc_status = self.pcm_acc_status
    self.pcm_acc_status = pcm_acc_status
    ret.cruiseState.standstill = pcm_acc_status == 7
    ret.cruiseState.enabled = bool(cp.vl["PCM_CRUISE"]["CRUISE_ACTIVE"])
    ret.cruiseState.nonAdaptive = pcm_acc_status in (1, 2, 3, 4, 5, 6)

    ret.genericToggle = bool(cp.vl["LIGHT_STALK"]["AUTO_HIGH_BEAM"])
    ret.espDisabled = cp.vl["ESP_CONTROL"]["TC_DISABLED"] != 0

    if self.CP.flags & ToyotaFlags.HAS_BSM:
      ret.leftBlindspot = (cp.vl["BSM"]["L_ADJACENT"] == 1) or (cp.vl["BSM"]["L_APPROACHING"] == 1)
      ret.rightBlindspot = (cp.vl["BSM"]["R_ADJACENT"] == 1) or (cp.vl["BSM"]["R_APPROACHING"] == 1)

    if self.CP.carFingerprint != CAR.TOYOTA_PRIUS_V:
      self.lkas_hud = copy.copy(cp_cam.vl["LKAS_HUD"])

    if not (self.CP.flags & ToyotaFlags.UNSUPPORTED_DSU):
      self.pcm_follow_distance = cp.vl["PCM_CRUISE_2"]["PCM_FOLLOW_DISTANCE"]

    # PCM exposes the actual +/- stalk state even when it owns selected speed.
    # Keep these edges independent of SET_SPEED: the update can arrive on release.
    buttonEvents = create_button_events(int(pcm_acc_status) if pcm_acc_status in (9, 10) else 0,
                                       int(previous_pcm_acc_status) if previous_pcm_acc_status in (9, 10) else 0,
                                       {9: ButtonType.accelCruise, 10: ButtonType.decelCruise})
    if self.CP.carFingerprint != CAR.TOYOTA_CAMRY and (
        self.CP.flags & (ToyotaFlags.TSS2 | ToyotaFlags.LONG_FILTER | ToyotaFlags.FILTER_BUTTONS) or
        self.CP.carFingerprint == CAR.TOYOTA_PRIUS_RETROFIT or
        self.CP.flags & ToyotaFlags.RADAR_CAN_FILTER and legacy_advert_filter.configured(self.CP)):
      # lkas button is wired to the camera
      prev_lkas_button = self.lkas_button
      self.lkas_button = cp_cam.vl["LKAS_HUD"]["LDA_ON_MESSAGE"]

      # Cycles between 1 and 2 when pressing the button, then rests back at 0 after ~3s
      if self.lkas_button != 0 and self.lkas_button != prev_lkas_button:
        buttonEvents.extend(create_button_events(1, 0, {1: ButtonType.lkas}) +
                            create_button_events(0, 1, {1: ButtonType.lkas}))

      if (self.CP.flags & ToyotaFlags.TSS2 and not advert_filter.configured(self.CP) and
          not (self.CP.flags & (ToyotaFlags.RADAR_ACC | ToyotaFlags.SECOC | ToyotaFlags.LONG_FILTER))):
        # distance button is wired to the ACC module (camera or radar)
        prev_distance_button = self.distance_button
        self.distance_button = cp_acc.vl["ACC_CONTROL"]["DISTANCE"]

        buttonEvents += create_button_events(self.distance_button, prev_distance_button, {1: ButtonType.gapAdjustCruise})

    if (self.CP.carFingerprint in (CAR.TOYOTA_PRIUS, CAR.TOYOTA_PRIUS_RETROFIT) or
        self.CP.flags & (ToyotaFlags.LONG_FILTER | ToyotaFlags.FILTER_BUTTONS) or advert_filter.configured(self.CP)):
      prev_distance_button = self.distance_button
      if self.CP.flags & ToyotaFlags.FILTER_BUTTONS or advert_filter.configured(self.CP) or prius_advert_filter.configured(self.CP):
        fresh = cp.advert_button_ns > 0 and 0 <= cp._last_update_nanos - cp.advert_button_ns <= 200_000_000
        self.distance_button = cp.advert_distance_button if fresh else 0
      else:
        self.distance_button = (cp.vl["SDSU"]["FD_BUTTON"] if self.CP.flags & ToyotaFlags.LONG_FILTER
                                else cp_acc.vl["ACC_CONTROL"]["DISTANCE"])
      buttonEvents += create_button_events(self.distance_button, prev_distance_button, {1: ButtonType.gapAdjustCruise})

    if self.CP.carFingerprint == CAR.TOYOTA_SIENNA_4TH_GEN:
      prev_distance_button = self.distance_button
      self.distance_button = cp.vl["PCM_CRUISE_4"]["DISTANCE"]
      buttonEvents += create_button_events(self.distance_button, prev_distance_button, {1: ButtonType.gapAdjustCruise})

    if self.CP.flags & ToyotaFlags.ZSS:
      # ZSS support - credit to Erich! Align on each real lateral engagement.
      # A repeated parser value is not a new sensor sample; fall back to stock.
      active = self.zss_lateral_active
      if active and not self.zss_previous_active:
        self.zss_needs_offset = True
      self.zss_previous_active = active
      stamp = cp.ts_nanos.get("SECONDARY_STEER_ANGLE", {}).get("ZORRO_STEER", 0)
      if cp.can_valid and stamp > self.zss_last_timestamp:
        self.zss_last_timestamp = stamp
        zorro = cp.vl["SECONDARY_STEER_ANGLE"]["ZORRO_STEER"]
        if math.isfinite(zorro):
          if self.zss_needs_offset:
            if abs(ret.steeringAngleDeg) > 1e-3 and abs(zorro) > 1e-3:
              self.zss_offset = zorro - ret.steeringAngleDeg
              self.zss_needs_offset = False
          elif abs(ret.steeringAngleDeg - (zorro - self.zss_offset)) < 4.0:
            ret.steeringAngleDeg = zorro - self.zss_offset

    original_prius_sdsu = prius_sdsu.configured(self.CP) or prius_longitudinal.eps73_status_configured(self.CP)
    if rav4h_filter_configured(self.CP) or original_prius_sdsu:
      self.sdsu_state = int(cp.vl["SDSU"]["STATE"])
      source_ns, valid_until_ns = parser_expiry(cp, "SDSU", "STATE")
      # The shared parser debounces canValid. Withdraw this command owner at
      # the actual source expiry, before native health rejects another request.
      sdsu_fresh = source_ns > 0 and source_ns <= cp._last_update_nanos <= valid_until_ns
      ret.accFaulted = ret.accFaulted or not sdsu_fresh or self.sdsu_state not in (0, 7)
      ret.stockAeb = ret.stockAeb or self.sdsu_state == 7
    if original_prius_sdsu or prius_advert_filter.configured(self.CP):
      main_current = cp.main_valid and prius_sdsu.source_current(cp, 'PCM_CRUISE_2', 'MAIN_ON')
      eps_current = cp.eps_valid and prius_sdsu.source_current(cp, 'EPS_STATUS', 'LKA_STATE')
      ret.cruiseState.available = ret.cruiseState.available and main_current and not cp.hardware_ambiguous
      ret.steerFaultTemporary = ret.steerFaultTemporary or not eps_current or cp.hardware_ambiguous
      ret.accFaulted = ret.accFaulted or cp.hardware_ambiguous

    if stock_aol.configured(self.CP):
      main = 'DSU_CRUISE' if self.CP.safetyConfigs[0].safetyParam in stock_aol.ALT_MAIN_WORDS else 'PCM_CRUISE_2'
      main_stamp = cp.ts_nanos[main]['MAIN_ON']
      eps_stamp = cp.ts_nanos['EPS_STATUS']['LKA_STATE']
      ret.cruiseState.available &= bool(cp.main_valid and main_stamp > 0 and
                                       0 <= cp._last_update_nanos - main_stamp <= 303_031_000)
      ret.steerFaultTemporary |= not (cp.eps_valid and eps_stamp > 0 and
                                     0 <= cp._last_update_nanos - eps_stamp <= 400_000_000)

    if pedal.configured(self.CP):
      cp_pedal = can_parsers[Bus.alt] if pedal_composite.configured(self.CP) else cp
      stamp = cp_pedal.ts_nanos.get("GAS_SENSOR", {}).get("STATE", 0)
      self.pedal_ready = bool(cp_pedal.pedal_valid and stamp > 0 and 0 <= cp_pedal._last_update_nanos - stamp <= 200_000_000)
      pedal_gas = (cp_pedal.vl['GAS_SENSOR']['INTERCEPTOR_GAS'] + cp_pedal.vl['GAS_SENSOR']['INTERCEPTOR_GAS2']) / 2 > 805
      ret.gasPressed = (ret.gasPressed or pedal_gas) if pedal_composite.configured(self.CP) else pedal_gas
      ret.accFaulted = ret.accFaulted or not self.pedal_ready
    ret.buttonEvents = buttonEvents
    timestamp, expiry = parser_expiry(cp_cam, "RSA1", "TSGN1")
    if timestamp > self.dashboard_limit.observation.observed_ns:
      sign = int(cp_cam.vl["RSA1"]["TSGN1"])
      speed = int(cp_cam.vl["RSA1"]["SPDVAL1"])
      status, value_mps = toyota_sign(sign, speed)
      self.dashboard_limit.update(timestamp, status, value_mps, valid_until_ns=expiry)
    # Road Sign Assist stop sign (undocumented TSGN1_AUX = 5, see the DBC). Optional observation for StarPilot's stop
    # handling, never a control grant; only on the platform it was observed on. RSA1 is sent about once a second.
    self.dashboard_stop_sign = None
    if self.CP.carFingerprint in RSA_STOP_SIGN_CARS:
      stamp = cp_cam.ts_nanos.get("RSA1", {}).get("TSGN1_AUX", 0)
      if stamp > 0:
        self.dashboard_stop_sign = (int(cp_cam.vl["RSA1"]["TSGN1"]) == 0 and int(cp_cam.vl["RSA1"]["TSGN1_AUX"]) == 5, stamp)
    return ret

  def update_radar_bus_silence(self, cp_radar) -> bool:
    """True after RADAR_SILENT_FRAMES without any radar-bus frame: the ARS510 receiver and T2 then have nothing to work with."""
    limit = RADAR_SILENT_FRAMES if self.radar_frames_seen else RADAR_NEVER_SEEN_FRAMES
    was_silent = self.radar_silent_frames >= limit
    if any(len(cp_radar.vl_all[msg][sig]) for msg, sig in zip(RADAR_BUS_MESSAGES, ("PCS_ALARM", "TIMESTAMP", "OBJECT_DIST", "REQUIRED_DECEL"),
                                                                strict=True)):
      self.radar_silent_frames = 0
      self.radar_frames_seen += 1
    else:
      self.radar_silent_frames = min(self.radar_silent_frames + 1, RADAR_NEVER_SEEN_FRAMES)
    limit = RADAR_SILENT_FRAMES if self.radar_frames_seen else RADAR_NEVER_SEEN_FRAMES
    silent = self.radar_silent_frames >= limit
    if silent != was_silent:
      # error level reaches errorLogMessage, which is kept in qlogs (uploaded automatically)
      carlog.error({"event": "ars510_radar_bus", "silent": silent, "frames_seen_before": self.radar_frames_seen})
    return silent

  def update_required_decel_log(self, cp_radar, v_ego: float) -> None:
    """Log-only (TODO T3): one summary line per radar short-TTC event, so qlogs show what REQUIRED_DECEL would have asked for."""
    dists = cp_radar.vl_all["EVENT_OBJECT"]["OBJECT_DIST"]
    reqs = cp_radar.vl_all["EVENT_STATE"]["REQUIRED_DECEL"]
    if not dists and not reqs:
      ev = self.required_decel_event
      if ev is not None:
        ev["stale"] += 1
        if ev["stale"] > 50:  # 0.5 s with no 0x195/0x196 frame: close it
          self._close_required_decel_event()
      return
    # OBJECT_DIST is filled continuously under Alpha Long (the event object follows the ACC car), so only a real
    # REQUIRED_DECEL value below the idle code marks an event
    active = any(r < REQUIRED_DECEL_IDLE for r in reqs)
    ev = self.required_decel_event
    if active:
      if ev is None:
        ev = self.required_decel_event = {"frames": 0, "stale": 0, "v_ego_start": round(v_ego, 2), "min_required_decel": 0.0,
                                          "min_object_dist": float("inf")}
      ev["frames"] += 1
      ev["stale"] = 0
      ev["min_required_decel"] = min([ev["min_required_decel"], *reqs])
      ev["min_object_dist"] = min([ev["min_object_dist"], *(d for d in dists if d > 0)])
    elif ev is not None:
      self._close_required_decel_event()

  def _close_required_decel_event(self) -> None:
    ev, self.required_decel_event = self.required_decel_event, None
    carlog.error({"event": "ars510_required_decel", "duration_s": round(ev["frames"] / 100, 2), "v_ego_start": ev["v_ego_start"],
                  "min_required_decel": round(ev["min_required_decel"], 3),
                  "min_object_dist": round(ev["min_object_dist"], 2) if ev["min_object_dist"] != float("inf") else None})

  def update_radar_pcs_alarm(self, cp_radar) -> bool:
    alarms = cp_radar.vl_all["PCS_STATE"]["PCS_ALARM"]
    power_up = cp_radar.vl_all["PCS_STATE"]["POWER_UP"]
    if alarms:
      self.radar_pcs_age = 0
      self.radar_pcs_alarm = any(a and not p for a, p in zip(alarms, power_up, strict=True))
    else:
      self.radar_pcs_age = min(self.radar_pcs_age + 1, PCS_STATE_TIMEOUT_FRAMES)
      if self.radar_pcs_age >= PCS_STATE_TIMEOUT_FRAMES:
        self.radar_pcs_alarm = False
    return self.radar_pcs_alarm

  @staticmethod
  def get_can_parsers(CP):
    if pedal_composite.configured(CP):
      parent = CarState.get_can_parsers(pedal_composite.parent_cp(CP))
      for bus in parent:
        parent[bus] = pedal_composite.ParentPedalParser(parent[bus], mirrored_pedal=True)
      parent[Bus.alt] = pedal.PedalCANParser(DBC[CP.carFingerprint][Bus.pt], [('GAS_SENSOR', 50)], 0)
      return parent
    if dsu_bypass.configured(CP):
      alternate = CP.carFingerprint in dsu_bypass.ALT_MAIN_CARS
      main = 'DSU_CRUISE' if alternate else 'PCM_CRUISE_2'
      kwargs = {'eps_length': 8 if CP.carFingerprint in dsu_bypass.EPS8_CARS or CP.carFingerprint == CAR.TOYOTA_PRIUS else 5,
                'alternate_main': alternate}
      return {
        Bus.pt: dsu_bypass.BypassCANParser(DBC[CP.carFingerprint][Bus.pt], [(main, 10), ('EPS_STATUS', 25)], 0, **kwargs),
        Bus.cam: dsu_bypass.BypassCANParser(DBC[CP.carFingerprint][Bus.pt], [('ACC_CONTROL', 33)], 2, **kwargs),
      }
    dbc_name = DBC[CP.carFingerprint][Bus.pt]
    cam_messages = [("RSA1", math.nan)] if "RSA1" in CANDBC(dbc_name).name_to_msg else []
    if CP.flags & ToyotaFlags.AUTO_BRAKE_HOLD and uses_toyota_auto_hold_aeb(CP):
      cam_messages.append(("PRE_COLLISION_2", 50))
    pt_messages = [
      ("BLINKERS_STATE", float('nan')),
    ]

    if CP.carFingerprint == CAR.TOYOTA_SIENNA_4TH_GEN:
      pt_messages.append(("PCM_CRUISE_4", 1))
    from opendbc.car.toyota import camera_2aa_filter
    camera_twoaa = camera_2aa_filter.configured(CP)
    legacy_advert = legacy_advert_filter.configured(CP)
    alternate_sdsu_main = (legacy_sdsu.configured(CP) or legacy_advert) and CP.carFingerprint in legacy_sdsu.ALT_MAIN_CARS
    if CP.carFingerprint == CAR.LEXUS_IS or alternate_sdsu_main:
      pt_messages.append(("DSU_CRUISE", 10))

    if ((advert_filter.configured(CP) and not alternate_sdsu_main) or prius_sdsu.configured(CP) or
        prius_longitudinal.eps73_status_configured(CP) or prius_advert_filter.configured(CP)):
      pt_messages.append(("PCM_CRUISE_2", 10))
    if prius_sdsu.configured(CP) or prius_longitudinal.eps73_status_configured(CP) or prius_advert_filter.configured(CP):
      pt_messages.append(("EPS_STATUS", 25))
    if CP.flags & ToyotaFlags.LONG_FILTER and not prius_advert_filter.configured(CP):
      pt_messages.append(("SDSU", 50))
    if CP.flags & ToyotaFlags.ZSS:
      pt_messages.append(("SECONDARY_STEER_ANGLE", math.nan))

    if pedal.configured(CP):
      pt_messages.append(("GAS_SENSOR", 50))
    stock = stock_aol.configured(CP)
    pt_parser = (stock_aol.StockCANParser if stock else prius_advert_filter.PriusAdvertCANParser if prius_advert_filter.configured(CP) else
                 prius_sdsu.PriusSDSUCANParser if prius_sdsu.configured(CP) or prius_longitudinal.eps73_status_configured(CP) else
                 camera_2aa_filter.CameraTwoAACANParser if camera_twoaa else
                 legacy_advert_filter.LegacyAdvertCANParser if legacy_advert else
                 legacy_sdsu.AlternateMainSDSUCANParser if alternate_sdsu_main else
                 LexusIsCANParser if CP.carFingerprint == CAR.LEXUS_IS else pedal.PedalCANParser if pedal.configured(CP) else
                 advert_filter.AdvertFilterCANParser if CP.flags & ToyotaFlags.FILTER_BUTTONS or advert_filter.configured(CP) else
                 Rav4hCANParser if rav4h_filter_configured(CP) else CANParser)
    parsers = {
      Bus.pt: pt_parser(DBC[CP.carFingerprint][Bus.pt], pt_messages, 0, **({"stock_word": CP.safetyConfigs[0].safetyParam} if stock else
        {"alternate_main": alternate_sdsu_main} if legacy_advert else {})),
      Bus.cam: CANParser(dbc_name, cam_messages, 2),
    }
    if radar_pcs_fcw(CP):
      # optional (nan): a missing radar bus never makes CAN invalid, it only leaves the alarm off
      parsers[Bus.radar] = CANParser(ARS510_DBC, [(msg, float('nan')) for msg in RADAR_BUS_MESSAGES], 1)
    return parsers
