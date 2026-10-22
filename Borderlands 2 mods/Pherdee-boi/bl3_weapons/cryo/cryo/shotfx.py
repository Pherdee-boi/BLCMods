"""TPS's cryo shot effects on BL2's cryo guns: muzzle flash, tracer, rocket trail, bullet impact and first-person glow.

BL3 Weapons' cryo gives BL2 TPS's cryo element; its guns shoot with the plain gun's muzzle flash and tracer and have no glow
(cryo/guns.py: "no first-person glow until TPS's ice effects are converted"). TPS has them in its Startup.upk:
- `<Type>_Default_MuzzleFlashes`, `Default_Tracers` and `Default_Elemental_Rocket_Trails` (EffectCollectionDefinition)
  are lists of (test of the weapon's damage type, particle system); TPS's lists have an ice entry where BL2's have slag
  or nothing. Here the same entry is added to BL2's lists, built like aurelia's explosion entries
  (guns.patch_explosions): a copy of an element's entry with its test turned to `WeaponDamage_Is_Ice`, ahead of the
  list's last (untested) entry.
- the glow in front of a gun's barrel is the elemental part's `FirstPersonParticle` (a particle component): each cryo
  part gets a component of its own whose template is TPS's `Part_Ice_Elemental_<Type>`.
The particle systems are CryoShotFX_SF.upk (convert/shotfx.py, built by builder.py from the player's own TPS install like the ice effects); their
TPS-only materials are drawn at runtime by core/particles.py. Until the Cryo Shot FX mod was folded in (2026-10-22) this was a mod of its own: when that
mod is still installed beside this one, this stands aside (`_separate_mod`), so the muzzle flash offsets are not scaled twice.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base import SETTINGS_DIR, hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import UObject

from ..core import maps, particles
from . import attributes, guns
from . import shotfx_emitters as emitters
from . import shotfx_looks as looks

PACKAGE = "CryoShotFX_SF"
TOP = "CryoShotFX.Particles."
_RF_ROOTSET = 0x4000
MUZZLE_FLASHES = {
    "GD_Weap_AssaultRifle.Effects.AR_Default_MuzzleFlashes": "Part_Assault_MuzzleFlash_Ice",
    "GD_Weap_Launchers.Effects.L_Default_MuzzleFlashes": "Part_RL_MuzzleFlash_Ice",
    "GD_Weap_Pistol.Effects.Pistol_Default_MuzzleFlashes": "Part_Pistol_MuzzleFlash_Ice",
    "GD_Weap_Shotgun.Effects.SG_Default_MuzzleFlashes": "Part_SG_MuzzleFlash_Ice",
    "GD_Weap_SMG.Effects.SMG_Default_MuzzleFlashes": "Part_SMG_MuzzleFlash_Ice",
    "GD_Weap_SniperRifles.Effects.SR_Default_MuzzleFlashes": "Part_SR_MuzzleFlash_Ice",
}
TRAILS = {
    "GD_Weap_Shared_Effects.Default_Tracers": "Part_Wep_Tracer_Ice",
    "GD_Weap_Shared_Effects.Default_Elemental_Rocket_Trails": "Part_InFlight_Ice",
}
GLOWS = {"Pistol": "Part_Ice_Elemental_Pistol", "SMG": "Part_Ice_Elemental_SMG", "AR": "Part_Ice_Elemental_AR",
         "Shotgun": "Part_Ice_Elemental_SG", "SR": "Part_Ice_Elemental_Sniper"}      # TPS has none for launchers
# emitters whose TPS material has no BL2 twin to import and no look to draw: BL2's material in its place
BORROWED = {TOP + "Part_Ice_Trail_Fast": {1: "FX_WEP_Trails.Materials.Instances.Default.Mati_Default_Bullet_Trail"}}
# entries of a list that an ice entry can be a copy of, best first (their tests are turned to ice)
TEMPLATES = ("WeaponDamage_Is_Amp", "WeaponDamage_Is_Shock")
# the bullet impact: BL2's shock impact (a fallback effect: one particle system) with TPS's ice system in it
IMPACT_FROM = "GD_Impacts.Bullets.Bullet_Shock"
IMPACT_NAME = "Impact_Cryo_Bullet"
IMPACT_SYSTEM = "Part_Ice_ImpactFX"

# TPS's muzzle flashes put their cross-flare sprites (the "Location" emitters) 10-20 units ahead of the muzzle socket, which TPS's guns' barrels suit; BL2's
# barrels end sooner (a Maliwan SMG's Tediore or Hellfire barrel), so the flare floated in front of the gun. Their offsets are scaled down by this (2026-10-21).
FLASH_PULL = 0.25

_systems: dict[str, UObject] = {}
_said: set[str] = set()
_pulled: set[str] = set()
_pulled_modules: set[int] = set()  # (a module object can be shared by several emitters: scaled once)


def log(msg: str) -> None:
    unrealsdk.logging.info(f"[CryoShotFX] {msg}")


def _once(key: str, msg: str) -> None:
    if key not in _said:
        _said.add(key)
        log(msg)


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def load() -> bool:
    """Load the package (absent before a build), keep its systems, and give their emitters their materials."""
    if _systems:
        return True
    if _find("Package", PACKAGE) is None:
        try:
            unrealsdk.load_package(PACKAGE)
        except Exception as ex:  # noqa: BLE001 - no such file yet
            _once("nopackage", f"{PACKAGE} not loaded ({type(ex).__name__}: {ex}): build it with build_shotfx.py")
            return False
    found: dict[str, UObject] = {}
    for path in emitters.EMITTERS:
        system = _find("ParticleSystem", path)
        if system is None:
            _once("missing", f"no {path}: {PACKAGE} is older than this mod, or didn't load")
            return False
        system.ObjectFlags = int(system.ObjectFlags) | _RF_ROOTSET
        found[path] = system
    _systems.update(found)
    particles.EMITTERS.update(emitters.EMITTERS)
    looks.register()
    _borrow()
    particles.start()                       # materials: what is compiled already now, the rest over the next ticks
    try:
        _pull_flashes_in()                  # after the start: a failure here must not cost the other effects their materials
    except Exception as ex:  # noqa: BLE001
        _once("pull", f"muzzle flashes left as TPS has them ({type(ex).__name__}: {ex})")
    log(f"{len(_systems)} cryo shot effects loaded")
    return True


def _pull_flashes_in() -> None:
    """Once per muzzle flash system: scale the forward offset of its sprite emitters (a ParticleModuleLocation, not the sphere ones) by FLASH_PULL."""
    for name in MUZZLE_FLASHES.values():
        path = TOP + name
        system = _systems.get(path)
        if system is None or path in _pulled:
            continue
        _pulled.add(path)
        for emitter in system.Emitters:
            lod = emitter.LODLevels[0] if len(emitter.LODLevels) else None
            for module in (lod.Modules if lod is not None else []):
                if module.Class.Name.endswith("ParticleModuleLocation") and module._get_address() not in _pulled_modules:
                    _pulled_modules.add(module._get_address())
                    table = [float(x) for x in module.StartLocation.LookupTable]
                    module.StartLocation.LookupTable = [x * FLASH_PULL for x in table]


def _borrow() -> None:
    for path, by_emitter in BORROWED.items():
        system = _systems.get(path)
        for index, material_path in by_emitter.items():
            material = _find("MaterialInstanceConstant", material_path) or _find("Material", material_path)
            if system is None or material is None:
                _once(f"borrow:{path}", f"no {material_path} to lend {path}[{index}]")
                continue
            for lod in system.Emitters[index].LODLevels:
                if lod is not None and lod.RequiredModule is not None:
                    lod.RequiredModule.Material = material


# ----------------------------------------------------------------------------------------------------- lists
def _asks(entry: Any) -> str | None:
    """The weapon-damage attribute a list entry tests (None for the untested last entry)."""
    evaluator = entry.Expression
    attribute = evaluator.Expression.AttributeOperand1 if evaluator is not None else None
    return attribute.Name if attribute is not None else None


def _patch_list(path: str, system_name: str, is_ice: UObject) -> str:
    coll = _find("EffectCollectionDefinition", path)
    system = _systems.get(TOP + system_name)
    if coll is None or system is None:
        return "not loaded"
    entries = [copy.copy(e) for e in coll.ParticleEffects]
    asked = [_asks(e) for e in entries]
    if is_ice.Name in asked:
        return "has cryo"
    source = next((asked.index(name) for name in TEMPLATES if name in asked), None)
    if source is None:
        return "no element entry to copy"
    template = entries[source]
    evaluator = unrealsdk.construct_object(template.Expression.Class, coll, "AttributeExpressionEvaluator_Ice", 0,
                                           template.Expression)
    evaluator.ObjectFlags = int(evaluator.ObjectFlags) | _RF_ROOTSET
    expression = copy.copy(evaluator.Expression)
    expression.AttributeOperand1 = is_ice
    evaluator.Expression = expression
    entry = copy.copy(template)
    entry.Expression = evaluator
    entry.ParticleEffect = system
    last = len(entries) - 1 if asked and asked[-1] is None else len(entries)       # ahead of the untested entry
    coll.ParticleEffects = [*entries[:last], entry, *entries[last:]]
    return "added"


def patch_lists() -> list[str]:
    is_ice = attributes.ensure_attributes()[attributes.WEAPON_IS_ICE]
    out = []
    for path, name in {**MUZZLE_FLASHES, **TRAILS}.items():
        try:
            out.append(f"{path.rpartition('.')[2]}: {_patch_list(path, name, is_ice)}")
        except Exception as ex:  # noqa: BLE001 - one list never stops the others
            out.append(f"{path.rpartition('.')[2]}: failed ({type(ex).__name__}: {ex})")
    return out


# ---------------------------------------------------------------------------------------------------- glow
def _impact() -> UObject | None:
    """The cryo bullet impact (TPS's GD_Cork_Impacts.Bullets.Bullet_Ice): made once, from shock's, rooted."""
    system = _systems.get(TOP + IMPACT_SYSTEM)
    template = _find("WillowImpactDefinition", IMPACT_FROM)
    if system is None or template is None:
        return None
    package = _find("Package", "CryoShotFX")
    impact = _find("WillowImpactDefinition", f"CryoShotFX.{IMPACT_NAME}")
    if impact is None:
        impact = unrealsdk.construct_object("WillowImpactDefinition", package, IMPACT_NAME, 0, template)
    impact.ObjectFlags = int(impact.ObjectFlags) | _RF_ROOTSET
    effect = copy.copy(impact.FallbackEffect)
    if effect.ParticleTemplate != system:
        effect.ParticleTemplate = system
        impact.FallbackEffect = effect
    return impact


def dress_parts() -> list[str]:
    """Each cryo part: TPS's bullet impact, and its first-person glow (a component of its own, made like shock's, with
    TPS's ice system as template)."""
    out = []
    parts = dict(guns._parts)         # aurelia builds them in its own setup (before a save is read): not here
    if not parts:
        return out
    twins = {mine.Name: shock for mine, shock in guns._copies}          # cryo part -> the shock part it copies
    impact = _impact()
    # shock's glow component: some of the shock parts have none to read, so every part is made from the first found
    sample = next((c for c in (twins[p.Name].FirstPersonParticle for p in parts.values()) if c is not None), None)
    for kind, part in parts.items():
        if impact is not None and part.WeaponImpact != impact:
            part.WeaponImpact = impact
            out.append(f"{kind}: impact set")
        name = GLOWS.get(kind)
        system = _systems.get(TOP + name) if name else None
        if system is None or sample is None:
            continue
        current = part.FirstPersonParticle
        if current is not None and current.Template == system:
            continue
        component = unrealsdk.construct_object(sample.Class, part, "FirstPersonParticle_Cryo", 0, sample)
        component.ObjectFlags = int(component.ObjectFlags) | _RF_ROOTSET
        component.Template = system
        part.FirstPersonParticle = component
        out.append(f"{kind}: glow set")
    return out


def _separate_mod() -> bool:
    """Is the Cryo Shot FX mod installed beside this one (and not switched off)? It does the same, so one of them must stand aside."""
    if not (SETTINGS_DIR.parent / "cryo_shot_fx" / "__init__.py").is_file():
        return False
    try:
        import json  # noqa: PLC0415

        return json.loads((SETTINGS_DIR / "cryo_shot_fx.json").read_text(encoding="utf-8")).get("enabled") is not False
    except (OSError, ValueError):
        return True


def register() -> None:
    """At each map start (and once at the main menu): everything idempotent, each step on its own."""
    if _separate_mod():
        _once("separate", "the Cryo Shot FX mod is installed: it keeps the shot effects (remove it to use these)")
        return
    if not load():
        return
    for label, step in (("lists", patch_lists), ("glow", dress_parts)):
        try:
            lines = step()
        except Exception as ex:  # noqa: BLE001 - looks; never block the rest
            _once(f"{label}-error", f"{label} not applied: {type(ex).__name__}: {ex}")
            continue
        for line in lines:
            _once(f"{label}:{line}", f"{label}: {line}")


@hook(maps.ARRIVED, Type.POST)
@hook(maps.NEW_WORLD, Type.POST)
def _on_map(*_: object) -> None:
    try:
        register()
    except Exception as ex:  # noqa: BLE001 - looks; never take the map down
        _once("register-error", f"not applied: {type(ex).__name__}: {ex}")


HOOKS = [_on_map]
