"""Jakobs: critical hits ricochet a bullet to the nearest other enemy (BL3 Jakobs passive).

Hook: WillowAIPawn.TakeDamage, post-unconditional so it also runs when an anointment blocked and
re-issued the hit. WillowPawn.bWasLastDamageACriticalHit says whether that hit was a crit.
Targets come from WorldInfo.PawnList; hostility from the player pawn's IsEnemy(); ricochets
don't ricochet again.

Visual: a tracer in the gun's element from the crit point to the new target, using BL2's always-loaded
Beam2 tracers (FX_WEP_Trails.Particles.Part_Wep_Tracer_<Element>, ends set with SetBeamSourcePoint /
SetBeamTargetPoint / SetBeamEndPoint), plus Siren's ricochet spark (FX_CHAR_Siren.Particles.
Part_BulletRicochet) at the bounce point. Spawned through WorldInfo.MyEmitterPool, never inside the
damage call.
"""

import math

import unrealsdk
from mods_base import BoolOption, SliderOption, get_pc, hook
from unrealsdk.hooks import Type, prevent_hooking_direct_calls
from unrealsdk.unreal import UObject

from .core import held_weapon, log, maker_of, pawns

GUN_DAMAGE = {"Bullet", "Pistol", "SubMachineGun", "Shotgun", "Sniper", "MachineGun", "Rocket"}
RANGE = 2000.0  # Unreal units from the crit target (about 20 m)

ricochet_pct = SliderOption(
    "Jakobs Ricochet Damage",
    75,
    10,
    150,
    5,
    True,
    description="Percent of a critical hit's damage dealt by its Jakobs ricochet.",
)

show_tracer = BoolOption("Jakobs Ricochet Tracer", True, description="Show a tracer and a spark when a Jakobs crit ricochets.")

TRACERS = {
    None: "FX_WEP_Trails.Particles.Part_Wep_Tracer_Default",
    "Fire": "FX_WEP_Trails.Particles.Part_Wep_Tracer_Incendiary",
    "Shock": "FX_WEP_Trails.Particles.Part_Wep_Tracer_Shock",
    "Corrosive": "FX_WEP_Trails.Particles.Part_Wep_Tracer_Corrosive",
    "Slag": "FX_WEP_Trails.Particles.Part_Wep_Tracer_Slag",
    "Explosive": "FX_WEP_Trails.Particles.Part_Wep_Tracer_Explosive",
    "Radiation": "FX_WEP_Trails.Particles.Part_Wep_Tracer_Corrosive",  # a placeholder: corrosive's green tracer until radiation has art of its own
}
SPARK = "FX_CHAR_Siren.Particles.Part_BulletRicochet"

_logged = False
_fx_logged = False


def _element(weapon: UObject) -> str | None:
    part = weapon.DefinitionData.ElementalPartDefinition
    suffix = part.Name.rsplit("_", 1)[-1] if part is not None else None
    return suffix if suffix in TRACERS else None


def _particle(path: str) -> UObject | None:
    try:
        return unrealsdk.find_object("ParticleSystem", path)
    except ValueError:
        return None


def _vec(x: float, y: float, z: float):
    return unrealsdk.make_struct("Vector", X=x, Y=y, Z=z)


def _show(pc: UObject, weapon: UObject, start, target: UObject) -> None:
    """Element tracer from the crit point to the ricochet target, and a spark at the bounce."""
    global _fx_logged
    pool = pc.WorldInfo.MyEmitterPool if pc.WorldInfo is not None else None
    if pool is None:
        return
    end = target.Location
    dx, dy, dz = end.X - start.X, end.Y - start.Y, end.Z - start.Z
    rot = unrealsdk.make_struct(
        "Rotator",
        Pitch=int(math.atan2(dz, math.hypot(dx, dy)) * 32768 / math.pi),
        Yaw=int(math.atan2(dy, dx) * 32768 / math.pi),
        Roll=0,
    )
    tracer = _particle(TRACERS.get(_element(weapon), TRACERS[None])) or _particle(TRACERS[None])
    if tracer is not None:
        psc = pool.SpawnEmitter(tracer, start, rot, None, None, 0, False, True)
        if psc is not None:
            for i in range(len(tracer.Emitters)):
                psc.SetBeamSourcePoint(i, start, 0)
                psc.SetBeamTargetPoint(i, end, 0)
                psc.SetBeamEndPoint(i, end)
    spark = _particle(SPARK)
    if spark is not None:
        pool.SpawnEmitter(spark, start, rot, None, None, 0, False, True)
    if not _fx_logged:
        _fx_logged = True
        log(f"Jakobs ricochet tracer: {tracer.Name if tracer else None}, spark: {spark is not None}")


def _dist(a: UObject, b: UObject) -> float:
    la, lb = a.Location, b.Location
    return math.sqrt((la.X - lb.X) ** 2 + (la.Y - lb.Y) ** 2 + (la.Z - lb.Z) ** 2)


def _nearest_enemy(player_pawn: UObject, hit: UObject) -> UObject | None:
    best, best_d = None, RANGE
    for pawn in pawns(player_pawn):
        if (
            pawn._get_address() != hit._get_address()
            and pawn._get_address() != player_pawn._get_address()
            and pawn.IsAliveAndWell()
            and player_pawn.IsEnemy(pawn)
        ):
            d = _dist(pawn, hit)
            if d < best_d:
                best, best_d = pawn, d
    return best


@hook("WillowGame.WillowAIPawn:TakeDamage", Type.POST_UNCONDITIONAL)
def ricochet(obj: UObject, args, *_: object) -> None:
    global _logged
    pc = get_pc(possibly_loading=True)
    if pc is None or pc.Pawn is None or args.InstigatedBy is None:
        return
    if args.InstigatedBy._get_address() != pc._get_address() or args.DamageType is None:
        return
    if args.DamageType.Name.removeprefix("WillowDmgSource_") not in GUN_DAMAGE:
        return
    weapon = held_weapon()
    if weapon is None or maker_of(weapon) != "Jakobs" or not obj.bWasLastDamageACriticalHit:
        return

    target = _nearest_enemy(pc.Pawn, obj)
    if target is None:
        return
    damage = args.Damage * ricochet_pct.value / 100
    with prevent_hooking_direct_calls():  # no ricochet-of-a-ricochet, no double anointment
        target.TakeDamage(
            damage,
            pc,
            target.Location,
            unrealsdk.make_struct("Vector", X=0, Y=0, Z=0),
            args.DamageType,
        )
    if show_tracer.value:
        try:
            _show(pc, weapon, args.HitLocation, target)
        except Exception as e:  # noqa: BLE001 - the visual must never break the ricochet
            log(f"Jakobs ricochet tracer error: {e!r}")
    if not _logged:
        _logged = True
        log(f"Jakobs ricochet works: {damage:.0f} dmg -> {target.Class.Name} ({_dist(target, obj):.0f}uu)")


HOOKS = [ricochet]
