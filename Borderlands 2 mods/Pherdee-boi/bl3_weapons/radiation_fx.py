"""Radiation's death blast: a yellow gas cloud, made from BL2's slag explosion while it plays.

How BL2 draws an explosion matters here (all of it found by testing, 2026-10-15; see also art/ASSETS.md):
- When a particle system has a PhysX variant (`PhysxParticleSystemRef`), the game draws THAT one: the variant under
  FX_WEP_Explosions_Fluid_Px.Particles.corrosive (with a fluid emitter) or .NOFLD (without). The plain system's emitters are never drawn, so
  edits to the plain system (and copies of it) show nothing.
- Those variants' colour tables (ColorOverLife and its kin) do set the colour, but they are read with the channels rotated: a table triple
  (a, b, c) comes out as red = c, green = a, blue = b. So yellow (r, g, b) is written as (g, b, r).
- Slag's smoke materials have purple baked in (it cannot be tinted to yellow), so while a radiation blast plays they are swapped for BL2's own
  neutral smoke material instances (which take their colour from the tables), and slag's purple puddle (the explosion's impact effect) is cleared.
- Material instances made at run time, packages with copies of these systems and run-time clones of the systems all failed (nothing drawn, solid
  squares, or a crash), so nothing is created: the real objects are edited for a few seconds and put back.

The price: a slag explosion that goes off in those few seconds (a Maliwan slag gun, a slag grenade) is yellow too.
Bubbles (2026-10-15): the texloader mod loads art/radiation_bubble.png as a real texture, and that texture is put into the opacity slot (Opc_GP)
of an existing smoke material instance with SetTextureParameterValue - an existing material instance draws it, one made at run time does not.
The slag emitters that used "Mat_SlagSmokeVariant2" draw that material, and are made many, small, and long-lived; without texloader they stay gas.
"""

import traceback
from pathlib import Path

import unrealsdk
from unrealsdk.unreal import UObject

from .core import log, now

PX = "FX_WEP_Explosions_Fluid_Px.Particles"
SYSTEMS = [f"{PX}.{variant}.Part_WEP_SlagExplosion{size}" for variant in ("NOFLD", "corrosive") for size in ("Small", "Medium", "Large")]
EXPLOSION = "GD_Explosions.Slag.Explosion_SlagMaster"
PUFF = "FX_Shared_Smoke.Materials.Mati_GenericSmokePuff_NoWorldLight"  # BL2's neutral smoke material instances
SWAP = {"Mat_SlagSmokeVariant2": PUFF, "Mat_SlagExplosionSmoke": PUFF, "Mat_Trans_Smoke": PUFF}
# The flipbook smoke emitters draw a 2x2 sprite sheet whose cell edges showed as hard-edged faint squares behind the gas and bubbles (found 2026-10-15
# by switching each emitter group off in turn: with them off the squares are gone and the gas is still a full cloud): they are switched off.
OFF_MATERIALS = ("Mat_SmokeSlagFlipBook",)
COLOURS = {"ParticleModuleColor": "StartColor", "ParticleModuleColorOverLife": "ColorOverLife", "ParticleModuleColorScaleOverLife": "ColorScaleOverLife"}
GAS_MATERIALS = ("Mat_SlagExplosionSmoke", "Mat_Trans_Smoke")  # the emitters of the gas cloud itself (by their original material)
GAS_ALPHA = 0.5  # how much of its opacity the cloud keeps (their AlphaOverLife is scaled by it)
BUBBLE_MAT = "FX_Shared_Smoke.Materials.Generic_Smoke_Puff_NoToD_Mati"  # another neutral smoke material instance: its opacity art is borrowed
BUBBLE_SLOT = "Opc_GP"
BUBBLE_PNG = Path(__file__).parent / "art" / "radiation_bubble.png"
GAS_PNG = Path(__file__).parent / "art" / "radiation_gas.png"  # a soft round puff for the gas (the stock cloud texture's edges showed as faint squares)
BUBBLE_EMITTERS = "Mat_SlagSmokeVariant2"  # the emitters (by material name) whose puffs become bubbles
BUBBLE_SIZE = (2.5, 6.0)  # start size range (the emitters grow it up to 2x)
BUBBLE_COUNT = 24  # bubbles from an emitter that spreads them (the others keep their count)
BUBBLE_RADIUS = 130.0  # they start in a sphere of this radius
BUBBLE_LIFE = 2.6  # seconds
BUBBLE_MODE = 0  # the order the colour channels are written in for the bubble emitters (_rotate): 0 reads yellow, 1 teal, 2 pink
BUBBLE_RISE = 40.0  # the speed (units/s) the bubbles leave their sphere at: the sphere is the upper half, so they go up and out
BUBBLE_GLOW = 3.0  # their brightness (slag's own is about 75, which is white; 1 is faint)
HOLD = 12.0  # seconds the effects stay edited after the last blast (the longest particles live ~5.5 s; a restore sooner brought the purple flames back, 2026-10-15)

_saved: dict = {"materials": [], "tables": [], "impacts": [], "slots": [], "bursts": [], "lods": []}
_painted = False
_until = 0.0


def _rotate(table: list[float], rgb: tuple[float, float, float], mode: int = 0, unit: bool = False) -> list[float]:
    """The same brightness (the tables hold HDR values) in the colour `rgb`, written the way the game reads it (see the top; `mode` 1 and 2: the
    other two orders of the channels; `unit`: the brightness BUBBLE_GLOW instead of the table's HDR one)."""
    out = list(table)
    top = max(rgb)
    order = ((1, 2, 0), (0, 1, 2), (2, 0, 1))[mode]
    for i in range(0, len(out) - 2, 3):
        k = BUBBLE_GLOW / top if unit else max(out[i], out[i + 1], out[i + 2]) / top
        out[i], out[i + 1], out[i + 2] = rgb[order[0]] * k, rgb[order[1]] * k, rgb[order[2]] * k
    return out


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _borrow(material_path: str, png: Path, name: str) -> UObject | None:
    """The smoke material instance `material_path` with its opacity slot holding the art `png` (loaded by the texloader mod) until restore();
    None when texloader or the art is missing (the material is then left as it is)."""
    try:
        from texloader import api  # noqa: PLC0415 (an optional library: without it the blast stays plain gas)

        name += str(int(png.stat().st_mtime)) if png.exists() else ""  # new art (the player's own, a new version) is a new texture, not the old one still loaded
        texture = _find("Texture2D", f"Transient.{name}")
        if texture is None and png.exists():
            texture = _find("Texture2D", api.load(str(png), name))
        material = _find("MaterialInstanceConstant", material_path)
        if texture is None or material is None:
            return None
        slot = next((t for t in material.TextureParameterValues if str(t.ParameterName) == BUBBLE_SLOT), None)
        if slot is None:
            return None
        _saved["slots"].append((material, slot.ParameterValue))
        material.SetTextureParameterValue(BUBBLE_SLOT, texture)
        return material
    except Exception:  # noqa: BLE001
        log(f"radiation blast: could not borrow {name}: " + traceback.format_exc()[-300:])
        return None


def _set_table(mod: UObject, field: str, table: list[float]) -> None:
    value = getattr(mod, field)
    _saved["tables"].append((mod, field, list(value.LookupTable)))
    value.LookupTable = table
    setattr(mod, field, value)


def _bubbles(lod: UObject, seen: set[int]) -> None:
    """Make one emitter's few big puffs many small bubbles."""
    spreads = any(m.Class.Name == "ParticleModuleLocationPrimitiveSphere" for m in lod.Modules)
    for mod in lod.Modules:
        kind = mod.Class.Name
        if mod._get_address() in seen or kind not in ("ParticleModuleSize", "ParticleModuleLifetime", "ParticleModuleLocationPrimitiveSphere"):
            continue
        seen.add(mod._get_address())
        if kind == "ParticleModuleSize":
            table = list(mod.StartSize.LookupTable)
            _set_table(mod, "StartSize", [BUBBLE_SIZE[0] if i % 6 < 3 else BUBBLE_SIZE[1] for i in range(len(table))])
        elif kind == "ParticleModuleLifetime":
            table = list(mod.Lifetime.LookupTable)
            _set_table(mod, "Lifetime", [BUBBLE_LIFE * (0.7 if i % 2 else 1.0) for i in range(len(table))])
        elif kind == "ParticleModuleLocationPrimitiveSphere":
            _set_table(mod, "StartRadius", [BUBBLE_RADIUS] * len(list(mod.StartRadius.LookupTable)))
            _set_table(mod, "VelocityScale", [BUBBLE_RISE] * len(list(mod.VelocityScale.LookupTable)))
    spawn = lod.SpawnModule
    if spawn is not None and spreads and spawn._get_address() not in seen:
        seen.add(spawn._get_address())
        for index, burst in enumerate(spawn.BurstList):
            _saved["bursts"].append((spawn, index, burst.Count, burst.CountLow))
            spawn.BurstList[index].Count = BUBBLE_COUNT
            spawn.BurstList[index].CountLow = -1


def _no_spawn(lod: UObject, seen: set[int]) -> None:
    """An emitter level that spawns nothing: its rate zero and its bursts empty (each detail level has its own spawn module)."""
    spawn = lod.SpawnModule
    if spawn is None or spawn._get_address() in seen:
        return
    seen.add(spawn._get_address())
    _set_table(spawn, "Rate", [0.0] * len(list(spawn.Rate.LookupTable)))
    for index, burst in enumerate(spawn.BurstList):
        _saved["bursts"].append((spawn, index, burst.Count, burst.CountLow))
        spawn.BurstList[index].Count = 0
        spawn.BurstList[index].CountLow = 0


def _edit(rgb: tuple[float, float, float]) -> None:
    materials = {name: _find("MaterialInstanceConstant", path) for name, path in SWAP.items()}
    bubble = _borrow(BUBBLE_MAT, BUBBLE_PNG, "RadBubble")
    _borrow(PUFF, GAS_PNG, "RadGas")  # the gas puffs: soft round, so no faint square edges
    if bubble is not None:
        materials[BUBBLE_EMITTERS] = bubble
    faded: set[int] = set()  # the gas modules whose opacity is scaled (their colour is edited too, below, so they are not in `seen` for that)
    seen: set[int] = set()  # an emitter's detail levels share their module objects: each is edited (and saved) once
    for path in SYSTEMS:
        system = _find("ParticleSystem", path)
        if system is None:
            continue
        for emitter in system.Emitters:
            for lod in emitter.LODLevels:
                module = lod.RequiredModule
                if module is not None and module.Material is not None and module.Material.Name in OFF_MATERIALS:
                    _saved["lods"].append((lod, lod.bEnabled))
                    lod.bEnabled = False
                    _no_spawn(lod, seen)  # (the engine reuses built particle instances, so the LOD switch alone let the flames back on later blasts)
                    continue
                lod_is_bubble = bubble is not None and module is not None and module.Material is not None and (
                    module.Material.Name == BUBBLE_EMITTERS or module.Material._get_address() == bubble._get_address())
                gas = module is not None and module.Material is not None and module.Material.Name in GAS_MATERIALS
                if module is not None and module._get_address() not in seen:
                    seen.add(module._get_address())
                    material = module.Material
                    new = materials.get(material.Name) if material is not None else None
                    if new is not None:
                        _saved["materials"].append((module, material))
                        module.Material = new
                        if new is bubble:
                            _bubbles(lod, seen)
                if gas:
                    for mod in lod.Modules:
                        if mod.Class.Name == "ParticleModuleColorOverLife" and mod._get_address() not in faded:
                            faded.add(mod._get_address())
                            _set_table(mod, "AlphaOverLife", [x * GAS_ALPHA for x in mod.AlphaOverLife.LookupTable])
                for mod in lod.Modules:
                    field = COLOURS.get(mod.Class.Name)
                    if field and mod._get_address() not in seen:
                        seen.add(mod._get_address())
                        value = getattr(mod, field)
                        table = list(value.LookupTable)
                        if len(table) >= 3:
                            _saved["tables"].append((mod, field, table))
                            if lod_is_bubble and field == "ColorScaleOverLife":
                                value.LookupTable = [1.0] * len(table)  # slag's purple tint would be multiplied into the colour
                            else:
                                value.LookupTable = _rotate(table, rgb, BUBBLE_MODE if lod_is_bubble else 0, unit=lod_is_bubble)
                            setattr(mod, field, value)
    boom = _find("ExplosionDefinition", EXPLOSION)
    if boom is not None:
        for index, scale in enumerate(boom.ExplosionScale):
            _saved["impacts"].append((index, scale.ImpactEffect))
            boom.ExplosionScale[index].ImpactEffect = None  # slag's purple puddle


def restore() -> None:
    """Put the slag effects back as the game made them."""
    global _painted, _until
    _painted, _until = False, 0.0
    if any(_saved.values()):
        log(f"radiation blast: restoring {len(_saved['materials'])} materials, {len(_saved['tables'])} tables, {len(_saved['impacts'])} impact effects, {len(_saved['bursts'])} bursts")
    try:
        for module, material in _saved["materials"]:
            module.Material = material
        for mod, field, table in _saved["tables"]:
            value = getattr(mod, field)
            value.LookupTable = table
            setattr(mod, field, value)
        for lod, was in _saved["lods"]:
            lod.bEnabled = was
        for spawn, index, count, low in _saved["bursts"]:
            spawn.BurstList[index].Count = count
            spawn.BurstList[index].CountLow = low
        for material, texture in _saved["slots"]:
            material.SetTextureParameterValue(BUBBLE_SLOT, texture)
        if _saved["impacts"]:
            boom = _find("ExplosionDefinition", EXPLOSION)
            if boom is not None:
                for index, effect in _saved["impacts"]:
                    boom.ExplosionScale[index].ImpactEffect = effect
    except Exception:  # noqa: BLE001
        log("radiation blast restore failed: " + traceback.format_exc()[-300:])
    for key in _saved:
        _saved[key] = []


def forget() -> None:
    """Let go of every edit WITHOUT writing to the objects: after a map change they may be gone (the wrappers keep raw pointers; writing to a freed
    particle module crashed the game, 2026-10-15). A reloaded map makes them new, as the game has them."""
    global _painted, _until
    _painted, _until = False, 0.0
    for key in _saved:
        _saved[key] = []


def _pc():
    from mods_base import get_pc  # noqa: PLC0415
    return get_pc()


def _radfx():
    """The Cryo mod's radiation effects (its own package of copies, nothing of the game edited), when it is installed and they are ready."""
    import sys  # noqa: PLC0415
    module = sys.modules.get(f"{__package__}.cryo.cryo.radfx")
    return module if module is not None and module.ready("medium") else None


def _installed() -> bool:
    """The Cryo mod's radiation effects are there (ready or not): then they are THE effect, and while they aren't ready nothing is drawn. The old way
    (slag's own explosion, edited) draws slag's PhysX liquid and is only for a game without the Cryo mod."""
    import sys  # noqa: PLC0415
    return sys.modules.get(f"{__package__}.cryo.cryo.radfx") is not None


def play(rgb: tuple[float, float, float], *args, sound_on: UObject | None = None) -> bool:
    """Play the blast in `rgb`; `args` are ExplosionDefinition.PlayExplosion's arguments. False when slag's explosion is not there."""
    global _painted, _until
    own = _radfx()
    if own is not None:
        try:
            radius = float(args[1])
            if own.spawn(_pc(), args[6], "medium" if radius <= 600 else "large", None, sound_on if sound_on is not None else args[0]):
                return True
        except Exception:  # noqa: BLE001
            log("radiation blast (own package) failed: " + traceback.format_exc()[-300:])
    if _installed():
        return True          # not ready yet (its materials are still compiling): no blast look rather than slag's liquid
    boom = _find("ExplosionDefinition", EXPLOSION)
    if boom is None:
        return False
    try:
        if not _painted:
            _painted = True  # before editing: a half-done edit is still undone by restore()
            _edit(rgb)
            log(f"radiation blast: edited {len(_saved['materials'])} materials and {len(_saved['tables'])} tables of the slag explosion")
        _until = now() + HOLD
        boom.PlayExplosion(*args)
        return True
    except Exception:  # noqa: BLE001
        log("radiation blast failed: " + traceback.format_exc()[-300:])
        restore()
        return False


PUFF_SYSTEM = "FX_WEP_Explosions.Particles.Slag.Part_WEP_SlagExplosionMedium"  # the plain system, as the game plays it: it picks the PhysX variant edited above (spawning the variant itself drew a flat purple star)
PUFF_PITCH = 0  # the emitter's X axis points up, as an explosion's does along its surface normal (flat, the gas lay on the ground as a purple star)
PUFF_SIZE = "small"  # the explosion size played on an irradiated enemy (small, medium or large)
PUFF_LIFT = 10.0  # units above the enemy's feet the puff starts at


def puff(pc: UObject, pawn: UObject, rgb: tuple[float, float, float], attach: bool = True) -> bool:
    """A burst of the yellow gas and bubbles on an irradiated enemy (the particle system alone: no explosion, no sound), following it."""
    global _painted, _until
    own = _radfx()
    if own is not None:
        try:
            loc = pawn.Location
            try:
                feet = float(pawn.CylinderComponent.CollisionHeight)  # an actor's origin is its middle: the bubbles come up from the ground it stands on
            except Exception:  # noqa: BLE001
                feet = 60.0
            return own.spawn(pc, unrealsdk.make_struct("Vector", X=loc.X, Y=loc.Y, Z=loc.Z - feet + PUFF_LIFT), PUFF_SIZE, pawn if attach else None)
        except Exception:  # noqa: BLE001
            log("radiation puff (own package) failed: " + traceback.format_exc()[-300:])
    if _installed():
        return False
    try:
        system = _find("ParticleSystem", PUFF_SYSTEM)
        world = pc.WorldInfo
        pool = world.MyEmitterPool if world is not None else None
        if system is None or pool is None:
            return False
        if not _painted:
            _painted = True
            _edit(rgb)
        _until = now() + HOLD
        loc = pawn.Location
        pool.SpawnEmitter(system, unrealsdk.make_struct("Vector", X=loc.X, Y=loc.Y, Z=loc.Z + PUFF_LIFT), unrealsdk.make_struct("Rotator", Pitch=PUFF_PITCH, Yaw=0, Roll=0), pawn if attach else None)
        return True
    except Exception:  # noqa: BLE001
        log("radiation puff failed: " + traceback.format_exc()[-300:])
        return False


def spot(pc: UObject, at: tuple[float, float, float], size: str = "small") -> bool:
    """One puff of smoke and bubbles standing in the world at `at` (the cloud a dead irradiated enemy leaves). Nothing without the Cryo mod's effects."""
    own = _radfx()
    if own is None:
        return False
    try:
        return own.spawn(pc, unrealsdk.make_struct("Vector", X=at[0], Y=at[1], Z=at[2] + PUFF_LIFT), size, None)
    except Exception:  # noqa: BLE001
        log("radiation cloud puff failed: " + traceback.format_exc()[-300:])
        return False


def tick() -> None:
    if _until and now() > _until:
        restore()


def reset() -> None:
    restore()
