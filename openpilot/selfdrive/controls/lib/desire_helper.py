from openpilot.cereal import log
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.starpilot.lateral.lane_change_preferences import LaneChangePolicy, MINIMUM_SPEED_MPS, turn_signal_guidance_max

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.
LANE_CHANGE_START_TIME = 0.5
# Blind spot during a lane change (0000001c rt538.2 left change, rt540.5 left BSM on while still 1.3 m from the
# line): abort back to the own lane until the car's center reaches the target line; past that, finishing is safer.
BSM_ABORT_DEBOUNCE_S = 0.2
BSM_ABORT_MIN_LINE_M = 0.3
# Steering this far is the driver turning (0000001c off ramp, -62..-80 deg), not a nudge confirming a lane change.
# A legitimate nudge on a bend reached -34 deg (00000013 rt157.7).
NUDGE_MAX_ANGLE_DEG = 45.

class DesireHelper:
  def __init__(self, policy: LaneChangePolicy | None = None):
    self.policy = policy if policy is not None else LaneChangePolicy()
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.prev_one_blinker = False
    self.lane_change_performed = False
    self.desire = log.Desire.none
    self.pre_lane_change_timer = 0.0
    self.auto_signal_blocked = False
    self.last_signal_direction = LaneChangeDirection.none
    self.auto_status = "manualRequired"
    self.blindspot_timer = 0.0
    self.line_crossed = False
    self.bsm_aborted = False
    self.bsm_during_change = False

  @staticmethod
  def get_lane_change_direction(CS):
    return LaneChangeDirection.left if CS.leftBlinker else LaneChangeDirection.right

  def update(self, carstate, lateral_active, lane_change_prob, *, auto_evidence=False, engaged=False, navigation_turn=False,
             road_edge_blocked=False, navigation_guiding=False, target_line_m=None):
    """target_line_m: lateral distance from the car's center to the lane line on the lane-change side (None: unknown)."""
    v_ego = carstate.vEgo
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    if not carstate.leftBlinker and not carstate.rightBlinker:
      self.lane_change_performed = False
      self.auto_signal_blocked = False
    signal_direction = self.get_lane_change_direction(carstate) if one_blinker else LaneChangeDirection.none
    if self.policy.auto_lane_change and (carstate.leftBlinker or carstate.rightBlinker) and (
      not one_blinker or not lateral_active or not engaged or
      (self.last_signal_direction not in (LaneChangeDirection.none, signal_direction))):
      self.auto_signal_blocked = True
    self.last_signal_direction = signal_direction
    self.auto_status = "manualRequired"
    self.bsm_aborted = False
    self.bsm_during_change = False
    below_lane_change_speed = v_ego < max(MINIMUM_SPEED_MPS, self.policy.minimum_speed_mps,
                                          turn_signal_guidance_max(navigation_guiding) if self.policy.turn_signal_guidance else 0.0)

    if not self.policy.enabled or not lateral_active or below_lane_change_speed or self.lane_change_timer > LANE_CHANGE_TIME_MAX:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
      self.lane_change_timer = 0.0
      self.pre_lane_change_timer = 0.0
    else:
      if navigation_turn and self.lane_change_state == LaneChangeState.preLaneChange:
        self.lane_change_state = LaneChangeState.off
        self.lane_change_direction = LaneChangeDirection.none
        self.pre_lane_change_timer = 0.0
      if (self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and
          not below_lane_change_speed and not navigation_turn and not (self.policy.one_per_signal and self.lane_change_performed)):
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_timer = 0.0
        self.pre_lane_change_timer = 0.0
        # Initialize lane change direction to prevent UI alert flicker
        self.lane_change_direction = self.get_lane_change_direction(carstate)

      elif self.lane_change_state == LaneChangeState.preLaneChange:
        # Update lane change direction
        self.lane_change_direction = self.get_lane_change_direction(carstate)

        torque_applied = carstate.steeringPressed and abs(getattr(carstate, 'steeringAngleDeg', 0.)) <= NUDGE_MAX_ANGLE_DEG and \
                         ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right))

        blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                              (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))
        # road_edge_blocked is for the signaled side: a median/curb right past the line is not a lane
        if road_edge_blocked:
          auto_evidence = False
        if not (self.policy.auto_lane_change and engaged and auto_evidence and not blindspot_detected and
                not self.auto_signal_blocked):
          self.pre_lane_change_timer = 0.0
        else:
          self.pre_lane_change_timer += DT_MDL
        auto_ready = (self.policy.auto_lane_change and engaged and auto_evidence and not self.auto_signal_blocked and
                      self.pre_lane_change_timer >= self.policy.auto_delay_s)
        if blindspot_detected:
          self.auto_status = "blindspotBlocked"
        elif road_edge_blocked:
          self.auto_status = "laneUnavailable"
        elif self.policy.auto_lane_change and engaged:
          self.auto_status = "waitingForDelay" if auto_evidence and not self.auto_signal_blocked else "laneUnavailable"

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_timer = 0.0
          self.pre_lane_change_timer = 0.0
        elif (torque_applied or auto_ready) and not blindspot_detected and not road_edge_blocked and not (self.policy.one_per_signal and self.lane_change_performed):
          self.lane_change_state = LaneChangeState.laneChangeStarting
          self.lane_change_timer = 0.0
          self.pre_lane_change_timer = 0.0
          self.blindspot_timer = 0.0
          self.line_crossed = False
          if self.policy.one_per_signal:
            self.lane_change_performed = True

      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        self.lane_change_timer += DT_MDL
        # The target line jumps to the far side once crossed, so crossing is latched.
        if target_line_m is not None and target_line_m < BSM_ABORT_MIN_LINE_M:
          self.line_crossed = True
        target_blindspot = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                            (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))
        self.blindspot_timer = self.blindspot_timer + DT_MDL if target_blindspot else 0.0
        self.bsm_during_change = target_blindspot

        if (self.blindspot_timer >= BSM_ABORT_DEBOUNCE_S - 1e-6 and not self.line_crossed and
            target_line_m is not None and one_blinker):
          # Back to preLaneChange: desire none returns the car to its lane; the blind spot then holds the
          # restart, and a clear blind spot restarts after the normal automatic delay or a nudge.
          self.lane_change_state = LaneChangeState.preLaneChange
          self.lane_change_direction = self.get_lane_change_direction(carstate)
          self.lane_change_timer = 0.0
          self.pre_lane_change_timer = 0.0
          self.blindspot_timer = 0.0
          self.lane_change_performed = False
          self.bsm_aborted = True
        elif lane_change_prob < 0.02 and self.lane_change_timer >= LANE_CHANGE_START_TIME:
          self.lane_change_timer = 0.0
          if one_blinker and not (self.policy.one_per_signal and self.lane_change_performed):
            self.lane_change_state = LaneChangeState.preLaneChange
            self.lane_change_direction = self.get_lane_change_direction(carstate)
          else:
            self.lane_change_state = LaneChangeState.off
            self.lane_change_direction = LaneChangeDirection.none

    self.prev_one_blinker = one_blinker and lateral_active

    self.desire = log.Desire.none
    if self.lane_change_state == LaneChangeState.laneChangeStarting:
      if self.lane_change_direction == LaneChangeDirection.left:
        self.desire = log.Desire.laneChangeLeft
      elif self.lane_change_direction == LaneChangeDirection.right:
        self.desire = log.Desire.laneChangeRight
