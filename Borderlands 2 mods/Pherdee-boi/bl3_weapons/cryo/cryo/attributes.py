"""TPS's ice attributes, built at runtime at TPS's paths (M4/M5; RESEARCH.md §6.14, §6.15).

A reference to an attribute BL2 doesn't have loads as None, and a formula that reads None gets 0: Cold as Ice gave its
shard a status chance of `InstigatedIceStatusChanceModifier x 0.5` = 0, and nothing it hit ever froze (2026-10-01).

TPS keeps its ice attributes in properties of its own controller and pawn, which BL2's classes don't have. Here they
are noun attributes: BL2's `INounAttributeProvider` keeps a named value with a base and a modifier stack, made on first
use, so each reads TPS's neutral 1.0 until a skill modifies it, and skills modify it like any other attribute:
- the instigator-side ones (her cryo chance and damage; Avalanche_Active raises them) live on her player record, the
  PlayerReplicationInfo, which outlives her pawn; any context reaches it through its controller, as TPS's did;
- the target-side ones (cryo damage taken; Avalanche and Cold Money's enemy debuff raise them) live on the pawn.
Who reads them: the engine, for her projectiles' status chance and, through the cryo damage types'
`CharacterDamageTypeModifierAttribute`, for the target side; element.py's damage hook for the instigator-side damage
(BL2 applies its instigator modifiers per element, natively, and has no ice).

The other TPS-only attributes her skills import (the melee override, laser and oxygen as constants, the player's
shield capacity) aren't cryo's: baroness/attributes.py.
"""

from __future__ import annotations

import time
from typing import Any

import unrealsdk
from unrealsdk.unreal import UObject

INSTIGATED_CHANCE = "D_Attributes.StatusEffectModifiers.InstigatedIceStatusChanceModifier"
INSTIGATED_DAMAGE = "D_Attributes.DamageTypeModifers.InstigatedIceDamageModifier"
INSTIGATED_STATUS_DAMAGE = "D_Attributes.DamageTypeModifers.InstigatedIceStatusDamageModifier"
TAKEN_IMPACT = "D_Attributes.DamageTypeModifers.IceImpactDamageModifier"
TAKEN_STATUS = "D_Attributes.DamageTypeModifers.IceStatusEffectDamageModifier"

_INSTIGATOR = ("ControllerAttributeContextResolver", "PlayerReplicationInfoAttributeContextResolver")
_TARGET = ("PawnAttributeContextResolver",)
ATTRIBUTES = {INSTIGATED_CHANCE: _INSTIGATOR, INSTIGATED_DAMAGE: _INSTIGATOR, INSTIGATED_STATUS_DAMAGE: _INSTIGATOR,
              TAKEN_IMPACT: _TARGET, TAKEN_STATUS: _TARGET}
NEUTRAL = 1.0
RECENT = 0.05                   # seconds `recent_value` keeps what it read
_MOST_RECENT = 64
_RF_ROOTSET = 0x4000
_built: dict[str, UObject] = {}
_recent: dict[tuple[Any, str], tuple[float, float]] = {}
_reader: list[Any] = []         # the attribute class's default object, whose static functions read any attribute


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _build(path: str, chain: tuple[str, ...]) -> UObject:
    group, _, name = path.rpartition(".")
    attribute = unrealsdk.construct_object("AttributeDefinition", unrealsdk.find_object("Package", group), name)
    attribute.ContextResolverChain = [unrealsdk.construct_object(cls, attribute, f"{cls}_0") for cls in chain]
    noun = unrealsdk.construct_object("NounAttributeValueResolver", attribute, "NounAttributeValueResolver_0")
    noun.DefaultValue = unrealsdk.make_struct("AttributeInitializationData", BaseValueConstant=NEUTRAL,
                                              BaseValueAttribute=None, InitializationDefinition=None,
                                              BaseValueScaleConstant=1.0)
    noun.ReplicationStrategy = unrealsdk.find_enum("ENounReplicationStrategy").NOUNREP_NoReplication
    attribute.ValueResolverChain = [noun]
    return attribute


# "Is this weapon cryo?" (her Huntress skills ask). TPS's matches its weapon's damage type by name, exactly as BL2's
# WeaponDamage_Is_Shock does; our runtime cryo types carry TPS's names, cryo guns included, so the same shape works.
WEAPON_IS_ICE = "D_Attributes.WeaponDamageType.WeaponDamage_Is_Ice"
_WEAPON_IS_SHOCK = "D_Attributes.WeaponDamageType.WeaponDamage_Is_Shock"
_ICE_TYPE_NAMES = ("DmgType_Ice_Impact", "DmgType_Ice_Impact_NoFreeze")


def _build_weapon_is_ice() -> UObject:
    """WeaponDamage_Is_Ice, from BL2's WeaponDamage_Is_Shock with TPS's ice type names."""
    import copy  # noqa: PLC0415

    shock = unrealsdk.find_object("AttributeDefinition", _WEAPON_IS_SHOCK)
    group, _, name = WEAPON_IS_ICE.rpartition(".")
    attribute = unrealsdk.construct_object("AttributeDefinition", unrealsdk.find_object("Package", group), name)
    attribute.bIsSimpleAttribute = True
    attribute.ContextResolverChain = [unrealsdk.construct_object("WeaponAttributeContextResolver", attribute,
                                                                 "WeaponAttributeContextResolver_0")]
    template = shock.ValueResolverChain[0]
    resolver = unrealsdk.construct_object(template.Class, attribute, "DamageTypeAttributeValueResolver_0", 0, template)
    entries = []
    for entry, type_name in zip(template.DamageTypes, _ICE_TYPE_NAMES):
        entry = copy.copy(entry)
        entry.AssociatedDamageTypeName = type_name
        entries.append(entry)
    resolver.DamageTypes = entries
    attribute.ValueResolverChain = [resolver]
    return attribute


def ensure_attributes() -> dict[str, UObject]:
    """The ice attributes, found or built in BL2's own attribute groups (D_Attributes is in Startup)."""
    builders = {path: (lambda p=path, c=chain: _build(p, c)) for path, chain in ATTRIBUTES.items()}
    builders[WEAPON_IS_ICE] = _build_weapon_is_ice
    for path, build in builders.items():
        attribute = _built.get(path) or _find("AttributeDefinition", path) or build()
        flags = int(attribute.ObjectFlags)
        if not flags & _RF_ROOTSET:
            attribute.ObjectFlags = flags | _RF_ROOTSET
        _built[path] = attribute
    return dict(_built)


def value(path: str, context: Any) -> float:
    """An ice attribute's value for `context` (a controller, pawn, skill or projectile); NEUTRAL if it can't be read."""
    attribute = _built.get(path) or _find("AttributeDefinition", path)
    if attribute is None or context is None:
        return NEUTRAL
    if not _reader:
        _reader.append(unrealsdk.find_class("AttributeDefinition").ClassDefaultObject)
    return float(_reader[0].StaticGetAttributeValueOrDefault(Definition=attribute, ContextSource=context,
                                                             DefaultValue=NEUTRAL))


def recent_value(path: str, context: Any) -> float:
    """`value`, as it was at most RECENT seconds ago: for the damage hook, which asks at every cryo hit (a shotgun's
    pellets land in one frame and ask once)."""
    now = time.monotonic()
    key = (context, path)
    known = _recent.get(key)
    if known is not None and 0.0 <= now - known[0] < RECENT:
        return known[1]
    if len(_recent) >= _MOST_RECENT:
        _recent.clear()
    read = value(path, context)
    _recent[key] = (now, read)
    return read


def forget() -> None:
    """Nothing kept of what was read (a new map; a test that changes a value and asks at once)."""
    _recent.clear()
