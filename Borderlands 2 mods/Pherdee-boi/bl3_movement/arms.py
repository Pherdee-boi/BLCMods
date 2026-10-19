"""First-person arms poses shared by the moves (slide, slam) plus the camera work.

Poses are offsets from each arms mesh's rest pose on Pawn.Arms.SkeletalMesh (RotOrigin pitch/yaw/roll
in Unreal rotation units, Origin x/y/z in Unreal units). Moves request poses; the highest-priority
active pose is eased toward each tick. The camera dip lowers WillowPawn.BaseEyeHeight briefly.

Camera (v0.4.0, BL3 feel):
  * BL2's own camera animations, always loaded (Anim_CameraAnimations.Explosions.Canim_Explosion_* for
    the slam impact), played with
    PlayerController.ClientPlayCameraAnim(Anim, Scale, Rate, BlendIn, BlendOut);
  * an FOV kick while sliding and a short punch on slam impact: added on top of whatever BL2 wants
    (PlayerController.DesiredFOV, which BL2's sprint FOV also uses), re-read when BL2 changes it;
  * a slight camera tilt while sliding (PlayerController.Rotation.Roll), eased in and out.
"""

import math
import time

import unrealsdk
from mods_base import BoolOption, SliderOption
from unrealsdk.unreal import UObject

camera_fx = BoolOption("Camera Effects", True, description="BL3-style camera work: slide tilt and FOV kick, slam shake.")
slide_fov = SliderOption("Slide FOV Kick", 8, 0, 20, 1, True, description="Extra field of view while sliding (degrees).")
slide_tilt = SliderOption("Slide Camera Tilt", 4, 0, 10, 1, True, description="Camera tilt while sliding (degrees).")
OPTIONS = [camera_fx, slide_fov, slide_tilt]
ANIMS = {
    "slam": "Anim_CameraAnimations.Explosions.Canim_Explosion_Medium",
}

# name: (priority, rotation offset, origin offset, easing rate 1/s)
POSES = {
    "slide": (1, (500, -200, -6300), (-10.0, -14.5, -8.0), 10.0),  # Juso's slide pose
    # v0.4.2: positive pitch pointed the gun at the sky (user); a negative pitch lowers it like a put-down
    "reach": (2, (-6500, 0, 1800), (-8.0, 2.0, -40.0), 14.0),  # weapon drops down and out of view while plunging
    "pull": (3, (900, 0, 0), (4.0, 0.0, 4.0), 22.0),  # comes back up with a little overshoot
}
REST_RATE = 9.0

_active: dict[str, float | None] = {}  # pose -> expiry (monotonic) or None while held
_rest: dict[int, tuple[tuple[int, int, int], tuple[float, float, float]]] = {}
_cur = [0.0] * 6  # current offset: pitch, yaw, roll, x, y, z
_dip: tuple[float, float, float] | None = None  # (start, duration, depth)
_eye_rest: float | None = None
_view_hold: set[str] = set()  # "slide" while sliding
_punch: tuple[float, float, float] | None = None  # (start, duration, extra fov)
_fov_cur = 0.0
_roll_cur = 0.0
_fov_written: float | None = None
_fov_base = 0.0
_logged: set[str] = set()


def view_hold(name: str, on: bool) -> None:
    """Slide FOV kick + tilt while `on`."""
    (_view_hold.add if on else _view_hold.discard)(name)


def fov_punch(extra: float = 10.0, seconds: float = 0.3) -> None:
    global _punch
    _punch = (time.monotonic(), seconds, extra)


def camera_anim(pc: UObject, kind: str, scale: float = 1.0, rate: float = 1.0) -> None:
    """Play one of BL2's camera animations (ANIMS) on the player."""
    if not camera_fx.value or pc is None:
        return
    try:
        anim = unrealsdk.find_object("CameraAnim", ANIMS[kind])
    except ValueError:
        if kind not in _logged:
            _logged.add(kind)
            unrealsdk.logging.info(f"[BL3Movement] camera anim {ANIMS[kind]} not loaded")
        return
    pc.ClientPlayCameraAnim(anim, scale, rate, 0.05, 0.2)
    if f"played{kind}" not in _logged:
        _logged.add(f"played{kind}")
        unrealsdk.logging.info(f"[BL3Movement] camera anim {kind}: {anim.Name} x{scale}")


def _tick_view(pc: UObject, dt: float) -> None:
    """Ease the slide FOV kick / tilt and the slam FOV punch; apply them on top of BL2's own values."""
    global _fov_cur, _roll_cur, _fov_written, _fov_base, _punch
    on = camera_fx.value and "slide" in _view_hold
    fov_target = slide_fov.value if on else 0.0
    roll_target = -slide_tilt.value * 182.04 if on else 0.0  # degrees -> rotation units
    k = min(1.0, (8.0 if on else 6.0) * dt)
    _fov_cur += (fov_target - _fov_cur) * k
    _roll_cur += (roll_target - _roll_cur) * k
    punch = 0.0
    if _punch is not None:
        t = (time.monotonic() - _punch[0]) / _punch[1]
        if t >= 1:
            _punch = None
        elif camera_fx.value:
            punch = _punch[2] * math.sin(math.pi * t)
    extra = _fov_cur + punch
    current = pc.DesiredFOV
    if _fov_written is None or abs(current - _fov_written) > 0.01:
        _fov_base = current  # first time, or BL2 changed it (zoom, sprint FOV, settings)
    if abs(extra) > 0.05 or _fov_written is not None:
        pc.DesiredFOV = _fov_base + extra
        _fov_written = pc.DesiredFOV if abs(extra) > 0.05 else None
    if abs(_roll_cur) > 1 or pc.Rotation.Roll != 0:
        pc.Rotation.Roll = int(_roll_cur) if abs(_roll_cur) > 1 else 0


def hold(name: str, on: bool) -> None:
    """Keep a pose active while `on` (e.g. slide while sliding)."""
    if on:
        _active[name] = None
    else:
        _active.pop(name, None)


def pulse(name: str, seconds: float) -> None:
    """Activate a pose for a short time."""
    _active[name] = time.monotonic() + seconds


def camera_dip(depth: float = 22.0, seconds: float = 0.35) -> None:
    global _dip
    _dip = (time.monotonic(), seconds, depth)


def _target() -> tuple[tuple[float, ...], float]:
    now = time.monotonic()
    for name, until in list(_active.items()):
        if until is not None and now > until:
            del _active[name]
    if not _active:
        return (0.0,) * 6, REST_RATE
    _prio, rot, org, rate = max((POSES[n] for n in _active), key=lambda p: p[0])
    return (*rot, *org), rate


def tick(pc: UObject, dt: float) -> None:
    pawn = pc.Pawn
    arms = pawn.Arms if pawn is not None else None
    if arms is None or not arms.Attachments or arms.SkeletalMesh is None:
        return
    mesh = arms.SkeletalMesh
    key = mesh._get_address()
    if key not in _rest:
        r, o = mesh.RotOrigin, mesh.Origin
        _rest[key] = ((r.Pitch, r.Yaw, r.Roll), (o.X, o.Y, o.Z))
    target, rate = _target()
    k = min(1.0, rate * dt)
    moved = False
    for i in range(6):
        delta = target[i] - _cur[i]
        if abs(delta) > 0.01:
            _cur[i] += delta * k
            moved = True
    if moved:
        (rp, ry, rr), (ox, oy, oz) = _rest[key]
        mesh.RotOrigin.Pitch = int(rp + _cur[0])
        mesh.RotOrigin.Yaw = int(ry + _cur[1])
        mesh.RotOrigin.Roll = int(rr + _cur[2])
        mesh.Origin.X, mesh.Origin.Y, mesh.Origin.Z = ox + _cur[3], oy + _cur[4], oz + _cur[5]
    _tick_dip(pawn)
    _tick_view(pc, dt)


def _tick_dip(pawn: UObject) -> None:
    """Eye height = rest - sine-shaped dip."""
    global _dip, _eye_rest
    if _dip is None:
        if _eye_rest is not None and pawn.BaseEyeHeight != _eye_rest:
            pawn.BaseEyeHeight = _eye_rest
        _eye_rest = None
        return
    if _eye_rest is None:
        _eye_rest = pawn.BaseEyeHeight
    dip = 0.0
    if _dip is not None:
        start, length, depth = _dip
        t = (time.monotonic() - start) / length
        if t >= 1:
            _dip = None
        else:
            dip = depth * math.sin(math.pi * t)
    pawn.BaseEyeHeight = _eye_rest - dip


def reset(pc: UObject | None) -> None:
    """Put arms and camera back exactly (mod disabled)."""
    global _dip
    _active.clear()
    _cur[:] = [0.0] * 6
    pawn = pc.Pawn if pc is not None else None
    if pawn is None:
        return
    if _eye_rest is not None:
        pawn.BaseEyeHeight = _eye_rest
    _dip = None
    global _fov_cur, _roll_cur, _fov_written, _punch
    _view_hold.clear()
    _punch = None
    if _fov_written is not None:
        pc.DesiredFOV = _fov_base
    _fov_cur = _roll_cur = 0.0
    _fov_written = None
    pc.Rotation.Roll = 0
    mesh = pawn.Arms.SkeletalMesh if pawn.Arms is not None else None
    if mesh is not None and mesh._get_address() in _rest:
        (rp, ry, rr), (ox, oy, oz) = _rest[mesh._get_address()]
        mesh.RotOrigin.Pitch, mesh.RotOrigin.Yaw, mesh.RotOrigin.Roll = rp, ry, rr
        mesh.Origin.X, mesh.Origin.Y, mesh.Origin.Z = ox, oy, oz
