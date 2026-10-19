"""Cryo gear (RESEARCH.md §6.24): cryo grenades, shields and relics, made the way cryo guns are (guns.py).

TPS has cryo grenades and shields (it has no relics), and each is BL2's shock one with another damage type. So each cryo
object is built at runtime from BL2's shock one, with TPS's names, texts and values (read from TPS's Startup.upk,
2026-10-02), and rolls wherever shock rolls, with shock's weights and game stages:
- Grenades. A grenade's element is its accessory part (`Accessory_Shock` and four grades): its behaviors give the grenade
  its damage type, explosion and in-flight effect, and its slots name it (payload titles ask `GrenadeModIs<Element>`,
  which reads the `Is<Element>` slot). Built: TPS's `GrenadeModIsIce`, the Ice slots on every grenade definition that
  has shock's, `Accessory_Ice` and its grades, and TPS's titles ("Cryo MIRV", "Snow Ball", "Frigid Sheila"...) next to
  shock's in each payload's title list. The card names a grenade's element by the attribute its status slots are
  constrained by (`[shock]Electrocute` for GrenadeModIsShock, then "Damage / sec."): the Ice slots are constrained by
  GrenadeModIsIce, whose label is TPS's `[ice]Cryo`, in BL2's list of grenade labels (guns.py adds the [ice] tag).
- Shields. TPS's Maliwan "Warming" capacitor (freeze immunity) rolls next to BL2's shock one in the lists that hold all
  three of BL2's; and TPS's Cryo Nova and Cryo Spike shields, with their own definitions, accessories, skills, balances
  and pools beside shock's, the Warming capacitor where shock's has its own immunity one. BL2 grants immunity by zeroing
  the wearer's status chance for the element; cryo's chance is scripted (element.py), so its immunity is too (element.IMMUNE).
- Relics. BL2's Elemental relic (slag or explosive damage) gets a third effect, cryo damage, and its Resistance relic a
  seventh, cryo resistance: new slots on BL2's definitions, rolled through BL2's own enable and upgrade parts (which
  already name those slots). A cryo relic is all BL2 assets, so it saves as one.

Looks: the cryo explosions and novas are TPS's once her build carries them (explosions.py), with shock's sounds; the
spikes keep shock's sparks, and grenades fly without an in-flight effect.

Cards: TPS's cryo icon (cardicons.py puts it into BL2's card movie) on a cryo grenade's, shield's and damage relic's
title, where BL2 names an element's icon natively from an enum that has no cryo (a cryo shield showed its shock twin's
until 0.3.2), and on the rows a cryo shield shares with its shock twin ("Nova Damage", "Spike Damage").

Saving: the new parts, name parts, definitions and balances sit in their own package, appended as sublibraries to the
base game's libraries like the other items (core/items.py); the layout keeper records their order per save. The order of
each sublibrary is its saved index: new assets go at the end, never between. Lists and pools loaded later (DLC) are
patched at each map start, as cryo guns' are.

"Cryo Item Drops" on No (guns.py's setting): what was added to BL2's part lists and pools comes out, and nothing is
added: they are as BL2 has them. A cryo grenade, a shield with the Warming capacitor or a cryo relic that a player
owns still loads: the mod answers the game's check of saved items for it as for its shock twin (`_check_as_shock`;
see guns.py). Switched in the menu, lists and pools follow at once.
"""

from __future__ import annotations

import copy
import itertools
import re
from typing import Any

import unrealsdk
from mods_base import hook
from unrealsdk.hooks import Block, Type, prevent_hooking_direct_calls
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

from ..core import actors, graphs, items, maps, saveedit, texts
from . import attributes, cardicons, element, explosions, guns

PACKAGE = "GD_AureliaCryoGear"
_RF_ROOTSET = 0x4000

# --- grenades (TPS's names: GD_GrenadeMods.*Ice*)
IS_ICE = "D_Attributes.GrenadeMod.GrenadeModIsIce"
_IS_SHOCK = "D_Attributes.GrenadeMod.GrenadeModIsShock"
# The card's element label: BL2's list of grenade labels gets TPS's for cryo
_GRENADE_PRESENTATION_LIST = "GD_AttributePresentation._AttributeList.GrenadeModOverridePresentationList"
_IS_SHOCK_PRESENTATION = "GD_AttributePresentation.GrenadeMod.AttrPresent_GrenadeModIsShock"
IS_ICE_PRESENTATION = ("AttrPresent_GrenadeModIsIce", "[ice]Cryo")
GRENADE_SLOTS = {"IsShock": "IsIce", "StatusEffectChanceModifier_Shock": "StatusEffectChanceModifier_Ice",
                 "StatusEffectDamage_Shock": "StatusEffectDamage_Ice"}
ACCESSORIES = {f"GD_GrenadeMods.Accessory.Accessory_Shock{grade}": f"Accessory_Ice{grade}"
               for grade in ("", "_Grade1", "_Grade2", "_Grade3", "_Grade4")}
# BL2's shock name part -> (TPS's cryo name part, its name)
GRENADE_NAMES = {
    "GD_GrenadeMods.Prefix.Prefix_Shock": ("Prefix_Ice", "Cryo"),
    "GD_GrenadeMods.Title.Title_AreaEffectShock": ("Title_AreaEffectIce", "Cryo"),
    "GD_GrenadeMods.Title.Title_BouncingBettyShock": ("Title_BouncingBettyIce", "Cryo Bouncing Betty"),
    "GD_GrenadeMods.Title.Title_BouncingBettyShock_Bandit": ("Title_BouncingBettyIce_Bandit", "Frigid Sheila"),
    "GD_GrenadeMods.Title.Title_ExterminatorShock": ("Title_ExterminatorIce", "Snow Ball"),
    "GD_GrenadeMods.Title.Title_GrenadeShock": ("Title_GrenadeIce", "Cryo Grenade"),
    "GD_GrenadeMods.Title.Title_GrenadeShock_Bandit": ("Title_GrenadeIce_Bandit", "Ice Gr'nade"),
    "GD_GrenadeMods.Title.Title_LeechShock": ("Title_LeechIce", "Cryo Leech"),
    "GD_GrenadeMods.Title.Title_MirvShock": ("Title_MirvIce", "Cryo MIRV"),
    "GD_GrenadeMods.Title.Title_MirvShock_Bandit": ("Title_MirvIce_Bandit", "Ice Merv"),
    "GD_GrenadeMods.Title.Title_SingularityShock": ("Title_SingularityIce", "Cryo Singularity"),
    "GD_GrenadeMods.Title.Title_TransfusionShock": ("Title_TransfusionIce", "Cryo Transfusion"),
}
_GRENADE_ELEMENT = re.compile(r"^Accessory_(Corrosive|Explosive|Incendiary|Shock|Slag|Ice)(_Grade\d)?$")

# --- shields
CAPACITOR = "Capacitor5_Maliwan_IceRes"
_SHOCK_CAPACITOR = "GD_Shields.Capacitor.Capacitor5_Maliwan_ShockRes"
_RESISTANCES = {f"GD_Shields.Capacitor.Capacitor5_Maliwan_{e}Res" for e in ("Fire", "Shock", "Corrosive")}
_SHOCK_RES_PREFIX = "GD_Shields.Prefixes.Prefix_ShockResistant"
RES_PREFIX = ("Prefix_IceResistant", "Warming")
IMMUNITY_TEXT = "Grants immunity to Freeze damage."
SPIKE_STATUS_CHANCE = 3.0       # TPS's Spike_Shield_Skill_Ice (BL2's shock spike: 20)


class _Shield:
    def __init__(self, kind: str, definition: str, accessory: str, skill: str, skill_name: str, title: str,
                 title_name: str) -> None:
        self.kind = kind
        self.definition = definition        # BL2's shock definition
        self.accessory = accessory          # BL2's shock accessory
        self.skill = skill                  # BL2's shock skill
        self.skill_name = skill_name
        self.title = title                  # TPS's cryo title (name part, its name)
        self.title_name = title_name


SHIELDS = (
    _Shield("Nova", "GD_Shields.A_Item.Shield_Nova_Shock", "GD_Shields.Accessory.Accessory6_Nova_Shock",
            "GD_Shields.Skills.Nova_Shield_Skill_Shock", "Cryo Nova Shield Skill", "Title_IceNova01_IceBurstShield",
            "Cryo Nova Shield"),
    _Shield("Spike", "GD_Shields.A_Item.Shield_Spike_Shock", "GD_Shields.Accessory.Accessory1_Spike_Shock",
            "GD_Shields.Skills.Spike_Shield_Skill_Shock", "Spike Shield Skill - Cryo", "Title_IceSpike01_IceSpikeShield",
            "Cryo Spike Shield"),
)
_SHIELD_BALANCES = "GD_ItemGrades.Shields.ItemGrade_Gear_Shield_{kind}_Shock_{grade}"
_GRADES = ("01_Common", "02_Uncommon", "03_Rare", "04_VeryRare", "05_Legendary")
# BL2's shock texts on the shields' item cards -> cryo (TPS's wording without its colour tags)
_TEXTS = (("[shock]Shock[-shock]", "Cryo"), ("[shock]Electrocute[-shock]", "Freeze"))
# That is English. In another language BL2's line has that language's shock words, which nothing here would find
# (German: "[shock]Schock[-shock]-Nova", "[shock]Stromschlag[-shock]": up to 0.6.0 a cryo shield's card named shock
# there). So there the line is TPS's own for its cryo shield (core/texts.py, `picked`): BL2's line, by the name it has
# on the shock shield -> (TPS's section, the English for where the game has no text of TPS's). No section: TPS's
# shield has no such line (the Spike shield's third says the second's again), and the card goes without it there.
TPS_SHIELDS = "gd_cork_shields"
SHIELD_LINES = {
    "Nova": {"AttributePresentationDefinition_1": ("A_Item.Shield_Nova_Ice:AttributePresentationDefinition_1",
                                                   "Releases a Cryo [skill]Nova[-skill] blast when depleted.")},
    "Spike": {"AttributePresentationDefinition_5": ("A_Item.Shield_Spike_Ice:AttributePresentationDefinition_5",
                                                    "Deals Cryo [skill]Spike Damage[-skill] to melee attackers."),
              "AttributePresentationDefinition_6": ("A_Item.Shield_Spike_Ice:AttributePresentationDefinition_6",
                                                    "Chance to Freeze enemies."),
              "AttributePresentationDefinition_1": (None, "")},
}
IMMUNITY_LINE = "Capacitor.Capacitor5_Maliwan_IceRes:AttributePresentationDefinition_2"
_LINE = "{} AttributePresentationDefinition"

# --- relics: (definition, the slot to copy, the new slot, the new slot's attribute, its part lists, their parts)
RELICS = (
    ("GD_Artifacts.A_Item.Artifact_Elemental", "Effect2", "Effect3", attributes.INSTIGATED_DAMAGE,
     ("GD_Artifacts.PartLists.Parts_Elemental", "GD_Artifacts.PartLists.Parts_Elemental_Rare"),
     {"GD_Artifacts.Enable1st.EnableFirst_Effect2": "GD_Artifacts.Enable1st.EnableFirst_Effect3"}),
    ("GD_Artifacts.A_Item.Artifact_Resistance", "Effect2", "Effect7", attributes.TAKEN_IMPACT,
     ("GD_Artifacts.PartLists.Parts_Resistance", "GD_Artifacts.PartLists.Parts_Resistance_Rare"),
     {f"GD_Artifacts.Enable{n}.Enable{w}_Effect2": f"GD_Artifacts.Enable{n}.Enable{w}_Effect7"
      for n, w in (("1st", "First"), ("2nd", "Second"), ("3rd", "Third"), ("4th", "Fourth"))}),
)
_PRESENTATION_LIST = "GD_AttributePresentation._AttributeList.ArtifactOverridePresentationList"
# the relic stat's label on the item card: attribute -> (BL2's shock one, ours, its text, its icon; None: the cryo
# icon, once BL2's card has it)
RELIC_PRESENTATIONS = {
    attributes.INSTIGATED_DAMAGE: ("GD_AttributePresentation.Artifacts.AttrPresent_ArtifactInstigatedShockDamageModifier",
                                   "AttrPresent_ArtifactInstigatedIceDamageModifier", "Cryo Damage", None),
    attributes.TAKEN_IMPACT: ("GD_AttributePresentation.Artifacts.AttrPresent_ArtifactShockImpactDamageModifier",
                              "AttrPresent_ArtifactIceImpactDamageModifier", "Cryo Resistance", "elementalresistance"),
}

ITEM_SLOTS = ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Material")

_built: dict[str, UObject] = {}         # our object name -> object
_INVENTORY = actors.Field("Pawn", "InvManager")
_pawn_class: list[Any] = [None]         # the Pawn class, found once
_twins: dict[str, UObject] = {}         # BL2 shock object path -> its cryo twin (parts, names, balances, pools)
_ours: set[str] = set()                 # our objects' paths, never patched themselves
_patched: dict[str, str] = {}           # BL2 part list, collection or pool path -> its class, patched in place
_passed: set[str] = set()               # BL2 part lists and collections cryo doesn't join (checked once)
_pools_passed: set[str] = set()         # BL2 pools with nothing for cryo (checked once)
_added: dict[str, set[str]] = {}        # BL2 part list (or collection:slot) -> the parts' paths we added to it
_copies: list[tuple[UObject, UObject]] = []     # (a part of ours in BL2's lists, the BL2 part its entries copy)
_titled: set[str] = set()               # BL2 grenade parts and definitions whose name lists got the cryo names
_said: set[str] = set()


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] cryo gear: {msg}")


def _once(key: str, msg: str) -> None:
    if key not in _said:
        _said.add(key)
        _log(msg)


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _get(cls: str, path: str) -> UObject:
    obj = _find(cls, path)
    if obj is None:
        raise LookupError(f"no {cls} {path}")
    return obj


def _root(obj: UObject) -> UObject:
    flags = int(obj.ObjectFlags)
    if not flags & _RF_ROOTSET:
        obj.ObjectFlags = flags | _RF_ROOTSET
    return obj


def _sub(cls: Any, outer: UObject, name: str, template: UObject) -> UObject:
    """A new copy of `template` named `name` in `outer`. Callers look for an earlier copy first, through what holds it
    (a subobject's path isn't looked up: constructing over an existing object would replace it in place)."""
    return _root(unrealsdk.construct_object(cls, outer, name, 0, template))


def _ours_in(pkg: UObject, cls: str, name: str, template: UObject, twin_of: UObject | None = None) -> UObject:
    """`template`'s copy named `name` directly in our package, found if it was made already."""
    obj = _find(cls, f"{pkg.Name}.{name}") or unrealsdk.construct_object(cls, pkg, name, 0, template)
    _root(obj)
    _built[name] = obj
    _ours.add(obj._path_name())
    if twin_of is not None:
        _twins[twin_of._path_name()] = obj
    return obj


def _own_copies(holder: UObject, current: list[UObject | None], templates: Any) -> list[UObject]:
    """`holder`'s own copies of `templates` (presentations): the ones it holds already, else new ones."""
    mine = [obj for obj in current if obj is not None and obj.Outer == holder]
    if mine:
        return mine
    return [_sub(t.Class, holder, t.Name, t) for t in templates if t is not None]


def _entry_like(src: Any, part: UObject) -> WrappedStruct:
    """A new weighted-part entry like `src` (weights, game stages, manufacturers) for `part`, built fresh (see
    guns._copy_entry)."""
    fields = {"Part": part, "Manufacturers": list(src.Manufacturers)}
    for name in ("MinGameStageIndex", "MaxGameStageIndex", "DefaultWeightIndex", "MinGameStage", "MaxGameStage",
                 "DefaultWeight"):
        if hasattr(src, name):
            fields[name] = getattr(src, name)
    return unrealsdk.make_struct(src._type.Name, **fields)


def _swapped(entries: Any, swap: dict[str, UObject]) -> list[WrappedStruct]:
    """Entries with each part in `swap` (by path) replaced."""
    return [_entry_like(e, swap.get(e.Part._path_name(), e.Part) if e.Part is not None else None) for e in entries]


def _with_twins(key: str, entries: Any, twins: dict[str, UObject]) -> list[WrappedStruct] | None:
    """`entries` and, after them, a copy of each entry whose part has a twin; None if there is nothing to add. What
    is added is noted under `key` (the list, or its collection and slot), for taking it out again (`_without`)."""
    entries = list(entries)
    have = {e.Part._path_name() for e in entries if e.Part is not None}
    added = [_entry_like(e, twins[e.Part._path_name()]) for e in entries
             if e.Part is not None and e.Part._path_name() in twins and twins[e.Part._path_name()]._path_name() not in have]
    if not added:
        return None
    _added.setdefault(key, set()).update(e.Part._path_name() for e in added)
    return [_entry_like(e, e.Part) for e in entries] + added


def _without(key: str, entries: Any) -> list[WrappedStruct] | None:
    """`entries` without the ones `_with_twins` added under `key`; None if none of them is there."""
    mine = _added.pop(key, None)
    if not mine:
        return None
    entries = list(entries)
    kept = [_entry_like(e, e.Part) for e in entries if e.Part is None or e.Part._path_name() not in mine]
    return kept if len(kept) != len(entries) else None


# ---------------------------------------------------------------------------------------------- grenades


def _is_ice() -> UObject:
    """TPS's GrenadeModIsIce: 1 when the grenade's IsIce slot is active (BL2's GrenadeModIsShock, for IsIce)."""
    found = _find("AttributeDefinition", IS_ICE)
    if found is not None:
        return _root(found)
    template = _get("AttributeDefinition", _IS_SHOCK)
    group, _, name = IS_ICE.rpartition(".")
    attribute = unrealsdk.construct_object("AttributeDefinition", unrealsdk.find_object("Package", group), name, 0,
                                           template)
    context = unrealsdk.construct_object("InventoryAttributeContextResolver", attribute,
                                         "InventoryAttributeContextResolver_1")
    value = unrealsdk.construct_object("AttributeSlotEffectAttributeValueResolver", attribute,
                                       "AttributeSlotEffectAttributeValueResolver_0")
    value.SlotName = "IsIce"
    attribute.ContextResolverChain = [_root(context)]
    attribute.ValueResolverChain = [_root(value)]
    return _root(attribute)


def _grenade_accessory(pkg: UObject, shock: UObject, name: str, damage: UObject, explosion: UObject) -> UObject:
    part = _ours_in(pkg, "GrenadeModPartDefinition", name, shock, twin_of=shock)
    upgrades = []
    for slot in part.AttributeSlotUpgrades:
        slot = copy.copy(slot)
        slot.SlotName = GRENADE_SLOTS.get(str(slot.SlotName), str(slot.SlotName))
        upgrades.append(slot)
    part.AttributeSlotUpgrades = upgrades
    bpd = graphs.clone(shock.BehaviorProviderDefinition, part, part.BehaviorProviderDefinition)
    for behavior in graphs.behaviors(bpd, "Behavior_AddInstanceData"):
        data = []
        for entry in behavior.InstanceData:
            entry = copy.copy(entry)
            what = str(entry.Name)
            if what == "DamageTypeOverride":
                entry.Object = damage
            elif what == "ExplosionOverride":
                entry.Object = explosion
            elif what == "InFlightEffectsOverride":
                entry.Object = None                       # shock's sparks; TPS's ice trail isn't converted yet
            data.append(entry)
        behavior.InstanceData = data
    part.BehaviorProviderDefinition = bpd
    r, g, b, a = guns.EMISSIVE
    colors = []
    for c in part.MaterialVectorParameterValues:
        c = copy.copy(c)
        if str(c.ParameterName) == "p_EmissiveColor":
            c.ParameterValue = unrealsdk.make_struct("LinearColor", R=r, G=g, B=b, A=a)
        colors.append(c)
    part.MaterialVectorParameterValues = colors
    return part


def _grenade_name(pkg: UObject, shock: UObject, name: str, text: str, is_ice: UObject) -> UObject:
    part = _ours_in(pkg, "ItemNamePartDefinition", name, shock, twin_of=shock)
    part.PartName = text
    expressions = []
    for expression in part.Expressions:
        expression = copy.copy(expression)
        if expression.AttributeOperand1 is not None and expression.AttributeOperand1.Name == "GrenadeModIsShock":
            expression.AttributeOperand1 = is_ice
        expressions.append(expression)
    part.Expressions = expressions
    return part


def _build_grenades(pkg: UObject) -> list[UObject]:
    """The cryo accessories and names; returns them for saving, in their saved order."""
    is_ice = _is_ice()
    damage = element.ensure_objects()[element.IMPACT]
    explosion = explosions.ensure_effects()[explosions.EXPLOSION]
    assets = [_grenade_accessory(pkg, _get("GrenadeModPartDefinition", shock), name, damage, explosion)
              for shock, name in ACCESSORIES.items()]
    for shock, (name, text) in GRENADE_NAMES.items():
        assets.append(_grenade_name(pkg, _get("ItemNamePartDefinition", shock), name, text, is_ice))
    return assets


def _grenade_slots() -> int:
    """Each grenade definition with shock's slots gets the Ice ones: copies, constrained by GrenadeModIsIce where
    shock's are by GrenadeModIsShock (as TPS's are; the card names the element by it). Idempotent; returns how many
    changed."""
    is_ice = _is_ice()
    count = 0
    for definition in unrealsdk.find_all("GrenadeModDefinition", exact=False):
        if definition.Name.startswith("Default__"):
            continue
        current = definition.AttributeSlotEffects       # looked at before anything is copied: most need nothing
        names = {str(e.SlotName) for e in current}
        if "IsShock" not in names:
            continue
        if "IsIce" in names and not any(
                str(e.SlotName) in GRENADE_SLOTS.values() and e.ConstraintAttribute is not None
                and e.ConstraintAttribute.Name == "GrenadeModIsShock" for e in current):
            continue
        effects = [copy.copy(e) for e in current]
        changed = "IsIce" not in names
        if changed:
            for effect in [e for e in effects if str(e.SlotName) in GRENADE_SLOTS]:
                ice = copy.copy(effect)
                ice.SlotName = GRENADE_SLOTS[str(effect.SlotName)]
                effects.append(ice)
        for effect in effects:        # also Ice slots built before 0.3.1, which kept shock's constraint
            constraint = effect.ConstraintAttribute
            if (str(effect.SlotName) in GRENADE_SLOTS.values() and constraint is not None
                    and constraint.Name == "GrenadeModIsShock"):
                effect.ConstraintAttribute = is_ice
                changed = True
        if changed:
            definition.AttributeSlotEffects = effects
            count += 1
    return count


def _grenade_presentation(pkg: UObject) -> None:
    """TPS's card label for a cryo grenade's element ("[ice]Cryo", before "Damage / sec."), in BL2's list of grenade
    labels."""
    plist = _get("AttributePresentationListDefinition", _GRENADE_PRESENTATION_LIST)
    name, text = IS_ICE_PRESENTATION
    mine = _ours_in(pkg, "AttributePresentationDefinition", name,
                    _get("AttributePresentationDefinition", _IS_SHOCK_PRESENTATION))
    mine.Attribute = _is_ice()
    # The element's name, as on the grenade itself: BL2's card puts its own "Damage / sec." after it. (TPS's label
    # is the whole row in some languages, "Kryoschaden/Sek.", which BL2's card would say twice.)
    cryo = texts.picked("gd_grenademods", "Prefix.Prefix_Ice ItemNamePartDefinition", "PartName", "")
    mine.Description = f"[ice]{cryo}" if cryo else text
    if not any(p is not None and p == mine for p in plist.Attributes):
        plist.Attributes = [*list(plist.Attributes), mine]


def _grenade_titles() -> int:
    """The cryo names next to shock's in every grenade part's and definition's title and prefix lists."""
    names = {path: _twins[path] for path in GRENADE_NAMES if path in _twins}
    count = 0
    for cls in ("GrenadeModPartDefinition", "GrenadeModDefinition"):
        for obj in unrealsdk.find_all(cls, exact=False):
            path = obj._path_name()
            if path in _titled or path in _ours or obj.Name.startswith("Default__"):
                continue
            _titled.add(path)
            for field in ("TitleList", "PrefixList"):
                current = list(getattr(obj, field))
                have = {n._path_name() for n in current if n is not None}
                wanted = []
                for n in current:
                    wanted.append(n)
                    twin = names.get(n._path_name()) if n is not None else None
                    if twin is not None and twin._path_name() not in have:
                        wanted.append(twin)
                if len(wanted) != len(current):
                    setattr(obj, field, wanted)
                    count += 1
    return count


def _grenade_element(part: UObject | None) -> str | None:
    if part is None or part.Class.Name != "GrenadeModPartDefinition":
        return None
    match = _GRENADE_ELEMENT.match(part.Name)
    return match.group(1) if match else None


def _rolls_grenade_element(entries: Any) -> bool:
    """A grenade accessory list that picks a random element (two or more) and has shock among them."""
    elements = {_grenade_element(e.Part) for e in entries} - {None}
    return len(elements) >= 2 and "Shock" in elements


# ---------------------------------------------------------------------------------------------- shields


def _capacitor(pkg: UObject) -> list[UObject]:
    """TPS's Warming capacitor: BL2's shock immunity one, its prefix, text and slot for cryo. BL2's also sets the
    shield's resistance type (what the Chimera shield reacts to); cryo's immunity is element.py's, so it has no behaviors."""
    shock = _get("ShieldPartDefinition", _SHOCK_CAPACITOR)
    part = _ours_in(pkg, "ShieldPartDefinition", CAPACITOR, shock, twin_of=shock)
    prefix_name, prefix_text = RES_PREFIX
    shock_prefix = _get("ItemNamePartDefinition", _SHOCK_RES_PREFIX)
    prefix = _ours_in(pkg, "ItemNamePartDefinition", prefix_name, shock_prefix, twin_of=shock_prefix)
    prefix.PartName = prefix_text
    part.PrefixList = [prefix]
    lines = _own_copies(part, list(part.CustomPresentations), shock.CustomPresentations)
    for line in lines:
        line.Description = texts.picked(TPS_SHIELDS, _LINE.format(IMMUNITY_LINE), "Description", IMMUNITY_TEXT)
    part.CustomPresentations = lines
    part.BehaviorProviderDefinition = None
    part.AttributeSlotUpgrades = [copy.copy(u) for u in part.AttributeSlotUpgrades if str(u.SlotName) != "ShockResist"]
    return [part, prefix]


def _cryo_text(text: str) -> str:
    for shock, ice in _TEXTS:
        text = text.replace(shock, ice)
    return text


def _part_list(pkg: UObject, shock: UObject, swap: dict[str, UObject]) -> UObject:
    plist = _ours_in(pkg, "ItemPartListDefinition", shock.Name.replace("_Shock", "_Ice"), shock)
    plist.WeightedParts = _swapped(shock.WeightedParts, swap)
    return plist


def _shield(pkg: UObject, spec: _Shield, capacitor: UObject) -> dict[str, UObject]:
    """One cryo shield type: skill, accessory, title, part lists and definition."""
    shock_skill = _get("SkillDefinition", spec.skill)
    skill = _ours_in(pkg, "SkillDefinition", shock_skill.Name.replace("_Shock", "_Ice"), shock_skill)
    skill.SkillName = spec.skill_name
    skill_bpd = graphs.clone(shock_skill.BehaviorProviderDefinition, skill, skill.BehaviorProviderDefinition)
    damage = element.ensure_objects()[element.IMPACT]
    for behavior in graphs.behaviors(skill_bpd, "Behavior_CauseDamage"):      # the spike
        behavior.DamageTypeDefinition = damage
        chance = copy.copy(behavior.StatusEffectChance)
        chance.BaseValueConstant = SPIKE_STATUS_CHANCE
        behavior.StatusEffectChance = chance
    for behavior in graphs.behaviors(skill_bpd, "Behavior_Explode"):          # the nova
        behavior.Definition = explosions.ensure_effects()[explosions.NOVA]
    skill.BehaviorProviderDefinition = skill_bpd

    shock_accessory = _get("ShieldPartDefinition", spec.accessory)
    accessory = _ours_in(pkg, "ShieldPartDefinition", shock_accessory.Name.replace("_Shock", "_Ice"), shock_accessory,
                         twin_of=shock_accessory)
    shock_title = shock_accessory.TitleList[0]
    title = _ours_in(pkg, "ItemNamePartDefinition", spec.title, shock_title, twin_of=shock_title)
    title.PartName = spec.title_name
    accessory.TitleList = [title]
    accessory_bpd = graphs.clone(shock_accessory.BehaviorProviderDefinition, accessory,
                                     accessory.BehaviorProviderDefinition)
    for behavior in graphs.behaviors(accessory_bpd, "Behavior_ActivateSkill"):
        behavior.SkillToActivate = skill
    for behavior in graphs.behaviors(accessory_bpd, "Behavior_DeactivateSkill"):
        behavior.SkillToDeactivate = skill
    accessory.BehaviorProviderDefinition = accessory_bpd

    shock_definition = _get("ShieldDefinition", spec.definition)
    definition = _ours_in(pkg, "ShieldDefinition", shock_definition.Name.replace("_Shock", "_Ice"), shock_definition,
                          twin_of=shock_definition)
    definition.DamageTypeIcon = unrealsdk.find_enum("EDamageType").DAMAGE_TYPE_Impact     # not shock's: _item_frame
    definition.GammaParts = _part_list(pkg, shock_definition.GammaParts, {_SHOCK_CAPACITOR: capacitor})
    definition.DeltaParts = _part_list(pkg, shock_definition.DeltaParts, {shock_accessory._path_name(): accessory})
    originals = [line for line in shock_definition.CustomPresentations if line is not None]
    lines = _own_copies(definition, list(definition.CustomPresentations), shock_definition.CustomPresentations)
    shown = []
    for line, original in zip(lines, originals, strict=False):
        said = _shield_line(spec.kind, original)
        if said is not None:
            line.Description = said
            shown.append(line)
    definition.CustomPresentations = shown
    return {"definition": definition, "accessory": accessory, "title": title}


def _shield_line(kind: str, original: UObject) -> str | None:
    """A cryo shield's card line for BL2's shock shield's `original`: in English BL2's own with cryo's words; in
    another language TPS's own line, BL2's own where the line names no element, and None where TPS has none."""
    own = str(original.Description)
    if texts.english():
        return _cryo_text(own)
    names = SHIELD_LINES.get(kind, {})
    if original.Name not in names:
        return own                              # nothing of shock's in it: BL2's line, in the game's language
    section, english = names[original.Name]
    if section is None:
        return None
    return texts.picked(TPS_SHIELDS, _LINE.format(section), "Description", english)


def _shield_balances(pkg: UObject, spec: _Shield, definition: UObject) -> list[UObject]:
    """Cryo balances, each a twin of a shock one, with a twin of its part collection (only its materials differ, and
    those are Maliwan's either way; the twin names our definition)."""
    balances = []
    for grade in _GRADES:
        shock = _find("InventoryBalanceDefinition", _SHIELD_BALANCES.format(kind=spec.kind, grade=grade))
        if shock is None:
            continue
        balance = _ours_in(pkg, "InventoryBalanceDefinition", shock.Name.replace("_Shock_", "_Ice_"), shock,
                           twin_of=shock)
        balance.InventoryDefinition = definition
        coll = shock.PartListCollection
        if coll is not None:
            twin = _twins.get(coll._path_name())
            if twin is None:
                twin = _ours_in(pkg, "ItemPartListCollectionDefinition", coll.Name.replace("_Shock_", "_Ice_"), coll,
                                twin_of=coll)
                twin.AssociatedItem = definition
            balance.PartListCollection = twin
        balances.append(balance)
    return balances


def _build_shields(pkg: UObject) -> tuple[list[UObject], list[UObject], list[UObject]]:
    """(definitions, parts and name parts, balances), each in its saved order."""
    parts = _capacitor(pkg)
    definitions, balances = [], []
    for spec in SHIELDS:
        made = _shield(pkg, spec, parts[0])
        definitions.append(made["definition"])
        parts += [made["accessory"], made["title"]]
        balances += _shield_balances(pkg, spec, made["definition"])
    return definitions, parts, balances


def wears_immunity(pawn: UObject) -> bool:
    """Is a Warming shield among the pawn's equipped items (player or enemy)?"""
    capacitor = _built.get(CAPACITOR)
    if capacitor is None or pawn is None:
        return False
    # Asked at every cryo status, of the one it lands on: a pawn's inventory through its field (core/actors.py).
    pawn_class = _pawn_class[0]
    if pawn_class is None:
        pawn_class = _pawn_class[0] = unrealsdk.find_class("Pawn")
    manager = _INVENTORY(pawn) if pawn.Class._inherits(pawn_class) else getattr(pawn, "InvManager", None)
    item = getattr(manager, "ItemChain", None) if manager is not None else None
    for _ in range(32):                                 # the equipped items' chain
        if item is None:
            return False
        data = getattr(item, "DefinitionData", None)
        if data is not None and data.GammaItemPartDefinition == capacitor:
            return True
        item = item.Inventory
    return False


def _is_resistance_list(entries: Any) -> bool:
    """A capacitor list that holds all three of BL2's immunity capacitors (not one element's own)."""
    return _RESISTANCES <= {e.Part._path_name() for e in entries if e.Part is not None}


# ---------------------------------------------------------------------------------------------- relics


def _relic_presentations(pkg: UObject) -> None:
    """The relic stats' labels ("Cryo Damage", "Cryo Resistance"), in BL2's list of relic labels."""
    plist = _get("AttributePresentationListDefinition", _PRESENTATION_LIST)
    found = attributes.ensure_attributes()
    added = []
    for attribute_path, (template_path, name, text, icon) in RELIC_PRESENTATIONS.items():
        mine = _ours_in(pkg, "AttributePresentationDefinition", name, _get("AttributePresentationDefinition", template_path))
        mine.Attribute = found[attribute_path]
        mine.Description = texts.picked("gd_attributepresentation", f"Artifacts.{name} AttributePresentationDefinition",
                                        "Description", text)
        mine.Icon = cardicons.row_frame() if icon is None else icon
        if not any(p is not None and p == mine for p in plist.Attributes):
            added.append(mine)
    if added:
        plist.Attributes = [*list(plist.Attributes), *added]


def _relic_slots() -> int:
    """The new slot on each relic definition: a copy of the slot it is modelled on, for the cryo attribute, and its
    item-card line. Idempotent; returns how many definitions changed."""
    found = attributes.ensure_attributes()
    count = 0
    for definition_path, from_slot, to_slot, attribute_path, _lists, _parts in RELICS:
        definition = _get("ArtifactDefinition", definition_path)
        effects = [copy.copy(e) for e in definition.AttributeSlotEffects]
        if any(str(e.SlotName) == to_slot for e in effects):
            continue
        source = next(e for e in effects if str(e.SlotName) == from_slot)
        modelled_on = source.AttributeToModify
        attribute = found[attribute_path]
        effect = copy.copy(source)
        effect.SlotName = to_slot
        effect.AttributeToModify = attribute
        stats = [copy.copy(s) for s in definition.UIStatList]
        stat = next((s for s in stats if s.Attribute == modelled_on), None)
        definition.AttributeSlotEffects = [*effects, effect]
        if stat is not None and not any(s.Attribute == attribute for s in stats):
            line = copy.copy(stat)
            line.Attribute = attribute
            definition.UIStatList = [*stats, line]
        count += 1
    return count


def _relic_twins() -> dict[str, dict[str, UObject]]:
    """Part collection path -> {BL2 enable part path -> the one for the new slot}."""
    out = {}
    for _definition, _from, _to, _attribute, lists, parts in RELICS:
        twins = {src: _get("ArtifactPartDefinition", dst) for src, dst in parts.items()
                 if _find("ArtifactPartDefinition", src) is not None}
        for path in lists:
            out[path] = twins
    return out


# ---------------------------------------------------------------------------------------------- rolling


_LISTS = ("ItemPartListDefinition", "ItemPartListCollectionDefinition")
_Twins = tuple[dict[str, UObject], dict[str, UObject], dict[str, dict[str, UObject]]]


def _list_twins() -> _Twins:
    """BL2 part path -> its cryo twin: the grenade accessories, the shock capacitor, and for each relic collection
    its enable parts."""
    return ({path: _twins[path] for path in ACCESSORIES if path in _twins}, {_SHOCK_CAPACITOR: _built[CAPACITOR]},
            _relic_twins())


def _join(obj: UObject, cls: str, all_twins: _Twins) -> bool:
    """Cryo's entries added to one part list or collection. True if it changed."""
    path = obj._path_name()
    grenade, capacitor, relic = all_twins
    if cls == "ItemPartListDefinition":
        entries = obj.WeightedParts
        twins = grenade if _rolls_grenade_element(entries) else capacitor if _is_resistance_list(entries) else None
        fresh = _with_twins(path, entries, twins) if twins else None
        if fresh is not None:
            obj.WeightedParts = fresh
        return fresh is not None
    changed = False
    for slot in ITEM_SLOTS:
        data = getattr(obj, f"{slot}PartData", None)
        if data is None or not data.bEnabled:
            continue
        entries = data.WeightedParts
        twins = relic.get(path) or (grenade if _rolls_grenade_element(entries)
                                    else capacitor if _is_resistance_list(entries) else None)
        fresh = _with_twins(f"{path}:{slot}", entries, twins) if twins else None
        if fresh is not None:
            setattr(obj, f"{slot}PartData", unrealsdk.make_struct(data._type.Name, bEnabled=True, WeightedParts=fresh))
            changed = True
    return changed


def _unpatch_lists() -> int:
    """ "Cryo Item Drops" on No: what was added to BL2's part lists and collections comes out again, and they are as
    BL2 has them. Returns how many changed."""
    count = 0
    for path, cls in list(_patched.items()):
        if cls not in _LISTS:
            continue
        del _patched[path]                              # looked at again when the setting goes back to Yes
        obj = _find(cls, path)
        if obj is None:
            continue
        if cls == "ItemPartListDefinition":
            kept = _without(path, obj.WeightedParts)
            if kept is not None:
                obj.WeightedParts = kept
                count += 1
            continue
        changed = False
        for slot in ITEM_SLOTS:
            data = getattr(obj, f"{slot}PartData", None)
            kept = _without(f"{path}:{slot}", data.WeightedParts) if data is not None else None
            if kept is not None:
                setattr(obj, f"{slot}PartData",
                        unrealsdk.make_struct(data._type.Name, bEnabled=bool(data.bEnabled), WeightedParts=kept))
                changed = True
        count += changed
    return count


def _patch_lists(rolls: bool | None = None) -> int:
    """Cryo entries in every loaded part list and collection that rolls a random grenade element, BL2's three immunity
    capacitors, or a relic effect cryo joins. Idempotent; returns how many changed. While "Cryo Item Drops" is on
    No, the entries come out instead (`rolls`: the setting, when it is just being changed)."""
    if not (guns.drops_option.value if rolls is None else rolls):
        return _unpatch_lists()
    all_twins = _list_twins()
    count = 0
    for cls in _LISTS:
        for obj in unrealsdk.find_all(cls, exact=False):
            path = obj._path_name()
            if path in _patched or path in _passed or path in _ours or obj.Name.startswith("Default__"):
                continue
            if _join(obj, cls, all_twins):
                _patched[path] = cls
                count += 1
            else:
                _passed.add(path)
    return count


def _pool_ref(entry: Any) -> UObject | None:
    return entry.ItmPoolDefinition or entry.InvBalanceDefinition


def _twin_pool(pool: UObject) -> UObject:
    pkg = items.package(PACKAGE)
    name = pool.Name.replace("Shock", "Ice") if "Shock" in pool.Name else f"{pool.Name}_Ice"
    twin = _ours_in(pkg, "ItemPoolDefinition", name, pool, twin_of=pool)
    entries = []
    for entry in pool.BalancedItems:
        entry = copy.copy(entry)
        ref = _pool_ref(entry)
        mine = _twins.get(ref._path_name()) if ref is not None else None
        if entry.ItmPoolDefinition is not None:
            entry.ItmPoolDefinition = mine
        else:
            entry.InvBalanceDefinition = mine
        entries.append(entry)
    twin.BalancedItems = entries
    return twin


def _unpatch_pools() -> int:
    """ "Cryo Item Drops" on No: BL2's pools lose the entries for our pools and balances. Nothing owned depends on a
    pool: it is only where things drop from. Returns how many pools changed."""
    count = 0
    for path, cls in list(_patched.items()):
        if cls != "ItemPoolDefinition":
            continue
        del _patched[path]                              # looked at again when the setting goes back to Yes
        pool = _find(cls, path)
        if pool is None:
            continue
        entries = list(pool.BalancedItems)
        kept = [copy.copy(e) for e in entries
                if (ref := _pool_ref(e)) is None or ref._path_name() not in _ours]
        if len(kept) != len(entries):
            pool.BalancedItems = kept
            count += 1
    return count


def _patch_pools(rolls: bool | None = None) -> int:
    """Cryo shields drop where shock's do: a pool that holds only shock Nova or Spike balances (or such pools) gets a
    cryo twin, and every other pool that holds one gets an entry for its twin, with the same probability. Idempotent;
    returns how many pools changed or were made. While "Cryo Item Drops" is on No, the entries come out instead."""
    if not (guns.drops_option.value if rolls is None else rolls):
        return _unpatch_pools()
    # A pool that had nothing for cryo is passed by from then on (`_pools_passed`, by path: what loads again at a
    # path is the same pool). Going through all of them again at every map start took a quarter of a second (§6.44).
    pools = []
    for pool in unrealsdk.find_all("ItemPoolDefinition", exact=False):
        path = pool._path_name()
        if path not in _pools_passed and not pool.Name.startswith("Default__"):
            pools.append((path, pool))
    count = 0
    grew = True
    while grew:                                         # pools of pools: until no more twins appear
        grew = False
        for path, pool in pools:
            if path in _twins or path in _ours:
                continue
            refs = [_pool_ref(e) for e in pool.BalancedItems]
            if refs and all(r is not None and r._path_name() in _twins for r in refs):
                _twin_pool(pool)
                grew = True
                count += 1
    for path, pool in pools:
        if path in _twins or path in _ours:
            continue
        entries = list(pool.BalancedItems)
        have = {r._path_name() for r in (_pool_ref(e) for e in entries) if r is not None}
        added = []
        for entry in entries:
            ref = _pool_ref(entry)
            twin = _twins.get(ref._path_name()) if ref is not None else None
            if twin is None or twin._path_name() in have or twin.Class.Name not in ("ItemPoolDefinition",
                                                                                   "InventoryBalanceDefinition"):
                continue
            entry = copy.copy(entry)
            if entry.ItmPoolDefinition is not None:
                entry.ItmPoolDefinition = twin
            else:
                entry.InvBalanceDefinition = twin
            added.append(entry)
            have.add(twin._path_name())
        if added:
            pool.BalancedItems = [*[copy.copy(e) for e in entries], *added]
            _patched[path] = "ItemPoolDefinition"
            count += 1
        elif path not in _patched:
            _pools_passed.add(path)
    return count


# ---------------------------------------------------------------------------------------------- registration


def build() -> bool:
    """Everything that exists once: the cryo objects, registered for saving. True when built."""
    if _built.get(CAPACITOR) is not None and _built.get("Shield_Spike_Ice") is not None:
        return True
    pkg = items.package(PACKAGE)
    grenade_assets = _build_grenades(pkg)
    definitions, shield_parts, balances = _build_shields(pkg)
    where = {lib: items.append_to_base(lib, pkg, assets)
             for lib, assets in ((items.LIB_ITEM_TYPES, definitions), (items.LIB_ITEM_PARTS, grenade_assets + shield_parts),
                                 (items.LIB_BALANCE, balances))}
    _log(f"built {len(grenade_assets)} grenade parts and names, {len(definitions)} shields with "
         f"{len(shield_parts)} parts and names and {len(balances)} balances; sublibraries at {where}")
    for label, add in (("relic", _relic_presentations), ("grenade", _grenade_presentation)):
        try:
            add(pkg)
        except Exception as ex:  # noqa: BLE001 - labels only
            _once(f"labels:{label}", f"{label} labels not added: {type(ex).__name__}: {ex}")
    return True


def patch() -> dict[str, int]:
    """What is loaded now gets cryo: grenade slots and names, relic slots, part lists and pools. Idempotent; each step
    on its own, so one's fault never keeps the others."""
    done = {}
    for label, step in (("grenade definitions", _grenade_slots), ("name lists", _grenade_titles),
                        ("relics", _relic_slots), ("part lists", _patch_lists), ("pools", _patch_pools)):
        try:
            done[label] = step()
        except Exception as ex:  # noqa: BLE001 - loot never blocks the rest of the mod
            _once(f"{label}:{type(ex).__name__}", f"{label} not patched: {type(ex).__name__}: {ex}")
    if any(done.values()):
        _log(f"cryo added: {done}")
    return done


def register() -> None:
    """At the main menu (before any save is read) and at each map start."""
    try:
        build()
    except Exception as ex:  # noqa: BLE001 - never block the rest of the mod over loot
        _once(f"build:{type(ex).__name__}", f"not built: {type(ex).__name__}: {ex}")
        return
    patch()
    if wears_immunity not in element.IMMUNE:
        element.IMMUNE.append(wears_immunity)


def exported() -> list[UObject]:
    """The loaded BL2 part lists and collections cryo was added to (for the save editor's data)."""
    found = [_find(cls, path) for path, cls in _patched.items() if cls != "ItemPoolDefinition"]
    return [obj for obj in found if obj is not None]


def _dump(dumps: saveedit.Dumps) -> None:
    """For the save editor's data: BL2's grenade, capacitor and relic lists cryo joined."""
    for obj in exported():
        if obj.Class.Name == "ItemPartListCollectionDefinition":
            dumps.item_part_collection(obj)
        else:
            dumps.item_part_list(obj)


saveedit.CONTRIBUTORS["cryo gear"] = _dump


def _drops_changed(rolls: bool) -> None:
    """ "Cryo Item Drops" was switched in the menu (guns.py calls this with the new value): lists and pools follow."""
    if _built.get(CAPACITOR) is None:
        return
    lists = _patch_lists(rolls)
    pools = _patch_pools(rolls)
    _log(f"cryo item drops {'on' if rolls else 'off'}: {lists} part lists and {pools} pools changed")


if _drops_changed not in guns.ON_DROPS:
    guns.ON_DROPS.append(_drops_changed)


@hook(maps.ARRIVED, Type.POST)
@hook(maps.NEW_WORLD, Type.POST)
def _on_new_map(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    # Lists, pools and grenade parts unloaded with the old map are forgotten (by path: never touch a stale object).
    for path, cls in list(_patched.items()):
        if _find(cls, path) is None:
            del _patched[path]
    _titled.intersection_update({p for p in _titled if _find("Object", p) is not None})
    register()


# --- the game's check of a saved item (see guns.py's `_check_as_shock`): each part must be in the list its balance
# rolls that part from. A cryo grenade's accessory, the Warming capacitor and a cryo relic's enable part are in BL2's
# lists as copies of BL2's own entries, and only while "Cryo Item Drops" is on Yes; the item is checked with those
# BL2 parts in their place, and so passes as it would with cryo in the lists. Like the guns' hook it only ever
# answers yes, only where the game itself would say no, and not at all while another mod reads saved items in the
# game's place (Sanity Saver; guns.py has why); on a no the game is left to its own answer. A relic's enable part can
# stand for two of BL2's (the same part opens the damage relic's cryo slot and is one of the resistance relic's own),
# so each is tried.
_PART_FIELDS = tuple(f"{slot}ItemPartDefinition" for slot in ITEM_SLOTS)


def _copied() -> list[tuple[UObject, UObject]]:
    """(a part of ours that goes into BL2's lists, the BL2 part its entries copy), once they are built."""
    if not _copies and _built.get(CAPACITOR) is not None:
        grenade, capacitor, relic = _list_twins()
        seen = set()
        for twins in (grenade, capacitor, *relic.values()):
            for path, twin in twins.items():
                pair = (twin._path_name(), path)
                source = None if pair in seen else _find(twin.Class.Name, path)
                seen.add(pair)
                if source is not None:
                    _copies.append((twin, source))
    return _copies


@hook("WillowGame.WillowPlayerController:ValidateItemDefinition")
def _check_as_shock(obj: UObject, args: WrappedStruct, _ret: Any, _func: BoundFunction) -> Any:
    copies = _copied()
    if not copies:
        return None
    data = args.DefinitionData
    fields, choices = [], []
    for field in _PART_FIELDS:
        part = getattr(data, field, None)
        sources = [theirs for mine, theirs in copies if mine == part] if part is not None else []
        if sources:
            fields.append(field)
            choices.append(sources)
    if not fields or items.check_taken_over():
        return None
    with prevent_hooking_direct_calls():
        if obj.ValidateItemDefinition(copy.copy(data)):
            return None                          # its parts are in the lists: the game says yes itself
    for choice in itertools.product(*choices):
        asked = copy.copy(data)
        for field, source in zip(fields, choice, strict=True):
            setattr(asked, field, source)
        with prevent_hooking_direct_calls():
            if obj.ValidateItemDefinition(asked):
                return Block, True
    return None


# --- item cards: the cryo icon (cardicons.py puts TPS's snowflake into BL2's card movie)
_SHOCK_ROW = "elementShock"             # the icon of the rows a cryo shield shares with its shock twin
_card_is_cryo_shield = [False]


def _made_here(obj: UObject | None) -> bool:
    return obj is not None and obj._path_name().startswith(PACKAGE + ".")


def is_cryo(item: UObject) -> bool:
    """A cryo grenade (its accessory is one of ours), a cryo nova or spike shield (its definition is), or a cryo
    damage relic (the Elemental relic with its cryo slot enabled)."""
    data = item.DefinitionData
    definition = data.ItemDefinition
    if definition is None:
        return False
    if _made_here(definition):
        return True
    kind = item.Class.Name
    if kind == "WillowGrenadeMod":
        return _made_here(data.DeltaItemPartDefinition)
    if kind == "WillowArtifact":
        relic, _from, _to, _attribute, _lists, parts = RELICS[0]
        first = data.AlphaItemPartDefinition
        return definition._path_name() == relic and first is not None and first._path_name() in parts.values()
    return False


@hook("WillowGame.WillowEquipAbleItem:ClientInitializeFromItemDefinition", Type.POST)
def _item_frame(obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    """An item's title icon is its element's frame of the card movie, named natively from an enum BL2 has no cryo value
    in: a cryo grenade or relic got none, a cryo shield its shock twin's (until 0.3.2)."""
    if cardicons.ready and is_cryo(obj):
        obj.ElementalFrame = cardicons.TITLE_FRAME


@hook("WillowGame.ItemCardGFxObject:SetItemCardEx", Type.PRE)
def _card_begins(_obj: UObject, args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    item = args.InventoryItem
    _card_is_cryo_shield[0] = (cardicons.ready and item is not None and item.Class.Name == "WillowShield"
                               and _made_here(item.DefinitionData.ItemDefinition))


@hook("WillowGame.ItemCardGFxObject:SetItemCardEx", Type.POST)
def _card_ends(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    _card_is_cryo_shield[0] = False


@hook("WillowGame.ItemCardGFxObject:SetTopStat", Type.PRE)
def _card_row(_obj: UObject, args: WrappedStruct, _ret: Any, func: BoundFunction) -> Any:
    """A cryo shield's "Nova Damage" or "Spike Damage" row is its shock twin's (the same attribute, so the same
    label and icon): on its card the row takes the cryo icon."""
    if not _card_is_cryo_shield[0] or str(args.IconName) != _SHOCK_ROW:
        return None
    with prevent_hooking_direct_calls():
        func(args.StatIndex, args.LabelText, args.ValueText, args.CompareArrow, args.AuxText, cardicons.ROW_FRAME)
    return Block


HOOKS = (_on_new_map, _check_as_shock, _item_frame, _card_begins, _card_ends, _card_row)
