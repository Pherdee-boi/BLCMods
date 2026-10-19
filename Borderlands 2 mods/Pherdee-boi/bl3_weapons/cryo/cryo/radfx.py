"""The radiation element's gas and bubbles (bl3_weapons' radiation.py draws its death blast and the cloud on an irradiated enemy with these).

Until 0.54.x the effect was BL2's own slag explosion, edited while it played and put back after (radiation_fx.py): a slag grenade in those seconds was
yellow too, and writing to the game's objects once crashed the game (the map was gone when they were put back). Here the effect is the mod's own:
`RadiationFX_SF.upk` (convert/radfx.py, cut from BL2's Startup.upk at build time) holds copies of the three NOFLD slag explosions. The copies are
dressed here, once, and nothing of the game is touched:

- the gas emitters' and the bubble emitters' materials are the mod's (a look of the translucent sprite material, particles.py), with the pictures
  `radiation_gas.png` and `radiation_bubble.png` (bl3_weapons/art, else this mod's art/) as their opacity, and the colour a tint;
- the slag explosion's other emitters (the flipbook smoke that drew squares, the flash, the ring, the sparks) are left off;
- the bubbles are made small, many and long-lived, and sent up; the gas is made see-through.

`spawn()` plays one at a place; it says False until the package is loaded and the materials have compiled (the caller then uses its own fallback).
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

import unrealsdk

from mods_base import hook
from unrealsdk.hooks import Type

from ..core import maps, particles

PACKAGE = "RadiationFX_SF"
TOP = "RadiationFX"
SIZES = {"small": "Small", "medium": "Medium", "large": "Large"}
SYSTEM = f"{TOP}.Particles.Part_WEP_SlagExplosion" + "{}"
GAS = ("Mat_SlagExplosionSmoke", "Mat_Trans_Smoke", "Mati_Smoke_Generic_SubUV")
SPRAY = ("Mati_Dirtkick_NoWorldLight", "Mat_AcidSprayGoop")                  # the bullet impact's specks and goop: they become yellow bubbles
IMPACT_SCALE = 0.3                                                          # the bullet impact is small: its bubbles are this much of that (2026-10-18: they were gigantic)
IMPACT_GAS_SCALE = 1.5                                                      # and its gas puff (a tiny blast)
IMPACT_GAS_LIFE = 2.0                                                       # how much longer the impact's puff hangs there (a wall or ground hit felt empty)
SPRAY_SCALE = 0.6                                                           # their size, of the specks' and goop's own            # the gas emitters (by the material they were made with)
BUBBLES = "Mat_SlagSmokeVariant2"
FLASH = ("Mati_ExplosionGlowMODDepthBias", "Mati_Distortion_Ring_2_Add", "Mati_Explosive_Spikes")   # the blast's flash and ring (the sizes that keep them: Medium and Large)
FLASH_ALPHA = 0.2                                              # slag's flash opacity runs to 5 and 10: far too much for an additive sprite                              # the bubble emitters
COLOUR = (1.0, 0.97, 0.1)                                   # #D6E719, as bright as it goes: the material's tint
IMPACT_ALPHA = 6.0                                             # the bullet impact's smoke is faint to begin with (alpha to 0.3)
GAS_BOOST = 1.5                                                # the gas material's brightness of opacity (its art peaks at 0.7)
GAS_ALPHA = 0.5                                                # how much of its opacity the gas keeps
BUBBLE_SIZE = (5.5, 8.0)                                      # start size range
BUBBLE_RATE = 8.0                                             # bubbles a second from the emitter that spreads them, for BUBBLE_STREAM seconds (not one burst)
BUBBLE_STREAM = 1.8                                            # s the stream lasts
BUBBLE_RADIUS_SCALE = 2.3                                     # of slag's own sphere (50 / 100 / 155 units): small, so they start low and close
BUBBLE_RISE = (25.0, 55.0)                                      # units/s upward, at the start and at the end of a bubble's life (it speeds up slowly); sideways it only drifts
BUBBLE_FADE = (0.25, 0.35)                                      # the share of a bubble's life it fades in over, and the share it fades out over
BUBBLE_LIFE = 3.6                                              # s
BUBBLE_GLOW = 3.0                                              # their brightness (slag's own is about 75: white)

_HERE = Path(__file__).resolve()
ART = (_HERE.parents[2] / "art",)   # bl3_weapons/art: drop a PNG of the same name there to change a look


def _art(name: str) -> Path | None:
    found = [d / name for d in reversed(ART) if (d / name).is_file()]
    return found[0] if found else None


def _tex_name(base: str, png: Path | None) -> str:
    return base + (str(int(png.stat().st_mtime)) if png is not None else "")      # new art is a new texture, not the old one still loaded


_gas_png, _bubble_png, _smoke_png = _art("radiation_gas.png"), _art("radiation_bubble.png"), _art("radiation_smoke.png")
GAS_TEXTURE = "Transient." + _tex_name("RadFxGas", _gas_png)
SMOKE_TEXTURE = "Transient." + _tex_name("RadFxSmoke", _smoke_png)
BUBBLE_TEXTURE = "Transient." + _tex_name("RadFxBubble", _bubble_png)

particles.LOOKS["RadGas"] = particles.tran(1, GAS_BOOST, textures={"Tex0": GAS_TEXTURE}, fade=64.0, tint=COLOUR)
particles.LOOKS["RadSmoke"] = particles.tran(1, GAS_BOOST * 1.3, textures={"Tex0": SMOKE_TEXTURE}, fade=64.0, tint=COLOUR)   # the cloud on an irradiated enemy: wisps, not a round blob
particles.LOOKS["RadBubble"] = particles.tran(1, 1.8, textures={"Tex0": BUBBLE_TEXTURE}, fade=16.0, tint=COLOUR)
# Small, Medium and Large are the same nine emitters: the gas twice, the flipbook smoke twice (off), the bubbles twice, the flash, the ring and the sparks (off)
particles.LOOKS["RadFlash"] = particles.add(2, 2.0, tint=COLOUR)       # the explosion's flash
particles.LOOKS["RadRing"] = particles.add(4, 1.5, tint=COLOUR)         # and its ring
# Small is what an irradiated enemy gives off (gas and bubbles only); Medium and Large are the death blast: the same, with a flash and a ring
for _size in SIZES.values():
    _blast = _size != "Small"
    _gas = "RadGas" if _blast else "RadSmoke"
    particles.EMITTERS[SYSTEM.format(_size)] = (_gas, _gas, None, None, "RadBubble", "RadBubble", "RadFlash" if _blast else None,
                                                "RadRing" if _blast else None, None)

IMPACT = f"{TOP}.Particles.Part_Corrosive_ImpactFX_Nofld"       # the radiation gun's bullet impact: corrosive's without the fluid, as yellow gas and a spark
particles.LOOKS["RadSpark"] = particles.add(2, 1.5, tint=COLOUR)
particles.EMITTERS[IMPACT] = ("RadGas", None, "RadBubble", "RadBubble", "RadBubble", None)     # the smoke only: the shockwave sprite was a yellow square on the enemy; the dirt specks and the acid goop (twice) are yellow bubbles that spray out; the fluid is off
ALL = [SYSTEM.format(size) for size in SIZES.values()] + [IMPACT]

_dressed: set[str] = set()


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] radiation fx: {msg}")


def _find(cls: str, path: str) -> Any:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _set_table(mod: Any, field: str, table: list[float]) -> None:
    value = getattr(mod, field)
    value.LookupTable = table
    setattr(mod, field, value)


def _grey(table: list[float], level: float | None = None) -> list[float]:
    """A colour table (RGB triples; the tables hold HDR values) as the same brightness in grey (`level`: a brightness of its own): the material's
    tint makes it yellow."""
    out = list(table)
    for i in range(0, len(out) - 2, 3):
        m = level if level is not None else max(out[i], out[i + 1], out[i + 2])
        out[i] = out[i + 1] = out[i + 2] = m
    return out


def _dress(system: Any) -> None:
    """Edit one copy's modules, once (they are the mod's own objects: nothing is saved or put back)."""
    seen: set[int] = set()
    impact = system.Name == "Part_Corrosive_ImpactFX_Nofld"
    for emitter in system.Emitters:
        lod = emitter.LODLevels[0]
        required = lod.RequiredModule
        name = required.Material.Name if required is not None and required.Material is not None else "DefaultParticle"
        if name in FLASH:
            for level in emitter.LODLevels:
                for mod in level.Modules:
                    if mod.Class.Name == "ParticleModuleColorOverLife" and mod._get_address() not in seen:
                        seen.add(mod._get_address())
                        _set_table(mod, "ColorOverLife", _grey(list(mod.ColorOverLife.LookupTable), 1.0))
                        _set_table(mod, "AlphaOverLife", [x * FLASH_ALPHA for x in mod.AlphaOverLife.LookupTable])
            continue
        if name in SPRAY:
            for level in emitter.LODLevels:
                for mod in level.Modules:
                    if mod._get_address() in seen:
                        continue
                    kind = mod.Class.Name
                    if kind == "ParticleModuleColorOverLife":
                        _set_table(mod, "ColorOverLife", _grey(list(mod.ColorOverLife.LookupTable), BUBBLE_GLOW))
                    elif kind == "ParticleModuleColor":
                        _set_table(mod, "StartColor", _grey(list(mod.StartColor.LookupTable), BUBBLE_GLOW))
                    elif kind == "ParticleModuleColorScaleOverLife":
                        _set_table(mod, "ColorScaleOverLife", [1.0] * len(list(mod.ColorScaleOverLife.LookupTable)))
                    elif kind == "ParticleModuleSize":
                        _set_table(mod, "StartSize", [x * SPRAY_SCALE * (IMPACT_SCALE if impact else 1.0) for x in mod.StartSize.LookupTable])
                    else:
                        continue
                    seen.add(mod._get_address())
            continue
        if name not in GAS and name != BUBBLES:
            continue
        bubble = name == BUBBLES
        spreads = any(m.Class.Name == "ParticleModuleLocationPrimitiveSphere" for m in lod.Modules)
        for level in emitter.LODLevels:
            req = level.RequiredModule
            if req is not None and req._get_address() not in seen:
                seen.add(req._get_address())
                try:                                    # one picture per sprite: slag's smoke is cut from a sheet, ours is a single puff
                    req.SubImages_Horizontal = 1
                    req.SubImages_Vertical = 1
                    req.InterpolationMethod = 0
                except Exception:  # noqa: BLE001
                    _log("could not make the sprites single pictures: " + traceback.format_exc()[-200:])
            for mod in level.Modules:
                if mod._get_address() in seen:
                    continue
                kind = mod.Class.Name
                if bubble and kind == "ParticleModuleSize":
                    n = len(list(mod.StartSize.LookupTable))
                    _set_table(mod, "StartSize", [BUBBLE_SIZE[0] if i % 6 < 3 else BUBBLE_SIZE[1] for i in range(n)])
                elif bubble and kind == "ParticleModuleLifetime":
                    n = len(list(mod.Lifetime.LookupTable))
                    _set_table(mod, "Lifetime", [BUBBLE_LIFE * (0.7 if i % 2 else 1.0) for i in range(n)])
                elif bubble and kind == "ParticleModuleLocationPrimitiveSphere":
                    _set_table(mod, "StartRadius", [x * BUBBLE_RADIUS_SCALE for x in mod.StartRadius.LookupTable])
                    _set_table(mod, "VelocityScale", [4.0] * len(list(mod.VelocityScale.LookupTable)))    # a small drift out of the sphere; the rise is the velocity module's
                elif bubble and kind == "ParticleModuleColor":
                    _set_table(mod, "StartColor", _grey(list(mod.StartColor.LookupTable), BUBBLE_GLOW))
                elif bubble and kind == "ParticleModuleColorScaleOverLife":
                    _set_table(mod, "ColorScaleOverLife", [1.0] * len(list(mod.ColorScaleOverLife.LookupTable)))      # slag's purple tint
                    alpha = list(mod.AlphaScaleOverLife.LookupTable)
                    body = len(alpha) - 2                                    # the table's first two numbers are its header; the rest are samples over the particle's life
                    ramp = [min(1.0, (i + 0.5) / body / BUBBLE_FADE[0], (1.0 - (i + 0.5) / body) / BUBBLE_FADE[1]) for i in range(body)]
                    _set_table(mod, "AlphaScaleOverLife", alpha[:2] + ramp)    # faded in, then out
                elif bubble and kind == "ParticleModuleVelocityOverLifetime":
                    mod.Absolute = True                                       # a velocity of its own over the life, not a multiple of the start one: straight up, slowly faster
                    table = list(mod.VelOverLife.LookupTable)
                    _set_table(mod, "VelOverLife", table[:2] + [0.0, 0.0, BUBBLE_RISE[0], 0.0, 0.0, BUBBLE_RISE[1]])
                elif not bubble and kind == "ParticleModuleOrientationAxisLock":
                    mod.bEnabled = False                  # slag's gas lies flat (locked to an axis): a puff on an enemy is a billboard
                elif not bubble and kind == "ParticleModuleColorOverLife":
                    _set_table(mod, "ColorOverLife", _grey(list(mod.ColorOverLife.LookupTable), 1.0 if name == "Mati_Smoke_Generic_SubUV" else None))   # (the impact's smoke is BLACK smoke: its own brightness is nothing)
                    _set_table(mod, "AlphaOverLife", [x * (IMPACT_ALPHA if name == "Mati_Smoke_Generic_SubUV" else GAS_ALPHA) for x in mod.AlphaOverLife.LookupTable])
                elif impact and kind == "ParticleModuleLifetime":
                    _set_table(mod, "Lifetime", [x * IMPACT_GAS_LIFE for x in mod.Lifetime.LookupTable])
                elif impact and kind == "ParticleModuleSize":
                    _set_table(mod, "StartSize", [x * IMPACT_GAS_SCALE for x in mod.StartSize.LookupTable])
                else:
                    continue
                seen.add(mod._get_address())
            spawn = level.SpawnModule
            if bubble and spawn is not None and spawn._get_address() not in seen:
                seen.add(spawn._get_address())
                for index in range(len(spawn.BurstList)):            # no burst: all at once is a puff of dots
                    spawn.BurstList[index].Count = 0
                    spawn.BurstList[index].CountLow = 0
                if spreads:                                          # the sphere's emitter streams them instead, from low down, over a while
                    _set_table(spawn, "Rate", [BUBBLE_RATE] * len(list(spawn.Rate.LookupTable)))
                    if req is not None:
                        req.EmitterDuration = BUBBLE_STREAM
                        req.EmitterDelay = 0.0


def load() -> str:
    """Setup step: load the package, root and dress the copies, and make the pictures (texloader) the looks draw with."""
    load_sound()
    if _find("Package", TOP) is None or _find("ParticleSystem", SHOTS + next(iter(SHOT_LISTS.values()))) is None:  # (the group can exist before the package is read in full)
        unrealsdk.load_package(PACKAGE)
    try:
        register_shots()
    except Exception:  # noqa: BLE001 - the shot effects never stop the gas and bubbles
        _shot_once("error", "shot effects not applied: " + traceback.format_exc()[-300:])
    if len(_dressed) == len(ALL):
        return "dressed"
    try:
        from texloader import api  # noqa: PLC0415 - an optional mod: without it the gas and bubbles stay off
    except ImportError:
        _log("texloader isn't installed: the radiation gas and bubbles stay off")
        return "no texloader"
    for png, path in ((_gas_png, GAS_TEXTURE), (_bubble_png, BUBBLE_TEXTURE), (_smoke_png, SMOKE_TEXTURE)):
        if png is not None and _find("Texture2D", path) is None:
            api.load(str(png), path.removeprefix("Transient."))
    for path in ALL:
        if path in _dressed:
            continue
        system = _find("ParticleSystem", path)
        if system is None:
            _log(f"no {path}: the package is missing or outdated (the build makes it)")
            continue
        system.ObjectFlags = int(system.ObjectFlags) | 0x4000       # rooted: nothing else keeps it
        _dress(system)
        _dressed.add(path)
    _log(f"{len(_dressed)} explosions dressed")
    return "dressed" if len(_dressed) == len(ALL) else "incomplete"


def ready(size: str = "medium") -> bool:
    """True when this size's gas has its material (the compile queue has been through it)."""
    system = _find("ParticleSystem", SYSTEM.format(SIZES[size]))
    if system is None or system.Emitters[0] is None:
        return False
    lod = system.Emitters[0].LODLevels[0]
    material = lod.RequiredModule.Material
    return material is not None and str(material.Name).startswith("Mati_Rad") and bool(lod.bEnabled)


def impact_ready() -> bool:
    """True when the bullet impact's smoke has its material."""
    system = _find("ParticleSystem", IMPACT)
    if system is None or system.Emitters[0] is None:
        return False
    lod = system.Emitters[0].LODLevels[0]
    material = lod.RequiredModule.Material
    return material is not None and str(material.Name).startswith("Mati_Rad") and bool(lod.bEnabled)


NORMAL_IMPACT = "FX_BulletImpacts_Metal.Particles.Part_Impact_Metal_LargeCal"   # BL2's plain bullet impact (the generic one: sparks and dust)
_normal_added: list[int] = []


def impact_system() -> Any:
    """The radiation bullet impact: the gas and bubbles, with BL2's plain bullet impact's emitters on top of them (a radiation hit is a normal hit too; 2026-10-18)."""
    system = _find("ParticleSystem", IMPACT)
    if system is not None and not _normal_added:
        plain = _find("ParticleSystem", NORMAL_IMPACT)
        if plain is not None:
            _normal_added.append(1)
            try:
                system.Emitters = [*system.Emitters, *plain.Emitters]
                _log(f"radiation impact: {len(plain.Emitters)} emitters of the plain bullet impact added")
            except Exception:  # noqa: BLE001
                _log("plain impact not added: " + traceback.format_exc()[-200:])
    return system


def shot_system(name: str) -> Any:
    """A dressed carried effect by its BL2 name (None until the package has it)."""
    return _shot_systems.get(SHOTS + name)


def explosion_system() -> Any:
    """The medium gas blast, as a radiation rocket's explosion look (radiation.own_impact)."""
    return _find("ParticleSystem", SYSTEM.format(SIZES["medium"]))


GEIGER = (_HERE.parents[2] / "art" / "explosion_geiger.wav",)   # a Geiger counter over the blast: this mod's, then the player's own (wins)
_geiger_event: Any = None
_geiger_tried = False


def load_sound() -> None:
    """Setup step: the Geiger-counter sound as a Wwise event (the blaudio mod; without it the blast keeps slag's own sound only)."""
    global _geiger_event, _geiger_tried
    if _geiger_tried:
        return
    _geiger_tried = True
    file = next((f for f in GEIGER[::-1] if f.is_file()), None)
    if file is None:
        return
    try:
        import blaudio  # noqa: PLC0415 - an optional mod
    except ImportError:
        _log("blaudio isn't installed: the blast has no Geiger sound")
        return
    try:
        _geiger_event = blaudio.add({"geiger": file.read_bytes()}, namespace="radiation", bank_name="Weapon", positioning="3d").events["geiger"]
        _log(f"blast sound loaded from {file.name}")
    except Exception:  # noqa: BLE001
        _log("could not load the blast sound: " + traceback.format_exc()[-300:])


SOUNDS = {"small": "SM", "medium": "MED", "large": "LG"}   # slag's own explosion sounds (Ake_Exp_Elemental.Exp_Slag)


def spawn(pc: Any, where: Any, size: str = "medium", attach: Any = None, sound_on: Any = None) -> bool:
    """Play one at `where` (a Vector), following `attach` when given, with slag's explosion sound on the actor `sound_on` when given. False when it
    isn't ready (nothing played)."""
    try:
        if not ready(size):
            return False
        pool = pc.WorldInfo.MyEmitterPool
        system = _find("ParticleSystem", SYSTEM.format(SIZES[size]))
        pool.SpawnEmitter(system, where, unrealsdk.make_struct("Rotator", Pitch=0, Yaw=0, Roll=0), attach)
        if sound_on is not None:
            event = _find("AkEvent", f"Ake_Exp_Elemental.Exp_Slag.Ak_Play_Exp_Elemental_Slag_{SOUNDS[size]}")
            if event is not None:
                sound_on.PlayAkEvent(event)
            if _geiger_event is not None and size != "small":
                sound_on.PlayAkEvent(_geiger_event)
        return True
    except Exception:  # noqa: BLE001
        _log("spawn failed: " + traceback.format_exc()[-300:])
        return False


# ------------------------------------------------------------------------------------------------ the shot effects: muzzle flash, tracers, trails
# BL2 picks a gun's muzzle flash and tracer from lists (EffectCollectionDefinition) whose entries test the weapon's damage type
# (`WeaponDamage_Is_<Element>`), the way cryo_shot_fx adds cryo's. Radiation had none, so its guns shot the plain gun's flash and bullets, and an E-tech
# gun its purple default. Here the attribute `WeaponDamage_Is_Radiation` is built (it answers true for radiation's damage types), and each list gets an entry
# for it: a copy of corrosive's, with a yellow copy of corrosive's own effect (carried in the package: convert/radfx.py SHOT_SYSTEMS).
import copy  # noqa: E402

WEAPON_IS_RADIATION = "D_Attributes.WeaponDamageType.WeaponDamage_Is_Radiation"
_WEAPON_IS_CORROSIVE = "D_Attributes.WeaponDamageType.WeaponDamage_Is_Corrosive"
_RADIATION_TYPE_NAMES = ("DmgType_Radiation_Impact", "DmgType_Radiation_Status")
SHOTS = f"{TOP}.Shots."  # (the package keeps BL2's own object names; convert/radfx.py SHOT_SYSTEMS)
SHOT_LISTS = {  # list -> the carried system for its radiation entry
    "GD_Weap_AssaultRifle.Effects.AR_Default_MuzzleFlashes": "Part_Assault_MuzzleFlash_Corrosive",
    "GD_Weap_Launchers.Effects.L_Default_MuzzleFlashes": "Part_Launcher_Muzzle_Corrosive",
    "GD_Weap_Pistol.Effects.Pistol_Default_MuzzleFlashes": "Part_Pistol_Alien_Corrosive_MuzzleFlash",     # (BL2 has no corrosive pistol flash of its own to carry)
    "GD_Weap_Shotgun.Effects.SG_Default_MuzzleFlashes": "Part_Shotgun_MuzzleFlash_Corrosive",
    "GD_Weap_SMG.Effects.SMG_Default_MuzzleFlashes": "Part_SMG_MuzzleFlash_Corrosive",
    "GD_Weap_SniperRifles.Effects.SR_Default_MuzzleFlashes": "Part_Sniper_MuzzleFlash_Corrosive",
    "GD_Weap_AssaultRifle.Effects.AR_ETech_MuzzleFlashes": "Part_AR_Alien_Corrosive_Muzzleflash",
    "GD_Weap_Launchers.Effects.L_ETech_MuzzleFlashes": "Part_Launcher_Muzzle_Corrosive",
    "GD_Weap_Pistol.Effects.Pistol_ETech_MuzzleFlashes": "Part_Pistol_Alien_Corrosive_MuzzleFlash",
    "GD_Weap_Shotgun.Effects.SG_ETech_MuzzleFlashes": "Part_Shotgun_Alien_Corrosive_Muzzleflash",
    "GD_Weap_SMG.Effects.SMG_ETech_MuzzleFlashes": "Part_SMG_Alien_Corrosive_Muzzleflash",
    "GD_Weap_SniperRifles.Effects.SR_ETech_MuzzleFlashes": "Part_Snipe_Alien_Corrosive_Muzzleflash",
    "GD_Weap_Shared_Effects.Default_Tracers": "Part_Wep_Tracer_Corrosive",
    "GD_Weap_Shared_Effects.Default_Elemental_Rocket_Trails": "Part_Rocket_Trail_Corrosive",
    "GD_Weap_AssaultRifle.Effects.AR_ETech_Tracers": "Part_AR_Alien_Corrosive_Tracer",
    "GD_Weap_Launchers.Effects.L_ETech_Tracers": "Part_Wep_Tracer_Corrosive",
    "GD_Weap_Pistol.Effects.Pistol_ETech_Tracers": "Part_Pistol_Alien_Corrosive_Tracer",
    "GD_Weap_Pistol.Effects.Pistol_ETech_Homing_Tracers": "Part_Pistol_Homing_Corrosive_Tracer",
    "GD_Weap_Shotgun.Effects.SG_ETech_Tracers": "Part_Shotgun_Alien_Corrosive_Tracer",
    "GD_Weap_SMG.Effects.SMG_ETech_Tracers": "Part_SMG_Alien_Corrosive_Tracer",
    "GD_Weap_SniperRifles.Effects.SR_ETech_Tracers": "Part_Sniper_Alien_Corrosive_Tracer",
}
SHOTS_ENABLED = True  # the radiation guns' muzzle flashes, tracers and trails (edits BL2's effect lists; seen working in game 2026-10-18)
SPHERE = "Part_Corrosive_Nova_Maliwan_Explosion"  # the Maliwan nova's sphere (carried): on the weapons' explosions, so a radiation rocket is as big as an incendiary one
SPHERE_SCALE = 0.9  # its size tables times this (the tiers' particle scale is capped for the gas)
EXTRA_SHOTS = ("Part_InFlight_Corrosive", "Part_Shield_Spike_Corrosive", SPHERE)  # carried and dressed like the lists' systems, used by radiation grenades and spike shields (bl3_weapons/radgear.py)
SHOT_BRIGHTNESS_CAP = 6.0  # a colour table's HDR value is kept, up to this
_shot_systems: dict[str, Any] = {}
_shot_said: set[str] = set()
_shot_loaded: list[int] = []
_is_radiation: Any = None


def _shot_once(key: str, msg: str) -> None:
    if key not in _shot_said:
        _shot_said.add(key)
        _log(msg)


def _build_is_radiation() -> Any:
    """WeaponDamage_Is_Radiation, from BL2's WeaponDamage_Is_Corrosive with radiation's damage type names (cryo/attributes.py builds cryo's the same way)."""
    corrosive = unrealsdk.find_object("AttributeDefinition", _WEAPON_IS_CORROSIVE)
    group, _, name = WEAPON_IS_RADIATION.rpartition(".")
    attribute = unrealsdk.construct_object("AttributeDefinition", unrealsdk.find_object("Package", group), name)
    attribute.bIsSimpleAttribute = True
    attribute.ContextResolverChain = [unrealsdk.construct_object("WeaponAttributeContextResolver", attribute, "WeaponAttributeContextResolver_0")]
    template = corrosive.ValueResolverChain[0]
    resolver = unrealsdk.construct_object(template.Class, attribute, "DamageTypeAttributeValueResolver_0", 0, template)
    entries = []
    for entry, type_name in zip(template.DamageTypes, _RADIATION_TYPE_NAMES):
        entry = copy.copy(entry)
        entry.AssociatedDamageTypeName = type_name
        entries.append(entry)
    resolver.DamageTypes = entries
    attribute.ValueResolverChain = [resolver]
    return attribute


def is_radiation() -> Any:
    global _is_radiation
    if _is_radiation is None:
        _is_radiation = _find("AttributeDefinition", WEAPON_IS_RADIATION) or _build_is_radiation()
        _is_radiation.ObjectFlags = int(_is_radiation.ObjectFlags) | 0x4000
    return _is_radiation


def _yellow_table(table: list[float]) -> list[float]:
    """A colour table is two header numbers, then r, g, b triples (the first version read it from the start: the colours came out aqua and magenta)."""
    out = list(table)
    for i in range(2, len(out) - 2, 3):
        top = min(max(out[i], out[i + 1], out[i + 2]), SHOT_BRIGHTNESS_CAP)
        out[i], out[i + 1], out[i + 2] = COLOUR[0] * top, COLOUR[1] * top, COLOUR[2] * top
    return out


SHOT_NEUTRAL = {  # BL2's own plain (uncoloured) materials: the colour tables tint these; corrosive's own materials are green-blue pictures that stay aqua
    "flash": "FX_WEP_SMG.Materials._Default.Mati_MuzzFlash_SMG_Front",
    "tracer": "FX_WEP_Trails.Materials.Instances.Default.Mati_Default_Bullet_Tracer",
    "ring": "FX_WEP_Alien.Materials.BlasterRing_Geo_Desat",
}
SHOT_SWAP = ("corrosive", "blastertrail_inst_green", "prasma_base", "blasterring_geo", "energy_rings", "orb_core")  # (lower case) materials turned neutral
SHOT_OFF = ("acidspraygoop", "skag_bile", "skag_elecground", "lightning_horizontal", "seraenergyblast")  # goop, bile and lightning: green by nature, not drawn


def _neutral_material(name: str) -> Any:
    low = name.lower()
    if "desat" in low or not any(k in low for k in SHOT_SWAP):
        return None
    kind = "flash" if ("muzz" in low or "flash" in low) else "ring" if "ring" in low else "tracer"
    return _find("MaterialInstanceConstant", SHOT_NEUTRAL[kind]) or _find("Material", SHOT_NEUTRAL[kind])


def _neutralise(system: Any) -> None:
    for emitter in system.Emitters:
        for level in emitter.LODLevels:
            required = level.RequiredModule
            material = required.Material if required is not None else None
            if material is None:
                continue
            low = material.Name.lower()
            if any(k in low for k in SHOT_OFF):
                level.bEnabled = False
                continue
            replacement = _neutral_material(material.Name)
            if replacement is not None:
                required.Material = replacement


def _dress_shot(system: Any) -> None:
    """A carried corrosive effect in radiation's yellow: plain materials, then every colour table of every emitter (brightness kept, the colour replaced)."""
    if system.Name != SPHERE:       # (the sphere keeps its own materials: it is a mesh and a glow, not a muzzle flash)
        _neutralise(system)
    else:
        for emitter in system.Emitters:
            for level in emitter.LODLevels:
                for mod in level.Modules:
                    if mod.Class.Name == "ParticleModuleSize":
                        _set_table(mod, "StartSize", [x * SPHERE_SCALE for x in mod.StartSize.LookupTable])
    fields = {"ParticleModuleColor": "StartColor", "ParticleModuleColorOverLife": "ColorOverLife", "ParticleModuleColorScaleOverLife": "ColorScaleOverLife"}
    for emitter in system.Emitters:
        for level in emitter.LODLevels:
            for mod in level.Modules:
                field = fields.get(mod.Class.Name)
                if field is not None:
                    _set_table(mod, field, _yellow_table(list(getattr(mod, field).LookupTable)))


def _asks(entry: Any) -> str | None:
    evaluator = entry.Expression
    attribute = evaluator.Expression.AttributeOperand1 if evaluator is not None else None
    return attribute.Name if attribute is not None else None


def _patch_list(path: str, system: Any) -> str:
    coll = _find("EffectCollectionDefinition", path)
    if coll is None:
        return "not loaded"
    entries = [copy.copy(e) for e in coll.ParticleEffects]
    asked = [_asks(e) for e in entries]
    attribute = is_radiation()
    if attribute.Name in asked:
        return "has radiation"
    if "WeaponDamage_Is_Corrosive" not in asked:
        return "no corrosive entry to copy"
    template = entries[asked.index("WeaponDamage_Is_Corrosive")]
    evaluator = unrealsdk.construct_object(template.Expression.Class, coll, "AttributeExpressionEvaluator_Radiation", 0, template.Expression)
    evaluator.ObjectFlags = int(evaluator.ObjectFlags) | 0x4000
    expression = copy.copy(evaluator.Expression)
    expression.AttributeOperand1 = attribute
    evaluator.Expression = expression
    entry = copy.copy(template)
    entry.Expression = evaluator
    entry.ParticleEffect = system
    last = len(entries) - 1 if asked and asked[-1] is None else len(entries)       # ahead of the untested entry
    coll.ParticleEffects = [*entries[:last], entry, *entries[last:]]
    return "added"


def register_shots() -> None:
    """Load and dress the carried shot effects (once), and put radiation's entry into every effect list that has none (each map start: lists load with maps)."""
    if not SHOTS_ENABLED:
        return
    for name in set(SHOT_LISTS.values()) | set(EXTRA_SHOTS):
        path = SHOTS + name
        if path in _shot_systems:
            continue
        system = _find("ParticleSystem", path)
        if system is None and not _shot_loaded:  # the game can know the package without having read all of it: a second load reads it
            _shot_loaded.append(1)
            unrealsdk.load_package(PACKAGE)
            system = _find("ParticleSystem", path)
        if system is None:
            _shot_once("missing", f"no {path}: the package is outdated (the build makes it); the radiation shot effects stay off")
            return
        system.ObjectFlags = int(system.ObjectFlags) | 0x4000
        _dress_shot(system)
        _shot_systems[path] = system
    for path, name in SHOT_LISTS.items():
        try:
            result = _patch_list(path, _shot_systems[SHOTS + name])
        except Exception as ex:  # noqa: BLE001 - one list never stops the others
            result = f"failed ({type(ex).__name__}: {ex})"
        _shot_once(f"list:{path}:{result}", f"shot effects: {path.rpartition('.')[2]}: {result}")


# ------------------------------------------------------------------------------------------------ the weapons' explosions
# A rocket's (or an E-tech bullet's) explosion is picked from an ExplosionCollectionDefinition the same way: entries test `WeaponDamage_Is_<Element>`. BL2's
# five lists hold corrosive, incendiary, shock and slag only: a radiation gun matched none, and got the plain explosion (or the last entry, slag's: "slag
# damage" on a radiation rocket, 2026-10-18). Each list gets a radiation entry: a copy of corrosive's explosion with the radiation damage type and the gas blast.
EXPLOSION_LISTS = {  # list -> BL2's corrosive explosion it copies
    "GD_Weap_Shared_Effects.Default_Elemental_Explosions": "GD_Explosions.corrosive.Explosion_CorrosiveMaster",
    "GD_Weap_Shared_Effects.MaliwanLauncher_Elemental_Explosions": "GD_Explosions.corrosive.Explosion_CorrosiveMaster_MaliwanNova",
    "GD_Weap_Pistol.Explosions.Spiker_Elemental_Explosions": "GD_Explosions.corrosive.Explosion_AlienSpiker_CorrosiveMaster",
    "GD_Weap_Shotgun.Explosions.Splat_Elemental_Explosions": "GD_Explosions.corrosive.Explosion_AlienSplat_CorrosiveMaster",
    "GD_Weap_SMG.Explosions.Plasma_Elemental_Explosions": "GD_Explosions.corrosive.Explosion_AlienPlasma_CorrosiveMaster",
}
EXPLOSION_SCALE_CAP = 3.0
_damage_type: Any = None
_explosions: dict[str, Any] = {}


def use_damage_type(damage_type: Any) -> None:
    """The radiation damage type (bl3_weapons builds it): the explosions below deal it."""
    global _damage_type
    _damage_type = damage_type
    register_explosions()


BASE_EXPLOSIONS = {"Small": "FX_WEP_Explosions.Particles.Default.Part_ExplosiveExplosion_Small",
                   "Medium": "FX_WEP_Explosions.Particles.Default.Part_ExplosiveExplosion_Medium"}   # BL2's plain explosion (the orange one a non-elemental rocket makes)
BLAST_RAYS = True   # the spiked star of the impact, as rays (kill switches for the combined blast: BLAST_RAYS, BLAST_BASE)
BLAST_BASE = True
RAYS_LIFE = 0.35
RAYS_SCALE = 1.2   # the impact system's spiked star ("ShockwaveSprite"), moved here: rays coming out of the blast
_blasts: dict[str, Any] = {}
_rays: list[Any] = []
_sphere_waits = [0]
_blast_tries = [0]
_rays_dressed: list[int] = []


def _dress_rays(emitter: Any) -> None:
    fields = {"ParticleModuleColor": "StartColor", "ParticleModuleColorOverLife": "ColorOverLife", "ParticleModuleColorScaleOverLife": "ColorScaleOverLife"}
    for level in emitter.LODLevels:
        level.bEnabled = True
        for mod in level.Modules:
            field = fields.get(mod.Class.Name)
            if field is not None:
                _set_table(mod, field, _yellow_table(list(getattr(mod, field).LookupTable)))
            elif mod.Class.Name == "ParticleModuleSize":
                _set_table(mod, "StartSize", [x * RAYS_SCALE for x in mod.StartSize.LookupTable])
            elif mod.Class.Name == "ParticleModuleLifetime":       # (the star is a 0.03 s flash in BL2: two frames; the rays last a third of a second)
                _set_table(mod, "Lifetime", [RAYS_LIFE] * len(list(mod.Lifetime.LookupTable)))


BLAST = f"{TOP}.Blast.Part_WEP_SlagExplosion" + "{}"     # second root objects of the gas blasts (the package carries them; their emitters are the Particles ones)


def blast_system(size: str) -> Any:
    """The radiation gas blast with BL2's plain explosion and a burst of rays on top of it: the carried second root of the gas system gets the extra emitters
    (a clone made by script crashed the game). None until each part is there: the caller falls back to the gas alone."""
    if size in _blasts:
        return _blasts[size]
    system = _find("ParticleSystem", BLAST.format(size))
    if system is None and not _shot_loaded:  # (the game can know the package without having read all of it: a second load reads it)
        _shot_loaded.append(1)
        unrealsdk.load_package(PACKAGE)
        system = _find("ParticleSystem", BLAST.format(size))
    base, impact = _find("ParticleSystem", BASE_EXPLOSIONS.get(size, "")), _find("ParticleSystem", IMPACT)
    if not _rays and impact is not None:
        found = next((e for e in impact.Emitters if e is not None and e.EmitterName == "ShockwaveSprite"), None)
        if found is not None:
            _rays.append(found)
    rays = _rays[0] if _rays else None
    if system is None or base is None or not ready(size.lower()):
        return None
    if not BLAST_RAYS:
        rays = None
    system.ObjectFlags = int(system.ObjectFlags) | 0x4000
    if rays is not None and not _rays_dressed:
        _rays_dressed.append(1)
        _dress_rays(rays)
        impact.Emitters = [e for e in impact.Emitters if e is None or e._get_address() != rays._get_address()]     # (bullet hits don't get the big star)
    sphere = shot_system(SPHERE)
    if sphere is None and _sphere_waits[0] < 40:      # (the carried systems are dressed at start; it is a few tries at most, then the blast comes without)
        _sphere_waits[0] += 1
        return None
    gas = list(system.Emitters)
    system.Emitters = [*gas, *([rays] if rays is not None else []), *(base.Emitters if BLAST_BASE else []), *(sphere.Emitters if sphere is not None else [])]
    _blasts[size] = system
    _log(f"radiation blast {size}: gas {len(gas)} + rays {int(rays is not None)} + plain explosion {len(base.Emitters)} emitters")
    return system


def _tier_system(i: int, size: str | None) -> Any:
    wanted = size or ("Small", "Medium", "Medium")[min(i, 2)]
    if size is None:                                           # (the shield nova stays gas alone: it is played on the player)
        try:
            blast = blast_system(wanted)
        except Exception:  # noqa: BLE001 - the plain gas blast is always there
            _shot_once("blast-error", "radiation blast not combined: " + traceback.format_exc()[-300:])
            blast = None
        if blast is not None:
            return blast
    return _find("ParticleSystem", SYSTEM.format(wanted))


def radiation_explosion(corrosive_path: str, scale_cap: float | None = None, size: str | None = None) -> Any:
    """A copy of BL2's corrosive explosion as radiation's: its damage type, the gas blast for its look (None while those are not there)."""
    if corrosive_path in _explosions:
        return _explosions[corrosive_path]
    source, blast = _find("ExplosionDefinition", corrosive_path), explosion_system()
    if source is None or blast is None or _damage_type is None or not ready("medium"):
        return None
    if size is None and (blast_system("Small") is None or blast_system("Medium") is None) and _blast_tries[0] < 60:
        _blast_tries[0] += 1      # the combined blast isn't there yet (about three seconds a try): not yet, rather than the gas alone for good
        return None
    name = "Explosion_Radiation_" + source.Name.removeprefix("Explosion_")
    clone = _find("ExplosionDefinition", f"{source.Outer._path_name()}.{name}")
    if clone is None:
        clone = unrealsdk.construct_object(source.Class, source.Outer, name, 0, source)
    clone.ObjectFlags = int(clone.ObjectFlags) | 0x4000
    clone.DamageTypeDef = _damage_type
    clone.ExplosionPSTemplate = blast
    tiers = [copy.copy(tier) for tier in clone.ExplosionScale]     # the look is in the size tiers (small, medium, large by the explosion's radius), not in the template above
    for i, tier in enumerate(tiers):
        tier.ExplosionPSTemplate = _tier_system(i, size) or blast
        tier.MaxParticleScale = min(tier.MaxParticleScale, scale_cap or EXPLOSION_SCALE_CAP)       # (the Maliwan nova's goes to 10.5: the gas blast is made for 1)
        tier.MinParticleScale = min(tier.MinParticleScale, tier.MaxParticleScale * 0.6)
    clone.ExplosionScale = tiers
    _explosions[corrosive_path] = clone
    return clone


def _patch_explosions(path: str, corrosive_path: str) -> str:
    coll = _find("ExplosionCollectionDefinition", path)
    if coll is None:
        return "not loaded"
    explosion = radiation_explosion(corrosive_path)
    if explosion is None:
        return "not ready"
    entries = [copy.copy(e) for e in coll.Explosions]
    asked = [_asks(e) for e in entries]
    attribute = is_radiation()
    if attribute.Name in asked:
        return "has radiation"
    if "WeaponDamage_Is_Corrosive" not in asked:
        return "no corrosive entry to copy"
    template = entries[asked.index("WeaponDamage_Is_Corrosive")]
    fields = [f.Name for f in template._type._fields() if f.Name != "Expression" and f.Class.Name == "ObjectProperty"]
    if not fields:
        return "no explosion field in " + str([f.Name for f in template._type._fields()])
    evaluator = unrealsdk.construct_object(template.Expression.Class, coll, "AttributeExpressionEvaluator_Radiation", 0, template.Expression)
    evaluator.ObjectFlags = int(evaluator.ObjectFlags) | 0x4000
    expression = copy.copy(evaluator.Expression)
    expression.AttributeOperand1 = attribute
    evaluator.Expression = expression
    entry = copy.copy(template)
    entry.Expression = evaluator
    setattr(entry, fields[0], explosion)
    coll.Explosions = [entry, *entries]      # first: the other entries test other elements and are false for a radiation gun
    return "added"


def explosions_ready() -> bool:
    return all(source in _explosions for source in EXPLOSION_LISTS.values())


def register_explosions() -> None:
    if _damage_type is None or not SHOTS_ENABLED:
        return
    for path, source in EXPLOSION_LISTS.items():
        try:
            result = _patch_explosions(path, source)
        except Exception:  # noqa: BLE001 - one list never stops the others
            result = "failed " + traceback.format_exc()[-240:]
        _shot_once(f"explosions:{path}:{result}", f"explosions: {path.rpartition('.')[2]}: {result}")


@hook(maps.ARRIVED, Type.POST)
def _shots_on_arrival(*_: Any) -> None:
    """Each map's arrival: radiation's entry goes into the effect lists the map loaded afresh (idempotent)."""
    register_explosions()
    if _shot_systems:
        try:
            register_shots()
        except Exception:  # noqa: BLE001
            _shot_once("arrival-error", "shot effects not applied: " + traceback.format_exc()[-300:])


HOOKS = (_shots_on_arrival,)
