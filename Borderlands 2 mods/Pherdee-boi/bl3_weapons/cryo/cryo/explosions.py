"""Her skills' ice explosion and impacts, built at runtime at TPS's paths (M5; RESEARCH.md §6.14 item 5).

Her skill graphs import TPS's shared ice effects, which BL2 doesn't have, and each would load as None. Without them her
skills' explosions lose their definition, and with it the explosion's damage type (an ExplosionDefinition carries
one: BL2's shock master deals shock). Built from BL2's nearest, before her package loads:
- `Explosion_IceMaster`: BL2's `Explosion_ShockMaster` (the same three radius tiers, 0-192-384-1024), with the cryo
  damage type and the ice impacts below. Its particles are TPS's once her build's ice effects load (`dress`); its
  sound stays shock's (TPS's is in TPS's own banks, which her build doesn't carry);
- `ExplosiveImpactIce128/256/512`, and TPS's `ExplosiveImpactShock128`: BL2's neutral `ExplosiveImpactNormal` of each
  size, which BL2's shock master uses too;
- `Bullet_Ice`: BL2's `Bullet_Generic`, as for cryo guns;
- `Explosion_IceMaster_ShieldNova` (the Cryo Nova shield's blast, gear.py): BL2's `Explosion_ShockMaster_ShieldNova`
  with the cryo damage type. Its impacts are BL2's neutral ones already; its particles are TPS's, as above.

`dress` puts TPS's particle systems (tools/icefx.py carries them into Crocus_IceFX_SF) on both, with TPS's tiers: the
radius each covers and how the particles scale over it. Without them (her files not built yet, or built before 0.3.1)
the explosions keep shock's.
"""

from __future__ import annotations

import copy

import unrealsdk
from unrealsdk.unreal import UObject

from . import element

EXPLOSION = "gd_co_effects.Shared.Explosions.Explosion_IceMaster"
NOVA = "gd_co_effects.Shared.Explosions.Explosion_IceMaster_ShieldNova"
ICE_TIERS = ("gd_co_effects.Shared.Impacts.ExplosiveImpactIce128", "gd_co_effects.Shared.Impacts.ExplosiveImpactIce256",
             "gd_co_effects.Shared.Impacts.ExplosiveImpactIce512")
# TPS path -> BL2 template
IMPACTS = {
    ICE_TIERS[0]: "GD_Impacts.ExplosiveImpacts.ExplosiveImpactNormal128",
    ICE_TIERS[1]: "GD_Impacts.ExplosiveImpacts.ExplosiveImpactNormal256",
    ICE_TIERS[2]: "GD_Impacts.ExplosiveImpacts.ExplosiveImpactNormal512",
    "GD_Impacts.ExplosiveImpacts.ExplosiveImpactShock128": "GD_Impacts.ExplosiveImpacts.ExplosiveImpactNormal128",
    "GD_Cork_Impacts.Bullets.Bullet_Ice": "GD_Impacts.Bullets.Bullet_Generic",
}
# TPS's tiers: (particle system in her build's ice effects, MinRadius, MaxRadius, MinParticleScale, MaxParticleScale)
_FX = "Crocus_IceFX.Particles."
TPS_TIERS = {
    EXPLOSION: ((_FX + "Part_IceExplosion_Small", 0, 192, 0.0, 1.0),
                (_FX + "Part_IceExplosion_Medium", 193, 384, 0.6, 1.1),
                (_FX + "Part_IceExplosion_Large", 385, 1024, 0.6, 1.7)),
    NOVA: ((_FX + "Part_Ice_Nova_Shield_Explosion", 0, 192, 0.0, 1.5),
           (_FX + "Part_Ice_Nova_Shield_Explosion", 193, 384, 0.75, 1.5),
           (_FX + "Part_Ice_Nova_Shield_Explosion", 385, 2000, 0.75, 2.0)),
}
# The nova's two expanding rings draw with BL2's own Mat_Stagnant_Buildup (as BL2's shock nova does), which turns
# TPS's near-white (1, 1, 3) a purple wash: a colder colour, as [min, max, rgb, rgb] (bl2-test, 2026-10-03)
NOVA_RINGS = ("ShockNovaLeft", "ShockNovaRight")
NOVA_RING_COLOR = (0.35, 1.2, 3.0)
_SHOCK_MASTER = "GD_Explosions.shock.Explosion_ShockMaster"
_SHOCK_NOVA = "GD_Explosions.shock.Explosion_ShockMaster_ShieldNova"
_RF_ROOTSET = 0x4000
_built: dict[str, UObject] = {}


def _find(path: str) -> UObject | None:
    try:
        return unrealsdk.find_object("Object", path)
    except ValueError:
        return None


def _root(obj: UObject) -> UObject:
    flags = int(obj.ObjectFlags)
    if not flags & _RF_ROOTSET:
        obj.ObjectFlags = flags | _RF_ROOTSET
    return obj


def _package(path: str) -> UObject:
    pkg = _find(path)
    if pkg is None:
        outer, _, name = path.rpartition(".")
        pkg = unrealsdk.construct_object("Package", _package(outer) if outer else None, name)
    return _root(pkg)


def _clone(path: str, template_path: str) -> UObject:
    template = unrealsdk.find_object("Object", template_path)
    group, _, name = path.rpartition(".")
    return _root(unrealsdk.construct_object(template.Class, _package(group), name, 0, template))


def ensure_effects() -> dict[str, UObject]:
    """The ice explosion and impacts, found or built."""
    for path, template in IMPACTS.items():
        _built[path] = _built.get(path) or _find(path) or _clone(path, template)
    master = _built.get(EXPLOSION) or _find(EXPLOSION)
    if master is None:
        master = _clone(EXPLOSION, _SHOCK_MASTER)
        tiers = []
        for tier, impact in zip(master.ExplosionScale, ICE_TIERS):
            tier = copy.copy(tier)
            tier.ImpactEffect = _built[impact]
            tiers.append(tier)
        master.ExplosionScale = tiers
        master.DamageTypeDef = element.ensure_objects()[element.IMPACT]
    _built[EXPLOSION] = master
    nova = _built.get(NOVA) or _find(NOVA)
    if nova is None:
        nova = _clone(NOVA, _SHOCK_NOVA)
        nova.DamageTypeDef = element.ensure_objects()[element.IMPACT]
    _built[NOVA] = nova
    return dict(_built)


def dress() -> list[str]:
    """TPS's particles and tiers on the ice explosions, once her build's ice effects are loaded (frozenfx.load()).
    Idempotent; an explosion whose systems aren't all loaded keeps shock's. Returns what it dressed."""
    dressed = []
    for path, tiers in TPS_TIERS.items():
        explosion = ensure_effects()[path]
        systems = []
        for system_path, *_ in tiers:
            try:
                systems.append(unrealsdk.find_object("ParticleSystem", system_path))
            except ValueError:
                break
        if len(systems) != len(tiers) or len(explosion.ExplosionScale) != len(tiers):
            continue
        new = []
        for scale, system, (_path, low, high, small, big) in zip(explosion.ExplosionScale, systems, tiers):
            scale = copy.copy(scale)
            scale.ExplosionPSTemplate = _root(system)
            scale.MinRadius, scale.MaxRadius = low, high
            scale.MinParticleScale, scale.MaxParticleScale = small, big
            new.append(scale)
        explosion.ExplosionScale = new
        dressed.append(path.rpartition(".")[2])
        if path == NOVA:
            _color_rings(systems[0])
    return dressed


def _color_rings(system: UObject) -> None:
    for emitter in system.Emitters:
        if emitter is None or str(emitter.EmitterName) not in NOVA_RINGS:
            continue
        for lod in emitter.LODLevels:
            for module in (lod.Modules if lod is not None else ()):
                if module is not None and module.Class.Name == "ParticleModuleColor":
                    color = module.StartColor
                    color.LookupTable = [min(NOVA_RING_COLOR), max(NOVA_RING_COLOR), *NOVA_RING_COLOR, *NOVA_RING_COLOR]
                    module.StartColor = color
