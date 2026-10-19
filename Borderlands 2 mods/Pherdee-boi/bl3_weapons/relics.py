"""BL3 artifact-style perks on BL2 relics (v0.37.0). Rolled and shown by gear.py; the effects live here.

  rl_nova  Slide Nova: the start of a slide (as anoint.watch_moves sees it) hits enemies within NOVA_RADIUS
           for 2x the held gun's shot, with a gyrojet blast at your feet for the look (as BL3 Movement's slam).
  rl_slam  Shockwave: when a BL3 Movement ground slam lands (slam._slamming goes True -> False), a second hit
           of the slam's own damage reaches 1.5x its radius: 2x damage inside, 1x in the new ring.
  rl_air   Airborne: +25% gun damage while falling (gear.weapon_bonus).
  rl_moon  Moon Boots: Pawn.JumpZ held at x1.3; while falling and aiming down sights, the fall is capped at
           HOVER_FALL for up to HOVER_TIME per jump (a velocity clamp in the move tick, like the slam's plunge).
  rl_rush  Victory Rush: a kill holds Pawn.GroundSpeed at x1.3 for 6 s (skipped while a Vladof bipod roots
           you, which holds GroundSpeed itself).
Held values follow the mod's usual rule: re-read the base when the game changed it, put it back afterwards.
"""

import math
import sys

import unrealsdk
from mods_base import get_pc, hook
from ui_utils import show_hud_message
from unrealsdk.hooks import prevent_hooking_direct_calls
from unrealsdk.unreal import UObject

from .core import log, now as game_now, pawns

NOVA_RADIUS, NOVA_COOLDOWN = 350.0, 1.5
SLIDE_SPEED = 330.0
SLAM_RADIUS = 420.0 * 1.5
HOVER_FALL, HOVER_TIME = 60.0, 2.0
RUSH_TIME = 6.0
BOOM_FM = "GD_Weap_AssaultRifle.FiringModes.Bullets_Assault_Torgue_GyroJet"
PHYS_WALKING, PHYS_FALLING = 1, 2

_nova_at = -1e9
_was_sliding = False
_was_slamming = False
_hover_used = 0.0
_rush_at = -1e9
_held: dict[str, dict] = {}  # "JumpZ" / "GroundSpeed" -> {"base", "written"}
_last_tick = None


def _perk() -> str | None:
    from . import gear

    return gear._active("relic")


def _shot_damage(pawn: UObject) -> float:
    weapon = pawn.Weapon
    if weapon is None or not weapon.Class._inherits(unrealsdk.find_class("WillowWeapon")):
        return 0.0
    return weapon.InstantHitDamage * max(1, weapon.ProjectilesPerShot)


def _blast(pc: UObject, radius: float, damage: float, boom: bool) -> int:
    """Enemies within `radius` of the player take `damage` (collected first, then damaged)."""
    pawn = pc.Pawn
    loc = pawn.Location
    targets = [
        p for p in pawns(pawn)
        if p._get_address() != pawn._get_address() and p.IsAliveAndWell() and pawn.IsEnemy(p)
        and math.dist((p.Location.X, p.Location.Y, p.Location.Z), (loc.X, loc.Y, loc.Z)) < radius
    ]
    zero = unrealsdk.make_struct("Vector", X=0, Y=0, Z=0)
    dtype = unrealsdk.find_class("WillowDmgSource_Bullet")
    with prevent_hooking_direct_calls():
        for p in targets:
            p.TakeDamage(damage, pc, p.Location, zero, dtype)
    if boom and pawn.Weapon is not None:
        try:
            fm = unrealsdk.find_object("FiringModeDefinition", BOOM_FM)
            below = unrealsdk.make_struct("Vector", X=loc.X, Y=loc.Y, Z=loc.Z - 400)
            pawn.Weapon.Behavior_Fire(
                fm, below, pawn, 1.0, 0.0, 0.0, unrealsdk.find_class("WillowDmgSource_Rocket"),
                pawn.Weapon.GetDamageTypeDefinitionForFiringMode(fm), pawn.Weapon.GetTraceImpact(), "None", True,
            )
        except Exception as e:  # noqa: BLE001 - the damage already happened
            log(f"relic blast visual failed: {e!r}")
    return len(targets)


def _hold(pawn: UObject, field: str, factor: float | None) -> None:
    """Hold pawn.<field> at base * factor; factor None puts the base back."""
    h = _held.get(field)
    current = getattr(pawn, field)
    if factor is None:
        if h is not None:
            if abs(current - h["written"]) <= 1e-4 * max(1.0, abs(h["written"])):
                setattr(pawn, field, h["base"])
            del _held[field]
        return
    if h is None or abs(current - h["written"]) > 1e-4 * max(1.0, abs(h["written"])):
        h = _held[field] = {"base": current, "written": current}
    setattr(pawn, field, h["base"] * factor)
    h["written"] = getattr(pawn, field)


def _aiming(pawn: UObject) -> bool:
    weapon = pawn.Weapon
    if weapon is None:
        return False
    zoom = weapon.ZoomState
    return getattr(zoom, "name", str(zoom)) in ("ZST_ZoomingIn", "ZST_Zoomed")


@hook("WillowGame.WillowPlayerController:PlayerWalking.PlayerMove", hook_identifier="bl3w_relic_move")
def relic_move(obj: UObject, *_: object) -> None:
    global _nova_at, _was_sliding, _was_slamming, _hover_used, _last_tick
    pawn = obj.Pawn
    if pawn is None:
        return
    now = game_now()
    dt = 0.0 if _last_tick is None else max(0.0, min(now - _last_tick, 0.1))
    _last_tick = now
    perk = _perk()

    # Moon Boots
    _hold(pawn, "JumpZ", 1.3 if perk == "rl_moon" else None)
    if pawn.Physics != PHYS_FALLING:
        _hover_used = 0.0
    elif perk == "rl_moon" and _aiming(pawn) and _hover_used < HOVER_TIME and pawn.Velocity.Z < -HOVER_FALL:
        slam = sys.modules.get("bl3_movement.slam")
        if not getattr(slam, "_slamming", False):
            pawn.Velocity.Z = -HOVER_FALL
            _hover_used += dt

    # Victory Rush (the bipod holds GroundSpeed itself)
    vladof = sys.modules.get(f"{__package__}.vladof")
    bipod = getattr(vladof, "_speed", None) is not None
    _hold(pawn, "GroundSpeed", 1.0 + 0.30 if perk == "rl_rush" and now - _rush_at < RUSH_TIME and not bipod else None)

    # Slide Nova
    v = pawn.Velocity
    sliding = bool(pawn.bIsCrouched) and pawn.Physics == PHYS_WALKING and math.hypot(v.X, v.Y) > SLIDE_SPEED
    if sliding and not _was_sliding and perk == "rl_nova" and now - _nova_at > NOVA_COOLDOWN:
        _nova_at = now
        dmg = _shot_damage(pawn) * 2.0
        hits = _blast(obj, NOVA_RADIUS, dmg, boom=True)
        log(f"relic Slide Nova: {hits} enemies hit for {dmg:.0f}")
    _was_sliding = sliding

    # Shockwave (BL3 Movement's slam landed)
    slam = sys.modules.get("bl3_movement.slam")
    slamming = bool(getattr(slam, "_slamming", False))
    if _was_slamming and not slamming and perk == "rl_slam" and slam is not None:
        dmg = slam._gun_damage(obj)
        hits = _blast(obj, SLAM_RADIUS, dmg, boom=False)
        log(f"relic Shockwave: {hits} enemies hit for {dmg:.0f}")
    _was_slamming = slamming


def on_kill() -> None:
    """Called by gear.on_kill."""
    global _rush_at
    if _perk() == "rl_rush":
        if game_now() - _rush_at >= RUSH_TIME:
            show_hud_message("Relic", "Victory Rush: +30% move speed", 1.5)
        _rush_at = game_now()


def reset() -> None:
    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if pawn is not None:
        for field in list(_held):
            _hold(pawn, field, None)
    _held.clear()


HOOKS = [relic_move]
