"""TPS's frozen look on BL2's enemies, and a frost Bloodsplosion (RESEARCH.md §6.30).

TPS draws a frozen enemy natively: icicles on its bones while it's frozen, the ice breaking free when it thaws, a shatter
when it dies frozen. Her build carries TPS's own particle systems for that into Crocus_IceFX_SF (tools/icefx.py), and
core/particles.py gives them materials compiled at runtime (TPS's can't load). This spawns them from element.py's events:
- frozen: Part_Frozen_Icicles, attached to the enemy. Its bone-socket modules take the body from the actor parameter
  `BoneSocketActor` (UE3's default name) and put the icicles on random bones, following them. TPS's modules list no
  bones (TPS's engine then picks any); BL2's engine picks from the list and crashed on an empty one (bl2-test,
  2026-10-03, twice), so the list is filled before each spawn with a dozen of the enemy's own bones (`_aim`);
- thawed alive: the icicles stop, and Part_Frozen_BreakFree bursts where it stands;
- died frozen: the icicles stop, Part_Frozen_Shatter bursts with the Cryo Nova's blast (TPS's OnShatter explodes the
  shield nova; its graph gives that no radius, so the blast is drawn at half its first band's full size, which filled
  the screen at every frozen kill), and the body is hidden, as TPS removes a shattered body (unless BL2 gibbed it).
Not carried: TPS's frozen material layer (BL2's enemy materials have none; element.py's tint stands in) and the status
particles' snowflakes.

Bloodsplosion (Krieg's skill, which other classes get through skill mods) picks its blast and gore by the killing
element: one kill event per element, each naming its damage types (shock, slag, corrosive, fire, and "explosive or
normal"). Cryo's are none of them, so a cryo kill never set it off (the user's report, 2026-10-03): they join the
"explosive or normal" event (`_bloodsplosion_cryo`: when a skill tree is set up, which is when a skill mod or Krieg's
class brings the skill in, and at each map start). That branch spawns its gore at once and its
blast 1.5 s later (the graph's link delay); a cryo kill's gore becomes TPS's shatter and, paired by that delay, its
blast the cryo master explosion (explosions.py, with TPS's ice explosion).
"""

from __future__ import annotations

import copy
from typing import Any

import unrealsdk
from mods_base import hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WeakPointer, WrappedStruct

from ..core import actors, later, maps, particles
from . import element

PACKAGE = "Crocus_IceFX_SF"
FX = "Crocus_IceFX.Particles."
ICICLES = FX + "Part_Frozen_Icicles"
BREAK_FREE = FX + "Part_Frozen_BreakFree"
SHATTER = FX + "Part_Frozen_Shatter"
NOVA_BLAST = FX + "Part_Ice_Nova_Shield_Explosion"     # carried since 0.3.1 (older builds: no blast)
NOVA_BLAST_SCALE = 0.75    # half Explosion_IceMaster_ShieldNova's first-band MaxParticleScale (1.5)
SYSTEMS = (ICICLES, BREAK_FREE, SHATTER, FX + "Part_Frozen_BreakingLoose")
# The package's particle systems -> the TPS material of each of their emitters, in order: core/particles.py gives them
# their materials by these names (OWN: an emitter whose material is BL2's own, imported with the package).
OWN = particles.OWN
particles.EMITTERS.update({
    # TPS's frozen-enemy effects, carried into Crocus_IceFX_SF (tools/icefx.py; read with tools/dump.py)
    FX + "Part_Frozen_Icicles": ("Mat_IceChunk", "Mat_IceChunk", "DefaultParticle", "Mat_IceChunk",
                                 "Mat_SimpleSnow"),
    FX + "Part_Frozen_BreakingLoose": ("Mat_IceChunk2", "Mat_SimpleSnow", "Mat_SimpleSnow",
                                       "Mat_IceChunk2", "Mat_IceSpecs", "Mat_IceChunk"),
    FX + "Part_Frozen_BreakFree": ("Mat_SimpleSnow", "Mat_SimpleSnow", "Mat_IceChunk2", "Mat_IceSpecs",
                                   "Mat_IceChunk"),
    FX + "Part_Frozen_Shatter": ("Mat_SimpleSnow", "Mat_SimpleSnow", "Mat_IceSpecs"),
    # TPS's cryo explosions (tools/fxscan.py on TPS's Startup.upk). Their dark flash is TPS's, ported: BL2's own
    # material of that name drew a hard-edged grey patch with TPS's values (bl2-test, 2026-10-03).
    FX + "Part_IceExplosion_Small": ("Mat_Emp_Trans", "Mat_SimpleSnow", "Mat_IceSpecs",
                                     "Mat_AcidSprayGoop", "Mat_ExplosionFlashModulate",
                                     "Mat_IceChunk2", "Mati_Explosive_Spikes"),
    FX + "Part_IceExplosion_Medium": ("Mat_Emp_Trans", "Mat_SimpleSnow", "Mat_IceSpecs",
                                      "Mat_AcidSprayGoop", "Mat_ExplosionFlashModulate",
                                      "Mat_IceChunk2", "Mat_IceChunk2", "Mati_Explosive_Spikes",
                                      OWN),
    FX + "Part_IceExplosion_Large": ("Mat_Emp_Trans", "Mat_SimpleSnow", "Mat_SimpleSnow",
                                     "Mat_IceSpecs", "Mat_AcidSprayGoop", OWN,
                                     "Mat_ExplosionFlashModulate", "Mat_ExplosionFlashModulate",
                                     "Mat_IceChunk2", "Mat_IceChunk2", "Mati_Dirtkick",
                                     "Mati_Explosive_Spikes", OWN),
    FX + "Part_Ice_Nova_Shield_Explosion": (OWN, OWN, OWN, "Mat_Emp_Trans", "Mat_AcidSprayGoop",
                                            "Mat_ExplosionFlashModulate",
                                            "Mat_ExplosionFlashModulate", OWN, OWN),
    # TPS's cryo area-effect grenade's spray (switches.py puts it on BL2's grenade), carried since 0.4.4
    FX + "Part_Ice_AOESpray": ("Mat_WaterSpringDistorted", "Mat_Water_INST02", OWN, "Mat_AcidSprayGoop",
                               "Mat_WaterSpringDistorted", "Mat_ExplosionFlashModulate", "Mat_SimpleSnow"),
})
BONE_SOCKET_ACTOR = "BoneSocketActor"
# Bloodsplosion's default branch (GD_Lilac_Skills_Bloodlust, in Krieg's DLC): its blast and its gore
BLOODSPLOSION = "GD_Lilac_Skills_Bloodlust.Skills.Bloodsplosion.BehaviorProviderDefinition_0."
BLOODSPLOSION_BLAST = BLOODSPLOSION + "Behavior_Explode_64"
BLOODSPLOSION_GORE = BLOODSPLOSION + "Behavior_SpawnParticleSystem_63"
BLOODSPLOSION_SKILL = "GD_Lilac_Skills_Bloodlust.Skills.Bloodsplosion"
BLOODSPLOSION_EVENT = "Killed Enemy Explosive or Normal"     # the kill event of the branch above
CRYO_KILL_WINDOW = 0.25     # seconds: a Bloodsplosion gore this soon after a lethal cryo hit belongs to that kill
BLAST_DELAY = 1.5           # seconds from the gore to the blast (the graph's link delay)
BLAST_TOLERANCE = 0.4       # how far from that a blast may run and still be the cryo kill's
_RF_ROOTSET = 0x4000

BONE_COUNT = 12                             # bones the icicles and the thaw's ice spread over
_SKIP_BONES = ("root", "ik", "weapon", "attach", "camera", "socket", "hold", "grip", "fx", "aim", "target",
               "finger", "thumb", "toe", "eye", "lid", "brow", "lip", "jaw", "tongue", "teeth", "cheek", "nail",
               "twist", "helper", "dummy")   # whole limbs and the trunk, not small parts

_systems: dict[str, UObject] = {}
_bone_modules: list[UObject] = []           # every bone-socket module of the systems (shared between emitters)
_aimed: list[str | None] = [None]           # the skeleton those modules name the bones of now
_bones: dict[str, list[str]] = {}           # skeletal mesh path -> the bones its effects use
# enemy path -> (its icicles' particle component, the enemy), both weakly: an enemy that is gone without a thaw
# leaves its entry here, the pool may let the component go, and the next enemy may get the same name
_icicles: dict[str, tuple[WeakPointer, WeakPointer]] = {}
_swapped: list[tuple[UObject, str, UObject]] = []
_cryo_blasts: list[float] = []              # game times a cryo kill's Bloodsplosion blast is due
_said: set[str] = set()


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] frozen fx: {msg}")


def _once(key: str, msg: str) -> None:
    if key not in _said:
        _said.add(key)
        _log(msg)


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def load() -> bool:
    """Load her build's ice effects package (absent before a build, or with an older one) and keep its systems."""
    if len(_systems) == len(SYSTEMS):
        return True
    if _find("Package", PACKAGE) is None:
        unrealsdk.load_package(PACKAGE)
    for path in SYSTEMS:
        system = _find("ParticleSystem", path)
        if system is None:
            _once("missing", f"no {path}: her files predate TPS's ice effects (a rebuild adds them)")
            return False
        system.ObjectFlags = int(system.ObjectFlags) | _RF_ROOTSET
        _systems[path] = system
    seen: set[str] = set()
    for system in _systems.values():
        for emitter in system.Emitters:
            for lod in (emitter.LODLevels if emitter is not None else ()):
                for module in (lod.Modules if lod is not None else ()):
                    if (module is not None and module.Class.Name == "ParticleModuleLocationBoneSocket"
                            and module._path_name() not in seen):
                        seen.add(module._path_name())
                        _bone_modules.append(module)
    _aimed[0] = None
    _log(f"{len(_systems)} TPS ice effects loaded ({len(_bone_modules)} bone-socket modules)")
    return True


def _bones_of(pawn: UObject) -> list[str]:
    """A dozen of the pawn's bones, spread over its skeleton (no root, IK, weapon or attachment bones)."""
    mesh = pawn.Mesh
    skeleton = mesh.SkeletalMesh if mesh is not None else None
    if skeleton is None:
        return []
    key = skeleton._path_name()
    if key not in _bones:
        names = []
        for i in range(512):
            name = str(mesh.GetBoneName(i))
            if name in ("", "None"):
                break
            names.append(name)
        usable = [n for n in names if not any(s in n.lower() for s in _SKIP_BONES)] or names
        step = max(1, len(usable) // BONE_COUNT)
        _bones[key] = usable[step // 2::step][:BONE_COUNT]
    return _bones[key]


def _aim(pawn: UObject) -> bool:
    """Point every bone-socket module at this pawn's bones (they're shared: set before each spawn)."""
    bones = _bones_of(pawn)
    if not bones:
        return False
    skeleton = pawn.Mesh.SkeletalMesh._path_name()
    if _aimed[0] == skeleton:
        return True                               # they name this skeleton's bones already (a crowd of one kind)
    zero = unrealsdk.make_struct("Vector", X=0.0, Y=0.0, Z=0.0)
    for module in _bone_modules:
        module.SourceLocations = [unrealsdk.make_struct("LocationBoneSocketInfo", BoneSocketName=name, Offset=zero)
                                  for name in bones]
    _aimed[0] = skeleton
    return True


def _pool() -> Any:
    """The running world's emitter pool (asked of the engine: `pawn.WorldInfo` by name is the slowest read on a
    pawn, and each of these looks made it)."""
    world = actors.running_world()
    return world.MyEmitterPool if world is not None else None


def _spawn(path: str, pawn: UObject, attach: bool = False) -> Any:
    system = _systems.get(path)
    pool = _pool() if system is not None and pawn is not None else None
    if pool is None:
        return None
    loc = actors.LOCATION_OF(pawn)
    where = unrealsdk.make_struct("Vector", X=float(loc.X), Y=float(loc.Y), Z=float(loc.Z))
    facing = unrealsdk.make_struct("Rotator", Pitch=0, Yaw=0, Roll=0)
    return pool.SpawnEmitter(system, where, facing, pawn if attach else None)


def _stop(pawn: UObject) -> None:
    held = _icicles.pop(pawn._path_name(), None)
    if held is None:
        return
    component, owner = held[0](), held[1]()
    if component is None or owner != pawn:
        return                                    # the pool let it go, or it was another enemy's of this name
    try:
        component.DeactivateSystem()              # no new icicles; the ones there melt away on their own lifetimes
    except Exception:  # noqa: BLE001 - the pool may have recycled it
        pass


def _on_freeze(pawn: UObject, _instigator: Any) -> None:
    # Looks, 0.8 ms an enemy: they wait their turn (core/later.py), a frame or a few after the freeze.
    if load():
        later.do(_ice_over, WeakPointer(pawn))


def _ice_over(ref: WeakPointer) -> None:
    """Its turn has come: the icicles, if the enemy is still there and still frozen."""
    pawn = ref()
    if pawn is None or not actors.is_live_actor(pawn) or not element.is_frozen(pawn):
        return
    _stop(pawn)
    if not _aim(pawn):
        return                                    # no skeleton to hang icicles on
    component = _spawn(ICICLES, pawn, attach=True)
    if component is not None:
        component.SetActorParameter(BONE_SOCKET_ACTOR, pawn)
        _icicles[pawn._path_name()] = (WeakPointer(component), WeakPointer(pawn))
        _once("icicles", f"first icicles, on {pawn.Name}")


def _on_thaw(pawn: UObject, alive: bool) -> None:
    _stop(pawn)
    if alive and load():
        later.do(_break_free, WeakPointer(pawn))


def _break_free(ref: WeakPointer) -> None:
    pawn = ref()
    if pawn is not None and actors.is_live_actor(pawn) and _aim(pawn):
        component = _spawn(BREAK_FREE, pawn)
        if component is not None:
            component.SetActorParameter(BONE_SOCKET_ACTOR, pawn)


def _on_shatter(pawn: UObject, gibbed: bool) -> None:
    _stop(pawn)
    if not load():
        return
    _spawn(SHATTER, pawn)
    blast = _find("ParticleSystem", NOVA_BLAST)
    pool = _pool() if blast is not None else None
    if pool is not None:
        loc = actors.LOCATION_OF(pawn)
        where = unrealsdk.make_struct("Vector", X=float(loc.X), Y=float(loc.Y), Z=float(loc.Z))
        burst = pool.SpawnEmitter(blast, where, unrealsdk.make_struct("Rotator"), None)
        if burst is not None:
            burst.SetScale(NOVA_BLAST_SCALE)
    if not gibbed and actors.is_live_actor(pawn):
        pawn.SetHidden(True)                      # TPS removes a shattered body; the ice burst takes its place
    if "shatter" not in _said:
        _once("shatter", f"first shatter burst, on {pawn.Name} ({'gibbed' if gibbed else 'body hidden'})")


element.ON_FREEZE_LOOK.append(_on_freeze)
element.ON_THAW.append(_on_thaw)
element.ON_SHATTER.append(_on_shatter)


# ---------------------------------------------------------------------------------------------- Bloodsplosion
def _bloodsplosion_cryo() -> bool:
    """Cryo's damage types join Bloodsplosion's "explosive or normal" kill event. Idempotent; True when the skill is
    loaded (Krieg's, or a skill mod's) and has them."""
    skill = _find("SkillDefinition", BLOODSPLOSION_SKILL)
    if skill is None:
        return False
    objects = element.ensure_objects()
    ours = [objects[key] for key in (element.IMPACT, element.IMPACT_NO_FREEZE, element.STATUS_DAMAGE) if objects.get(key)]
    events = [copy.copy(e) for e in skill.KillEvents]
    for event in events:
        if str(event.EventName) != BLOODSPLOSION_EVENT or not event.EventConstraints:
            continue
        constraints = [copy.copy(c) for c in event.EventConstraints]
        have = {c.DamageTypeConstraint._path_name() for c in constraints if c.DamageTypeConstraint is not None}
        missing = [damage for damage in ours if damage._path_name() not in have]
        if not missing:
            return True
        for damage in missing:
            constraint = copy.copy(constraints[0])
            constraint.DamageTypeConstraint = damage
            constraints.append(constraint)
        event.EventConstraints = constraints
        skill.KillEvents = events
        _log(f"Bloodsplosion: cryo kills join its '{BLOODSPLOSION_EVENT}' event")
        return True
    _once("bloodsplosion-event", f"Bloodsplosion has no '{BLOODSPLOSION_EVENT}' kill event: cryo kills stay out")
    return False


def _game_time() -> float | None:
    return actors.now() if actors.running_world() is not None else None


def _is(obj: UObject, path: str) -> bool:
    """Is `obj` the object at `path`? A subobject's path name takes ':' after its outer (Bloodsplosion's behaviors are
    "...Bloodsplosion:BehaviorProviderDefinition_0.Behavior_..."), so the separators don't count."""
    return obj.Name == path.rpartition(".")[2] and obj._path_name().replace(":", ".") == path


@hook("WillowGame.Behavior_SpawnParticleSystem:ApplyBehaviorToContext")
def _gore(obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    """A cryo kill's Bloodsplosion: TPS's shatter for its gore, and its blast is due in BLAST_DELAY."""
    if not _is(obj, BLOODSPLOSION_GORE) or not element.recent_cryo_kill(CRYO_KILL_WINDOW):
        return
    now = _game_time()
    if now is not None:
        _cryo_blasts[:] = [due for due in _cryo_blasts if due > now - BLAST_TOLERANCE]
        _cryo_blasts.append(now + BLAST_DELAY)
    if load():
        _swapped.append((obj, "ParticleEffect", obj.ParticleEffect))
        obj.ParticleEffect = _systems[SHATTER]


@hook("WillowGame.Behavior_Explode:ApplyBehaviorToContext")
def _blast(obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    """The blast due for a cryo kill's Bloodsplosion: the cryo master explosion."""
    if not _cryo_blasts or not _is(obj, BLOODSPLOSION_BLAST):
        return
    now = _game_time()
    due = next((d for d in _cryo_blasts if now is not None and abs(now - d) <= BLAST_TOLERANCE), None)
    if due is None:
        return
    _cryo_blasts.remove(due)
    from . import explosions                       # built at setup, before any kill
    ice = explosions.ensure_effects().get(explosions.EXPLOSION)
    if ice is not None:
        _swapped.append((obj, "Definition", obj.Definition))
        obj.Definition = ice
        _once("blast", "first cryo Bloodsplosion blast")


@hook("WillowGame.Behavior_Explode:ApplyBehaviorToContext", Type.POST)
def _blast_done(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    _restore()


@hook("WillowGame.Behavior_SpawnParticleSystem:ApplyBehaviorToContext", Type.POST)
def _gore_done(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    _restore()


def _restore() -> None:
    while _swapped:
        behavior, prop, value = _swapped.pop()
        setattr(behavior, prop, value)


@hook("WillowGame.PlayerSkillTree:Initialize", Type.POST)
def _tree_ready(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    """A skill tree is set up (its skills' points come after): Bloodsplosion is loaded now if a skill mod's set or
    Krieg's tree has it."""
    try:
        _bloodsplosion_cryo()
    except Exception as ex:  # noqa: BLE001 - a skill mod's data; never break a character load
        _once("bloodsplosion-error", f"Bloodsplosion not patched: {type(ex).__name__}: {ex}")


@hook(maps.ARRIVED, Type.POST)
@hook(maps.NEW_WORLD, Type.POST)
def _on_new_map(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    _icicles.clear()
    _cryo_blasts.clear()
    try:
        _bloodsplosion_cryo()
    except Exception as ex:  # noqa: BLE001 - a skill mod's data; never break a map start
        _once("bloodsplosion-error", f"Bloodsplosion not patched: {type(ex).__name__}: {ex}")


HOOKS = (_blast, _gore, _blast_done, _gore_done, _tree_ready, _on_new_map)
