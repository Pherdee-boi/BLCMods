"""BL3 ground slam: press crouch in mid-air to plunge down; landing hits every enemy around you.

  * WillowPlayerInput:DuckPressed while airborne (and a moment after leaving the ground) starts it:
    horizontal speed is cut and the pawn is driven straight down.
  * WillowPlayerPawn:Landed (physics event) is the impact:
      - area damage via TakeDamage on enemies within RADIUS (WorldInfo.PawnList + IsEnemy, as in
        BL3 Weapons' Jakobs ricochet), scaled from the held gun's per-shot damage, as bullet damage
        (melee damage froze the game);
      - a real explosion at your feet for the look: the held gun fires one Torgue gyrojet round
        (explodes on any impact) straight down via WillowWeapon.Behavior_Fire, the same trick as the
        BL3 Weapons Tediore turret; it carries almost no damage itself;
      - a weapon jolt and a deep camera dip (arms.py).
"""

import math
import time

import unrealsdk
from mods_base import SliderOption, get_pc, hook
from unrealsdk.hooks import prevent_hooking_direct_calls
from unrealsdk.unreal import UObject

from . import arms

SLAM_SPEED = 2600.0  # Unreal units/s downward
MIN_AIR_TIME = 0.2  # s airborne before a slam can start
RADIUS = 420.0  # Unreal units
COOLDOWN = 1.0  # s
BOOM_FM = "GD_Weap_AssaultRifle.FiringModes.Bullets_Assault_Torgue_GyroJet"

damage_option = SliderOption(
    "Slam Damage",
    200,
    25,
    600,
    25,
    True,
    description="Ground slam damage, in percent of your held gun's per-shot damage.",
)

_slamming = False
_impact_pending = False  # set by Landed, handled on the next move tick
_air_since: float | None = None
_last_slam = -1e9
_boom_failed = False


def _gun_damage(pc: UObject) -> float:
    weapon = pc.Pawn.Weapon if pc.Pawn is not None else None
    if weapon is None:
        return 0.0
    return weapon.InstantHitDamage * max(1, weapon.ProjectilesPerShot) * damage_option.value / 100


def _boom(pc: UObject) -> None:
    """Real explosion at the player's feet (visual), via the held gun firing a gyrojet straight down."""
    global _boom_failed
    pawn, weapon = pc.Pawn, pc.Pawn.Weapon
    if _boom_failed or weapon is None:
        return
    try:
        fm = unrealsdk.find_object("FiringModeDefinition", BOOM_FM)
        loc = pawn.Location
        below = unrealsdk.make_struct("Vector", X=loc.X, Y=loc.Y, Z=loc.Z - 400)
        weapon.Behavior_Fire(
            fm,
            below,
            pawn,
            1.0,
            0.0,
            0.0,
            unrealsdk.find_class("WillowDmgSource_Rocket"),
            weapon.GetDamageTypeDefinitionForFiringMode(fm),
            weapon.GetTraceImpact(),
            "None",
            True,
        )
    except Exception as e:  # noqa: BLE001 - no explosion visual, the slam still works
        _boom_failed = True
        unrealsdk.logging.info(f"[BL3Movement] slam explosion unavailable: {e!r}")


def _pawns(player_pawn: UObject):
    """Walk WorldInfo.PawnList safely: stops on a cycle or after 1000 steps (a pawn removed from the
    list mid-walk, e.g. killed, can leave NextPawn pointing back into the list and hang the game).
    Callers must not damage/kill pawns while iterating: collect first, then act."""
    seen: set[int] = set()
    pawn = player_pawn.WorldInfo.PawnList
    while pawn is not None and len(seen) < 1000:
        addr = pawn._get_address()
        if addr in seen:
            return
        seen.add(addr)
        yield pawn
        pawn = pawn.NextPawn


def _impact(pc: UObject) -> None:
    """v0.3.0 froze the game: it damaged (and killed) enemies while walking PawnList. Now targets
    are collected first, then damaged."""
    pawn = pc.Pawn
    damage = _gun_damage(pc)
    loc = pawn.Location
    targets = []
    for other in _pawns(pawn):
        if other._get_address() != pawn._get_address() and other.IsAliveAndWell() and pawn.IsEnemy(other):
            p = other.Location
            if math.dist((p.X, p.Y, p.Z), (loc.X, loc.Y, loc.Z)) < RADIUS:
                targets.append(other)
    hits = 0
    if damage > 0:
        # Bullet damage type: v0.3.1 still froze with WillowDmgSource_Melee (BL2's melee path expects a
        # real melee attack). Bullet is the type BL3 Weapons' turret/ricochet use safely; hooks are off for
        # these calls so the slam can't trigger anointments or Jakobs ricochets.
        dtype = unrealsdk.find_class("WillowDmgSource_Bullet")
        zero = unrealsdk.make_struct("Vector", X=0, Y=0, Z=0)
        with prevent_hooking_direct_calls():
            for other in targets:
                other.TakeDamage(damage, pc, other.Location, zero, dtype)
                hits += 1
    _boom(pc)
    arms.pulse("pull", 0.18)
    arms.camera_dip(45.0, 0.4)
    arms.camera_anim(pc, "slam", 0.7)  # BL2's own explosion shake
    arms.fov_punch(10.0, 0.3)
    unrealsdk.logging.info(f"[BL3Movement] ground slam: {hits} enemies hit for {damage:.0f}")


@hook("WillowGame.WillowPlayerInput:DuckPressed")
def on_crouch(obj: UObject, *_: object) -> None:
    global _slamming, _last_slam
    pc = obj.Outer
    pawn = pc.Pawn if pc is not None else None
    if pawn is None or _slamming or _air_since is None:
        return
    now = time.monotonic()
    if now - _air_since < MIN_AIR_TIME or now - _last_slam < COOLDOWN or pawn.IsOnGroundOrShortFall():
        return
    _slamming, _last_slam = True, now
    pawn.Velocity.X, pawn.Velocity.Y, pawn.Velocity.Z = 0.0, 0.0, -SLAM_SPEED
    arms.pulse("reach", 0.6)  # weapon drops as you plunge


@hook("WillowGame.WillowPlayerController:PlayerWalking.PlayerMove")
def on_move(obj: UObject, *_: object) -> None:
    global _air_since, _slamming, _impact_pending
    pawn = obj.Pawn
    if pawn is None:
        return
    if _impact_pending:
        _impact_pending = False
        _impact(obj)
    if pawn.IsOnGroundOrShortFall():
        _air_since = None
    elif _air_since is None:
        _air_since = time.monotonic()
    if _slamming:
        pawn.Velocity.Z = min(pawn.Velocity.Z, -SLAM_SPEED)  # keep plunging
        if time.monotonic() - _last_slam > 3.0:  # failsafe: never stuck in slam mode
            _slamming = False


@hook("WillowGame.WillowPlayerPawn:Landed")
def on_landed(obj: UObject, *_: object) -> None:
    global _slamming, _impact_pending
    if not _slamming:
        return
    pc = get_pc(possibly_loading=True)
    if pc is None or pc.Pawn is None or obj._get_address() != pc.Pawn._get_address():
        return
    _slamming = False
    _impact_pending = True  # damage/explosion on the next move tick, outside the physics event


def reset() -> None:
    global _slamming, _air_since, _impact_pending
    _slamming = _impact_pending = False
    _air_since = None


HOOKS = [on_crouch, on_move, on_landed]
