"""Cryo's shot trails and muzzle flashes on E-tech guns (RESEARCH.md §6.60).

An E-tech gun takes its shot's trail from a list, by the gun's element (`EffectCollectionDefinition`: one particle
system per element, each behind a test of the weapon's damage type, as in the explosion lists of guns.py), and its
muzzle flash from another. BL2's lists know fire, shock and corrosive, and end in an entry that asks nothing, which
is slag's. So a cryo E-tech sniper, assault rifle, SMG or shotgun drew slag's purple trail and flash (the user,
2026-10-05: "Etech snipers don't have a white bullet trail, but a purple bullet trail"). The two pistol trail lists
ask for slag by name and have no last entry of that kind: a cryo Spiker or Dart had none of the list's trails. TPS
has no E-tech effects to take (lasers took their place there), so cryo's are made here: a copy of each list's slag
effect with its colours turned white, entered before the entry that asks nothing. The launchers' lists have one
effect for every element and are left.

**Copying a particle system** is where the dev copy went down four times before this was right (2026-10-05). What
the engine does with an object made from a template:
- Fields the engine manages by itself (`native` arrays) are copied byte for byte: the same memory as the template's.
  A LOD level keeps five such lists (which of its modules spawn, which update).
- An emitter made from a template comes with LOD levels of the engine's own making, made the same way: their lists
  sit in the original's memory. Whichever is destroyed first frees that memory under the other, and the game stops
  at its next garbage collection, reading or freeing it again.
- A module made from a template comes without the template's own subobjects. A value that isn't baked into a table
  (a colour a particle parameter gives, on the SMG's and the Spiker's trails) lives in one, a distribution; the
  copy's value then names none and has no table either, and drawing it is an array read out of bounds.
So the system, its emitters and their LOD levels are made plain and filled value by value, each array getting memory
of its own; only the modules, which hold no such list, are made from their templates, and each is then given its
own copy of whatever subobject its values name (`_restore`). Every part of the copy is named by the engine.
"""

from __future__ import annotations

import copy
from typing import Any

import unrealsdk
from unrealsdk.unreal import UObject, WrappedStruct

from . import attributes

PACKAGE = "GD_AureliaCryoFX"
_RF_ROOTSET = 0x4000
_CPF_NATIVE = 0x1000
_UNNAMED = "None"               # the engine then names the object itself, apart from every other under its owner
_OWNING = ("ArrayProperty", "StrProperty", "MapProperty")      # a native one of these owns memory of its own
_NOT_COPIED = ("ComponentProperty", "DelegateProperty", "PointerProperty")
_IS_SHOCK = "WeaponDamage_Is_Shock"
_IS_SLAG = "WeaponDamage_Is_Amp"
# The E-tech trails' and muzzle flashes' packages: the other lists (plain bullets, their flashes, rockets) are left
_ETECH = ("FX_WEP_Trails_Alien.", "FX_WEP_Alien.")
# A LOD level, field by field: its plain values, its single modules, and its lists of modules (the last five are the
# engine's own).
_LOD_VALUES = ("Level", "bEnabled", "ConvertedModules", "PeakActiveParticles")
_LOD_MODULES = ("RequiredModule", "TypeDataModule", "SpawnModule", "EventGenerator")
_LOD_LISTS = ("Modules", "SpawningModules", "SpawnModules", "UpdateModules", "OrbitModules", "EventReceiverModules")
_SYSTEM_SKIPPED = ("Emitters", "PreviewComponent", "CurveEdSetup")
# Where a module keeps a colour (a baked table of r, g, b after its lowest and highest value).
_COLOURS = {"ParticleModuleColor": "StartColor", "ParticleModuleColorOverLife": "ColorOverLife",
            "ParticleModuleColorScaleOverLife": "ColorScaleOverLife"}
WHITE = (0.9, 0.96, 1.0)        # white with a breath of ice in it
GAIN = 1.5                      # a colour's brightness as the eye has it, and half as much again: slag's purple is dark
DARK = 0.05                     # a trail's dark core stays as it is

_fields: dict[str, list[tuple[str, str, int]]] = {}
_said: set[str] = set()


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] cryo trails: {msg}")


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _root(obj: UObject) -> UObject:
    obj.ObjectFlags = int(obj.ObjectFlags) | _RF_ROOTSET
    return obj


def _package() -> UObject:
    return _root(_find("Package", PACKAGE) or unrealsdk.construct_object("Package", None, PACKAGE))


def _own_fields(cls: UObject) -> list[tuple[str, str, int]]:
    """(name, kind, flags) of a class's properties, without the ones every object has."""
    key = cls._path_name()
    if key not in _fields:
        _fields[key] = [(p.Name, p.Class.Name, int(p.PropertyFlags)) for p in cls._properties()
                        if p.Outer is not None and p.Outer.Name != "Object"]
    return _fields[key]


def _owns_memory(cls: UObject) -> list[str]:
    """The class's fields the engine manages itself and that hold memory: a template's are shared, not copied."""
    return [name for name, kind, flags in _own_fields(cls) if flags & _CPF_NATIVE and kind in _OWNING]


def _plain(original: UObject, outer: UObject, skipped: tuple[str, ...], name: str = _UNNAMED) -> UObject:
    """A new object of the original's class, each of its values copied through the SDK."""
    new = _root(unrealsdk.construct_object(original.Class, outer, name))
    for field, kind, _flags in _own_fields(original.Class):
        if field not in skipped and kind not in _NOT_COPIED:
            setattr(new, field, getattr(original, field))
    return new


def _restore_struct(original: UObject, mine: UObject, theirs: Any, ours: Any) -> bool:
    restored = False
    for prop in theirs._type._properties():
        kind = prop.Class.Name
        if kind == "StructProperty":
            restored |= _restore_struct(original, mine, getattr(theirs, prop.Name), getattr(ours, prop.Name))
        elif kind in ("ObjectProperty", "ComponentProperty"):
            sub = getattr(theirs, prop.Name)
            if sub is None or sub.Outer != original or getattr(ours, prop.Name) is not None:
                continue
            held = _owns_memory(sub.Class)
            if held:
                raise RuntimeError(f"{sub.Class.Name} holds {held}: not to be made from a template")
            setattr(ours, prop.Name, _root(unrealsdk.construct_object(sub.Class, mine, _UNNAMED, 0, sub)))
            restored = True
    return restored


def _restore(original: UObject, mine: UObject) -> None:
    """A module made from a template, given its own copy of each subobject of the original that its values name (in
    a struct, or in the structs of an array): the template's are not carried over, and the values are left empty."""
    for field, kind, _flags in _own_fields(original.Class):
        if kind == "StructProperty":
            ours = getattr(mine, field)
            if _restore_struct(original, mine, getattr(original, field), ours):
                setattr(mine, field, ours)
        elif kind == "ArrayProperty":
            for theirs, ours in zip(getattr(original, field), getattr(mine, field)):
                if isinstance(theirs, WrappedStruct):
                    _restore_struct(original, mine, theirs, ours)      # an entry of the copy's own array, in place


def _copy_system(system: UObject, name: str) -> UObject:
    """A particle system like `system` that shares nothing with it but materials, meshes and sounds."""
    new = _plain(system, _package(), _SYSTEM_SKIPPED, name)
    made: dict[str, UObject] = {}               # a module can serve several LOD levels, and several emitters

    def module(original: UObject | None) -> UObject | None:
        if original is None:
            return None
        key = original._path_name()
        if key not in made:
            held = _owns_memory(original.Class)
            if held:
                raise RuntimeError(f"{original.Class.Name} holds {held}: not to be made from a template")
            made[key] = _root(unrealsdk.construct_object(original.Class, new, _UNNAMED, 0, original))
            _restore(original, made[key])
        return made[key]

    emitters = []
    for emitter in system.Emitters:
        if emitter is None:
            raise RuntimeError("an emitter of it is missing")
        mine = _plain(emitter, new, ("LODLevels",))
        levels = []
        for lod in emitter.LODLevels:
            level = _root(unrealsdk.construct_object(lod.Class, mine))
            for field in _LOD_VALUES:
                setattr(level, field, getattr(lod, field))
            for field in _LOD_MODULES:
                setattr(level, field, module(getattr(lod, field)))
            for field in _LOD_LISTS:
                setattr(level, field, [module(m) for m in getattr(lod, field)])
            levels.append(level)
        mine.LODLevels = levels
        emitters.append(mine)
    new.Emitters = emitters
    return new


def _level(r: float, g: float, b: float) -> float:
    return (0.3 * r + 0.59 * g + 0.11 * b) * GAIN


def _whiten_table(dist: Any, scale: bool) -> bool:
    """A baked colour table turned white. Left alone: a table in one channel only (a ramp the material reads as a
    number), one with no colour in it, and dark entries."""
    table = [float(x) for x in dist.LookupTable]
    body = table[2:]
    triples = [(body[i], body[i + 1], body[i + 2]) for i in range(0, len(body) - 2, 3)]
    if not triples or sum(1 for c in range(3) if max(abs(t[c]) for t in triples) > 1e-3) < 2:
        return False
    if all(abs(r - g) < 1e-4 and abs(g - b) < 1e-4 for r, g, b in triples):
        return False
    out: list[float] = []
    for r, g, b in triples:
        if max(r, g, b) <= DARK:
            out += (r, g, b)
        elif scale:                              # a multiplier over a particle's life: evened out, not brightened
            out += [(r + g + b) / 3.0] * 3
        else:
            out += [_level(r, g, b) * tint for tint in WHITE]
    dist.LookupTable = [min(out), max(out), *out]
    return True


def _whiten_parameter(module: UObject, dist: Any) -> bool:
    """A colour a particle parameter gives, with a value for when nobody sets it: that value turned white. Only on the
    copy's own distribution object (the engine made it one with the module)."""
    source = dist.Distribution
    if source is None or source.Outer != module or not hasattr(source, "Constant"):
        return False
    value = source.Constant
    level = _level(float(value.X), float(value.Y), float(value.Z))
    source.Constant = unrealsdk.make_struct("Vector", X=level * WHITE[0], Y=level * WHITE[1], Z=level * WHITE[2])
    return True


def _white_material(material: UObject | None) -> UObject | None:
    """A material instance that is coloured by a parameter, as a copy with that colour white. None: no such colour."""
    if material is None or material.Class.Name != "MaterialInstanceConstant" or material.Parent is None:
        return None
    colours = [(str(v.ParameterName), v.ParameterValue) for v in material.VectorParameterValues
               if "color" in str(v.ParameterName).lower()]
    if not colours:
        return None
    name = f"{material.Name}_Cryo"
    mic = _find("MaterialInstanceConstant", f"{PACKAGE}.{name}")
    if mic is None:
        mic = _root(unrealsdk.construct_object("MaterialInstanceConstant", _package(), name))
    mic.SetParent(material.Parent)
    for value in material.ScalarParameterValues:
        mic.SetScalarParameterValue(str(value.ParameterName), float(value.ParameterValue))
    for value in material.TextureParameterValues:
        mic.SetTextureParameterValue(str(value.ParameterName), value.ParameterValue)
    for value in material.VectorParameterValues:
        mic.SetVectorParameterValue(str(value.ParameterName), copy.copy(value.ParameterValue))
    for param, value in colours:
        level = _level(float(value.R), float(value.G), float(value.B))
        mic.SetVectorParameterValue(param, unrealsdk.make_struct(
            "LinearColor", R=level * WHITE[0], G=level * WHITE[1], B=level * WHITE[2], A=float(value.A)))
    return mic


def _whiten(system: UObject) -> None:
    done: set[str] = set()
    for emitter in system.Emitters:
        for lod in emitter.LODLevels:
            for module in (lod.RequiredModule, *lod.Modules):
                if module is None or module._path_name() in done:
                    continue
                done.add(module._path_name())
                kind = module.Class.Name
                if kind == "ParticleModuleRequired":
                    white = _white_material(module.Material)
                    if white is not None:
                        module.Material = white
                elif kind in _COLOURS:
                    dist = getattr(module, _COLOURS[kind])
                    if _whiten_table(dist, kind.endswith("ScaleOverLife")) or _whiten_parameter(module, dist):
                        setattr(module, _COLOURS[kind], dist)


def _asks(entry: Any) -> str | None:
    """The weapon-damage attribute a list's entry tests; None for the entry that takes whatever is left."""
    evaluator = entry.Expression
    attribute = evaluator.Expression.AttributeOperand1 if evaluator is not None else None
    return attribute.Name if attribute is not None else None


def cryo_trail(slag: UObject) -> UObject:
    """The white copy of a slag trail or flash, made once."""
    name = slag.Name.replace("Slag", "Cryo") if "Slag" in slag.Name else f"{slag.Name}_Cryo"
    system = _find("ParticleSystem", f"{PACKAGE}.{name}")
    if system is None:
        system = _copy_system(slag, name)
        _whiten(system)
    return system


def patch() -> int:
    """A cryo entry in every loaded E-tech trail and flash list that tells the elements apart. Idempotent; returns
    how many it changed."""
    is_ice = attributes.ensure_attributes()[attributes.WEAPON_IS_ICE]
    count = 0
    for coll in unrealsdk.find_all("EffectCollectionDefinition", exact=False):
        if coll.Name.startswith("Default__"):
            continue
        entries = [copy.copy(e) for e in coll.ParticleEffects]
        asked = [_asks(e) for e in entries]
        if is_ice.Name in asked or _IS_SHOCK not in asked:
            continue
        shock = entries[asked.index(_IS_SHOCK)]
        last = asked.index(None) if None in asked else len(entries)
        theirs = entries[asked.index(_IS_SLAG)] if _IS_SLAG in asked else entries[last] if last < len(entries) else None
        slag = theirs.ParticleEffect if theirs is not None else None
        if (slag is None or slag == shock.ParticleEffect or "Slag" not in slag.Name
                or not slag._path_name().startswith(_ETECH)):
            continue                             # one effect for every element (the launchers'), or no E-tech list
        try:
            trail = cryo_trail(slag)
        except Exception as ex:  # noqa: BLE001 - a list without cryo's trail is as it was
            if coll.Name not in _said:
                _said.add(coll.Name)
                _log(f"{coll.Name}: nothing for cryo: {type(ex).__name__}: {ex}")
            continue
        evaluator = unrealsdk.construct_object(shock.Expression.Class, coll, "AttributeExpressionEvaluator_Ice", 0,
                                               shock.Expression)
        _root(evaluator)
        expression = copy.copy(evaluator.Expression)
        expression.AttributeOperand1 = is_ice
        evaluator.Expression = expression
        entry = copy.copy(shock)
        entry.Expression = evaluator
        entry.ParticleEffect = trail
        entries.insert(last, entry)
        coll.ParticleEffects = entries
        count += 1
    if count:
        _log(f"cryo's own added to {count} E-tech trail and muzzle flash lists")
    return count
