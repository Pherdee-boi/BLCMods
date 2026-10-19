"""BL3 slide: sprint + crouch on the ground slides with a burst of speed that decays (slower downhill,
faster uphill). Release crouch, stop or run out of speed to stand. Jumping out of a slide keeps the
momentum. The first-person arms tilt and drop while sliding. When a slide ends (out of speed, crouch
released, or off an edge) you stand and go straight back to sprinting, like BL3: sprint is driven by
WillowPlayerInput.bTryToSprint and crouching cancels it.

Technique credit: Juso's "Sliding" mod (bl2sdk-mods) - speed via WillowPawn.CrouchedPct while
crouched (0.5 normally), and the hooks WillowPlayerInput:DuckPressed / :Jump and
WillowPlayerController:PlayerWalking.PlayerMove. This is a fresh single-player implementation.
"""

import time

from mods_base import BoolOption, SliderOption, get_pc, hook
from unrealsdk.unreal import UObject

from . import arms

CROUCH_SPEED = 0.5  # WillowPawn.CrouchedPct default
END_SPEED = 0.55  # slide ends below this
COOLDOWN = 0.4  # s between slides

speed_option = SliderOption(
    "Slide Speed",
    24,
    12,
    40,
    1,
    True,
    description="Burst speed of a slide (x0.1 of walking speed; 24 = 2.4x).",
)
length_option = SliderOption(
    "Slide Length",
    10,
    4,
    25,
    1,
    True,
    description="How long a slide lasts on flat ground (x0.1 s).",
)

sprint_after = BoolOption(
    "Sprint After Slide",
    True,
    description="Stand up and keep sprinting when a slide ends (stopping during a slide still leaves you crouched).",
)

_sliding = False
_old_z = 0.0
_last_end = -1e9
_slide_jump: tuple[float, float] | None = None


def _decay_per_second() -> float:
    """Speed lost per second on flat ground so a slide lasts length_option/10 s."""
    return (speed_option.value / 10 - END_SPEED) / (length_option.value / 10)


def _enter(pc: UObject) -> None:
    global _sliding, _old_z
    _sliding = True
    _old_z = pc.Pawn.Location.Z
    pc.Pawn.CrouchedPct = speed_option.value / 10
    arms.hold("slide", True)
    arms.view_hold("slide", True)


def _exit(pc: UObject, to_sprint: bool = False) -> None:
    global _sliding, _last_end
    if not _sliding:
        return
    _sliding = False
    _last_end = time.monotonic()
    arms.hold("slide", False)
    arms.view_hold("slide", False)
    pawn = pc.Pawn
    if pawn is None:
        return
    pawn.CrouchedPct = CROUCH_SPEED
    if to_sprint and sprint_after.value:
        pc.bDuck = 0
        pawn.ShouldCrouch(False)
        if pc.PlayerInput is not None:
            pc.PlayerInput.bTryToSprint = True
        if pawn.CanSprint():  # WillowPawn.CanSprint / WillowPlayerController.BeginSprint (WillowGame.upk)
            pc.BeginSprint()


def reset(pc: UObject | None = None) -> None:
    """Stand up and put the arms/camera back (mod disabled)."""
    pc = pc or get_pc(possibly_loading=True)
    if pc is None:
        return
    _exit(pc)
    arms.reset(pc)


@hook("WillowGame.WillowPlayerInput:DuckPressed")
def on_crouch(obj: UObject, *_: object) -> None:
    pc = obj.Outer
    pawn = pc.Pawn if pc is not None else None
    if pawn is None or _sliding or time.monotonic() - _last_end < COOLDOWN:
        return
    if pc.bInSprintState and pawn.IsOnGroundOrShortFall():
        _enter(pc)


@hook("WillowGame.WillowPlayerInput:Jump")
def on_jump(obj: UObject, *_: object) -> None:
    global _slide_jump
    if _sliding:
        vel = obj.Outer.Pawn.Velocity
        _slide_jump = (vel.X, vel.Y)


@hook("WillowGame.WillowPlayerController:PlayerWalking.PlayerMove")
def on_move(obj: UObject, args, *_: object) -> None:
    global _old_z, _slide_jump
    pc, pawn, dt = obj, obj.Pawn, args.DeltaTime
    if pawn is None:
        return
    arms.tick(pc, dt)  # the one per-frame arms/camera update for every move

    # Slide-jump: jump first (standing up ends the slide), then restore the horizontal momentum
    if _slide_jump is not None:
        if pawn.IsOnGroundOrShortFall():
            pawn.DoJump(True)
        else:
            pawn.Velocity.X, pawn.Velocity.Y = _slide_jump
            _slide_jump = None
            _exit(pc, to_sprint=True)
            return

    if not _sliding:
        return
    accel = pawn.Acceleration
    if accel.X == 0 and accel.Y == 0:  # stopped on purpose: stay crouched
        _exit(pc)
        return
    if not pc.bDuck or not pawn.IsOnGroundOrShortFall():
        _exit(pc, to_sprint=True)
        return

    z_diff = pawn.Location.Z - _old_z
    _old_z = pawn.Location.Z
    speed = pawn.CrouchedPct
    if z_diff < 0:  # downhill: keeps (or gains) speed
        speed -= z_diff * 0.0005
    else:  # flat/uphill: decays, more uphill
        speed -= dt * _decay_per_second() + z_diff * 0.004
    pawn.CrouchedPct = speed
    if speed < END_SPEED:
        _exit(pc, to_sprint=True)


HOOKS = [on_crouch, on_jump, on_move]
