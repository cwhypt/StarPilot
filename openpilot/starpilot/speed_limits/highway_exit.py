"""Navigation-confirmed freeway exits for SLC.

A held pedal speed (SLCOverrideMode "Hold the speed you reached") belongs to the freeway. Vision and dashboard
limits misread low on freeways (0000001c: 40 at 63 mph; 0000001e: 65/55 flicker), so a lower limit alone cannot end
the hold. Navigation can: an "off ramp" step that completes, followed only by ramp forks, and then a surface-street
step (turn/arrive/end of road) means the car has left the freeway (0000001c rt750, 0000001e rt932.6).
"""

RAMP_CHAIN = ('off ramp', 'fork')
FREEWAY_AGAIN = ('merge', 'on ramp')
COMPLETE_M = 150.
NEW_STEP_JUMP_M = 50.


class HighwayExit:
  def __init__(self):
    self.last: tuple[str, str, float] | None = None
    self.on_exit_ramp = False

  def update(self, nav) -> bool:
    """Feed the current guiding navigation state (or None). True once, on the step that leaves the exit ramp."""
    if nav is None:
      return False
    try:
      instruction = nav.instruction
      step = (str(instruction.maneuverType), str(instruction.maneuverModifier), float(instruction.distanceMeters))
    except (AttributeError, TypeError, ValueError):
      return False
    last, self.last = self.last, step
    if last is None:
      return False
    new_step = step[:2] != last[:2] or step[2] > last[2] + NEW_STEP_JUMP_M
    if not new_step:
      return False
    completed = last[2] < COMPLETE_M
    if not completed:
      self.on_exit_ramp = False  # rerouted or skipped: the previous step was not driven
      return False
    if last[0] == 'off ramp':
      self.on_exit_ramp = True
    if not self.on_exit_ramp:
      return False
    if step[0] in RAMP_CHAIN:
      return False
    self.on_exit_ramp = False
    return step[0] not in FREEWAY_AGAIN
