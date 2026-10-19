"""Cryo guns (M3; RESEARCH.md §6.7): a cryo element for BL2's six weapon types, rolled like the others.

TPS's `<Type>_Elemental_Ice` parts are BL2's `<Type>_Elemental_Shock` parts with another damage type: the
same WeaponDamage effect per type (-20%, none on shotguns), the same kind of name prefixes (per manufacturer)
and elemental barrel accessory. So each cryo part is built at runtime from BL2's shock part, with:
- TPS's cryo damage type (element.py) and white emissive glow (TPS's value; an E-tech gun gets a brighter white,
  on its own material: `_etech_glow`, 0.5.5);
- TPS's cryo name prefixes ("Chilly", "Frozen", "Arctic"...), per BL2 manufacturer;
- a neutral bullet impact and no first-person glow until TPS's ice effects are converted;
- shock's behaviours, which hand the gun its element once more, as a datum (`_behaviours`): what its projectiles
  deal. Without them (until 0.4.4) every cryo rocket, dart and blob was explosive.

Explosions: a gun whose shot explodes (a Maliwan pistol's splash, a rocket, most E-tech guns) picks the explosion from
a list by its element, and BL2's lists end at slag. TPS's have ice in them; each gets a cryo entry (`patch_explosions`).

Bandit never rolls cryo (the user, 2026-10-02: TPS's cryo Scav guns aren't Bandit's, and mixing the two reads
wrong). Its lists are left alone. Its prefixes (TPS's Scav names) are still built, so the weapon-parts sublibrary
keeps its order and a Bandit cryo gun dropped before the change still loads with its name.

Rolling: decided 2026-09-30, cryo rolls exactly like the other elements. Every loaded part list that picks a
random element (two or more elemental parts; fixed-element uniques are left alone) gets a copy of its shock
entry pointing at the cryo part: same weights, same game stages. Lists loaded later (DLC, level uniques) are
patched at each map start. Bandit's lists are skipped (above).

Saving: the parts and their prefixes sit in their own package, appended as a sublibrary to the base game's
weapon-parts library like the mod's other items (core/items.py), with the order kept per save by the layout keeper.

Enemies roll from the same lists. With the "Enemies can use cryo weapons" setting off, cryo entries are taken
out while an enemy's inventory is made and put back at the frame's end (`_take_out`, `_put_back`).

"Cryo Item Drops" on No (0.4.6): cryo is in no list. Its entries are taken out and none are added, so the lists
are BL2's own and another mod that changes them finds them as it expects (the user, 2026-10-04: "the entries should
not remain in the loot table"). What a player owns stays. The game checks a saved gun's parts against its balance's
lists when a save is read and throws away a gun with a part that isn't there (its "sanity check"; only a patched
game has it switched off), so the mod answers that check for a cryo gun as for the same gun with shock
(`_check_as_shock`): shock's entry is what cryo's was a copy of. That also keeps a Bandit cryo gun from before
Bandit's lists were left alone. gear.py does the same for grenades, capacitors and relics, and takes cryo shields
out of the pools.

Card: BL2 picks an elemental gun's chance and damage lines ("Electrocute Chance", "Shock Damage / sec.") through three
presentation selectors, each mapping the gun's damage type to its element's line. Cryo is mapped to TPS's lines,
built from shock's under TPS's paths ("Freeze Chance", "Freeze Damage / sec."). TPS draws them with an "elementIce"
icon that BL2's card doesn't have: cardicons.py gives it one, and until it has, they have none. The damage type's own
line is element.py's.
"""

from __future__ import annotations

import copy
from typing import Any

import unrealsdk
from mods_base import BoolOption, hook
from unrealsdk.hooks import Block, Type, prevent_hooking_direct_calls
from unrealsdk.unreal import BoundFunction, UObject, WeakPointer, WrappedStruct

from ..core import graphs, items, maps, saveedit, texts
from . import attributes, cardicons, element, explosions, tracers

PACKAGE = "GD_AureliaCryo"
TYPES = {"Pistol": "GD_Weap_Pistol", "SMG": "GD_Weap_SMG", "AR": "GD_Weap_AssaultRifle",
         "Shotgun": "GD_Weap_Shotgun", "SR": "GD_Weap_SniperRifles", "RL": "GD_Weap_Launchers"}
# TPS's cryo prefixes, by BL2 shock prefix folder (read from TPS Startup.upk, 2026-10-01).
PREFIXES = {
    "Pistol": {"Prefix": "Ice", "Prefix_Bandit": "Chilly", "Prefix_Dahl": "Ice", "Prefix_Hyperion": "Iced",
               "Prefix_Maliwan": "Frozen", "Prefix_Tediore": "Coldest", "Prefix_Vladof": "Ice"},
    "SMG": {"Prefix": "Ice", "Prefix_Bandit": "Frosty", "Prefix_Dahl": "Freezing", "Prefix_Hyperion": "Frigid",
            "Prefix_Maliwan": "Arctic", "Prefix_Tediore": "Frosted"},
    "AR": {"Prefix": "Ice", "Prefix_Bandit": "Frosty", "Prefix_Dahl": "Ice", "Prefix_Vladof": "Frozen"},
    "Shotgun": {"Prefix": "Ice", "Prefix_Bandit": "Frosty", "Prefix_Hyperion": "Chilled", "Prefix_Tediore": "Frozen"},
    "SR": {"Prefix": "Ice", "Prefix_Bandit": "Chilly", "Prefix_Dahl": "Frozen", "Prefix_Hyperion": "Chilled",
           "Prefix_Maliwan": "Chilled", "Prefix_Vladof": "Cold"},
    "RL": {"Prefix": "Ice", "Prefix_Bandit": "Chilly", "Prefix_Maliwan": "Frozen", "Prefix_Tediore": "Refreshing",
           "Prefix_Vladof": "Cold"},
}
IMPACT = "GD_Impacts.Bullets.Bullet_Generic"       # until TPS's Bullet_Ice is converted
EMISSIVE = (1.0, 1.0, 1.0, 1.0)                    # TPS's p_EmissiveColor for ice parts
GLOW = "p_EmissiveColor"
ETECH = "Alien"                                    # in the name of every E-tech barrel (Pistol_Barrel_Alien, ...)
ETECH_GAIN = 3.0                                   # an E-tech gun's glow: the trails' white, this many times over
# The card's lines: selector (GD_AttributePresentation.Weapons.WeapStatusEffectAttrPresent_<key>) -> TPS's presentation
# and its text (TPS Startup.upk, read 2026-10-03), each a copy of shock's (AttrPresent_WeaponShock<key>).
SELECTORS = "GD_AttributePresentation.Weapons.WeapStatusEffectAttrPresent_{}"
PRESENTATIONS = "GD_AttributePresentation.Weapons_ElementalDamage"
# ... and where TPS has their texts in its other languages (core/texts.py, `picked`): its file, and the section's
# group in it
CARD_TEXTS = ("gd_attributepresentation", "Weapons_ElementalDamage.{} AttributePresentationDefinition")
CARD_LINES = {
    "CombinedStatusEffectChance": ("AttrPresent_WeaponFreezeCombinedStatusEffectChance", "Freeze Chance"),
    "StatusEffectChance": ("AttrPresent_WeaponFreezeStatusEffectChance", "Freeze Chance"),
    "StatusEffectDamage": ("AttrPresent_WeaponFreezeStatusEffectDamage", "Freeze Damage / sec."),
}

enemy_option = BoolOption(
    "Enemies can use cryo weapons",
    True,
    display_name="Enemies use cryo guns",       # short enough for the options menu under its group's title
    description=("On: enemies roll cryo guns like any other element and can freeze you, as in The Pre-Sequel. "
                 "Off: cryo guns are player loot only."),
)
# The description fits the options menu's three lines. The user's name for it and its choices (2026-10-04).
drops_option = BoolOption(
    "Cryo Item Drops", True, true_text="Yes", false_text="No",
    description=("Yes: cryo guns, grenades, shields and relics drop like other elements. No: cryo is taken out of "
                 "the loot lists (for other loot mods). What you own stays."),
)
ON_DROPS: list[Any] = []                   # called with the new setting when it changes (gear.py: its lists and pools)

BANDIT = "GD_Manufacturers.Manufacturers.Bandit"
DATUM = "DamageTypeOverride"                       # the datum a gun's projectiles ask it for: its element

_parts: dict[str, UObject] = {}            # type -> cryo part
_copies: list[tuple[UObject, UObject]] = []    # (cryo part, the shock part it is a copy of)
_shock_to_cryo: dict[str, UObject] = {}    # shock part path -> cryo part
# part list path -> the list object, weakly: a list that came with a map goes with it, and until the next map's
# start has looked (`_on_new_map`) an enemy armed on the way would read it ("Enemies use cryo guns" off)
_patched: dict[str, WeakPointer] = {}
_passed: set[str] = set()                  # part lists looked at and left alone (no random element, or Bandit's)
_tails: dict[str, list[Any]] = {}          # part list path -> its cryo entries (copies): they are its last ones
_removed: list[str] = []                   # part lists with cryo taken out while enemies are armed
_said: set[str] = set()


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] cryo guns: {msg}")


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _make(cls: str, pkg: UObject, name: str, template: UObject) -> UObject:
    obj = _find(cls, f"{PACKAGE}.{name}")
    if obj is None:
        obj = unrealsdk.construct_object(cls, pkg, name, 0, template)
    obj.ObjectFlags = int(obj.ObjectFlags) | 0x4000          # RF_RootSet
    return obj


def _prefix(pkg: UObject, kind: str, shock_prefix: UObject) -> UObject:
    folder = shock_prefix.Outer.Name                          # Prefix, Prefix_Bandit, Prefix_Dahl, ...
    part = _make("WeaponNamePartDefinition", pkg, f"{kind}_{folder}_Cryo", shock_prefix)
    part.PartName = PREFIXES[kind].get(folder, PREFIXES[kind]["Prefix"])
    return part


def _behaviours(shock: UObject, part: UObject, damage: UObject) -> UObject | None:
    """Shock's behaviours, for the cryo part. They are one step: when the gun is made, it gets a datum named
    `DamageTypeOverride` with the element's damage type (TPS's ice parts have the same, with ice). A gun's projectiles
    ask their owner for that datum and deal what it says; with none they deal their own default, which is explosive
    for every rocket and E-tech projectile. The part had no behaviours until 0.4.4 (they were taken for the glow's),
    so those were explosive (the report, 2026-10-04; a cryo Dart's hits measured before and after)."""
    bpd = graphs.clone(shock.BehaviorProviderDefinition, part, part.BehaviorProviderDefinition)
    for behavior in graphs.behaviors(bpd, "Behavior_AddInstanceData"):
        data = []
        for entry in behavior.InstanceData:
            entry = copy.copy(entry)
            if str(entry.Name) == DATUM:
                entry.Object = damage
            data.append(entry)
        behavior.InstanceData = data
    return bpd


def build() -> dict[str, UObject]:
    """The six cryo parts (and their prefixes), built once and registered for saving."""
    if len(_parts) == len(TYPES):
        return _parts
    damage = element.ensure_objects()[element.IMPACT]
    impact = _find("WillowImpactDefinition", IMPACT)
    pkg = items.package(PACKAGE)
    assets: list[UObject] = []
    for kind, weapons in TYPES.items():
        shock = unrealsdk.find_object("WeaponPartDefinition", f"{weapons}.elemental.{kind}_Elemental_Shock")
        part = _make("WeaponPartDefinition", pkg, f"{kind}_Elemental_Cryo", shock)
        prefixes = [_prefix(pkg, kind, p) for p in shock.PrefixList if p is not None]
        part.CustomDamageTypeDefinition = damage
        part.PrefixList = prefixes
        part.WeaponImpact = impact
        part.FirstPersonParticle = None                      # shock's glow; TPS's ice glow isn't converted yet
        part.BehaviorProviderDefinition = _behaviours(shock, part, damage)
        r, g, b, a = EMISSIVE
        colors = list(part.MaterialVectorParameterValues)
        for c in colors:
            if str(c.ParameterName) == "p_EmissiveColor":
                c.ParameterValue = unrealsdk.make_struct("LinearColor", R=r, G=g, B=b, A=a)
        part.MaterialVectorParameterValues = colors
        _parts[kind] = part
        _copies.append((part, shock))
        _shock_to_cryo[shock._path_name()] = part
        assets += [part, *prefixes]
    where = items.append_to_base(items.LIB_WEAPON_PARTS, pkg, assets)
    _log(f"built {len(_parts)} cryo parts ({len(assets)} assets), weapon-parts sublibrary {where}")
    try:
        _log(f"cryo card lines in {card_lines(damage)} of {len(CARD_LINES)} selectors"
             + (", [ice] text tag added" if ice_markup() else ""))
    except Exception as ex:  # noqa: BLE001 - a card without its lines is no reason to stop the loot
        _log(f"no cryo card lines: {type(ex).__name__}: {ex}")
    return _parts


# TPS's [ice] text tag (its TextMarkupDictionary's; BL2's has none), in BL2's elemental tags' style (no font size)
TEXT_MARKUP = "GD_Globals.UI.TheTextMarkupDictionary"
ICE_MARKUP = ("ice", '<font color="#00FFFF">', "</font>")


def ice_markup() -> bool:
    """BL2's text markup gets TPS's [ice] tag (cards and texts that say "[ice]Cryo"). Idempotent; True if added now."""
    markup = unrealsdk.find_object("TextMarkupDictionary", TEXT_MARKUP)
    tag, begin, end = ICE_MARKUP
    entries = [copy.copy(e) for e in markup.Dictionary]
    if any(str(e.MarkupTag) == tag for e in entries):
        return False
    entry = copy.copy(entries[0])
    entry.MarkupTag, entry.HTMLMarkupBeginText, entry.HTMLMarkupEndText = tag, begin, end
    markup.Dictionary = [*entries, entry]
    return True


def card_lines(damage: UObject) -> int:
    """Map `damage` (the cryo damage type) to TPS's cryo presentations in BL2's three card-line selectors.
    Idempotent; returns how many selectors it changed."""
    changed = 0
    for key, (name, text) in CARD_LINES.items():
        selector = unrealsdk.find_object("WeaponStatusEffectAttributePresentationDefinition", SELECTORS.format(key))
        shock = unrealsdk.find_object("AttributePresentationDefinition", f"{PRESENTATIONS}.AttrPresent_WeaponShock{key}")
        mine = _find("AttributePresentationDefinition", f"{PRESENTATIONS}.{name}")
        if mine is None:
            mine = unrealsdk.construct_object("AttributePresentationDefinition", shock.Outer, name, 0, shock)
        mine.ObjectFlags = int(mine.ObjectFlags) | 0x4000          # RF_RootSet
        mine.Description = texts.picked(CARD_TEXTS[0], CARD_TEXTS[1].format(name), "Description", text)
        mine.Icon = cardicons.row_frame()      # TPS's "elementIce", once BL2's card movie has that frame
        entries = [copy.copy(e) for e in selector.MappingData]
        if any(e.DamageType == damage and e.Presentation == mine for e in entries):
            continue
        template = next((e for e in entries if e.Presentation == shock), entries[0] if entries else None)
        if template is None:
            _log(f"{selector.Name} maps no element: left alone")
            continue
        entry = copy.copy(template)
        entry.DamageType = damage
        entry.Presentation = mine
        selector.MappingData = [*(e for e in entries if e.DamageType != damage), entry]
        changed += 1
    return changed


def _eligible(entries: Any) -> bool:
    """A list that picks a random element: two or more elemental parts besides "none"."""
    names = {e.Part.Name for e in entries if e.Part is not None and not e.Part.Name.endswith("_Elemental_None")}
    return len(names) >= 2


def _copy_entry(src: Any, part: UObject) -> WrappedStruct:
    """A new weighted-part entry like `src` (weights, game stages, manufacturers) for `part`. Built fresh:
    appending an element of the same array could read it after the array has moved."""
    fields = {"Part": part, "Manufacturers": list(src.Manufacturers)}
    for name in ("MinGameStageIndex", "MaxGameStageIndex", "DefaultWeightIndex", "MinGameStage", "MaxGameStage",
                 "DefaultWeight"):
        if hasattr(src, name):
            fields[name] = getattr(src, name)
    return unrealsdk.make_struct("PartGradeWeightData", **fields)


def _makers(coll: Any) -> list[str]:
    """The manufacturers of the balance a part list belongs to (a rarity balance names none: its base does)."""
    balance = coll.Outer
    for _ in range(8):
        if balance is None or not balance.Class.Name.endswith("WeaponBalanceDefinition"):   # also mission weapons'
            return []
        makers = [m.Manufacturer._path_name() for m in balance.Manufacturers if m.Manufacturer is not None]
        if makers:
            return makers
        balance = balance.BaseDefinition
    return []


def _for_bandit(coll: Any) -> bool:
    """A list Bandit guns roll from: its balance's manufacturer, else its weapon type or its own name (a mission
    weapon's list, measured 2026-10-02: Medical Mystery's E-tech rifle is a Bandit one)."""
    makers = _makers(coll)
    if makers:
        if BANDIT in makers and len(set(makers)) > 1 and "mixed" not in _said:
            _said.add("mixed")
            _log(f"a list shared by Bandit and others gets no cryo: {coll._path_name()}")
        return BANDIT in makers
    weapon_type = getattr(coll, "AssociatedWeaponType", None)
    names = (coll._path_name(), weapon_type._path_name() if weapon_type is not None else "")
    return any("_Bandit_" in name for name in names)


def _add_cryo(coll: Any) -> bool:
    """Add a cryo entry next to each shock entry of one random-element list. True if it changed."""
    data = coll.ElementalPartData
    entries = data.WeightedParts
    if not data.bEnabled or not _eligible(entries) or _for_bandit(coll):
        return False
    have = {e.Part._path_name() for e in entries if e.Part is not None}
    new = [_copy_entry(e, _shock_to_cryo[e.Part._path_name()]) for e in entries
           if e.Part is not None and e.Part._path_name() in _shock_to_cryo
           and _shock_to_cryo[e.Part._path_name()]._path_name() not in have]
    if not new:
        return False
    for entry in new:
        entries.append(entry)
    coll.ElementalPartData = data                             # write back, in case the read was a copy
    _tails[coll._path_name()] = new
    return True


def patch_part_lists(rolls: bool | None = None) -> int:
    """Give every loaded random-element part list a cryo entry (a copy of its shock entry). Idempotent. Nothing
    while "Cryo Item Drops" is on No (`rolls`: the setting, when it is just being changed)."""
    if not _shock_to_cryo or not (drops_option.value if rolls is None else rolls):
        return 0
    count = 0
    for coll in unrealsdk.find_all("WeaponPartListCollectionDefinition", exact=False):
        path = coll._path_name()
        if path in _patched or path in _passed or coll.Name.startswith("Default__"):
            continue
        if _add_cryo(coll):
            _patched[path] = WeakPointer(coll)
            count += 1
        elif _cryo_entries(coll):
            _patched[path] = WeakPointer(coll)   # it has cryo already (the mod's modules loaded again)
        else:
            _passed.add(path)                    # nothing for cryo here: not looked at again (44 ms a map start)
    if count:
        _log(f"cryo added to {count} part lists ({len(_patched)} in all)")
    return count


# The lists a gun's explosion is picked from by its element (`ExplosionCollectionDefinition`): one entry per element,
# each a test of the weapon's damage type and that element's explosion. The firing mode names its list; the gun keeps
# the pick as a datum, and a shot's explosion is that one. BL2 has five lists (the shared one, the Maliwan launchers'
# nova, and the Spiker's, Plasma Caster's and Splatgun's own), each ending at slag; with no entry for it, a cryo gun's
# shot exploded with what the shot itself names, an explosive one. TPS's lists have ice where BL2's have slag: the
# ice master, and for the Maliwan launchers the ice nova (the Cryo Nova shield's). The same here, as one more entry;
# the two E-tech lists TPS doesn't have get the ice master, as its Splatgun's does.
_IS_SHOCK = "WeaponDamage_Is_Shock"


def _asks(entry: Any) -> str | None:
    """The weapon-damage attribute an explosion list's entry tests."""
    evaluator = entry.Expression
    attribute = evaluator.Expression.AttributeOperand1 if evaluator is not None else None
    return attribute.Name if attribute is not None else None


def patch_explosions() -> int:
    """A cryo entry in every loaded explosion list that has a shock one. Idempotent; returns how many it changed."""
    is_ice = attributes.ensure_attributes()[attributes.WEAPON_IS_ICE]
    effects = explosions.ensure_effects()
    count = 0
    for coll in unrealsdk.find_all("ExplosionCollectionDefinition", exact=False):
        if coll.Name.startswith("Default__"):
            continue
        entries = [copy.copy(e) for e in coll.Explosions]
        asked = [_asks(e) for e in entries]
        if is_ice.Name in asked or _IS_SHOCK not in asked:
            continue
        shock = entries[asked.index(_IS_SHOCK)]
        evaluator = unrealsdk.construct_object(shock.Expression.Class, coll, "AttributeExpressionEvaluator_Ice", 0,
                                               shock.Expression)
        evaluator.ObjectFlags = int(evaluator.ObjectFlags) | 0x4000          # RF_RootSet
        expression = copy.copy(evaluator.Expression)
        expression.AttributeOperand1 = is_ice
        evaluator.Expression = expression
        entry = copy.copy(shock)
        entry.Expression = evaluator
        nova = shock.Explosion is not None and "Nova" in shock.Explosion.Name
        entry.Explosion = effects[explosions.NOVA if nova else explosions.EXPLOSION]
        coll.Explosions = [*entries, entry]
        count += 1
    if count:
        _log(f"cryo added to {count} explosion lists")
    return count


def register() -> None:
    """At the main menu and each map start: build the parts, patch whatever part, explosion and trail lists are
    loaded."""
    try:
        build()
        patch_part_lists()
        patch_explosions()
        tracers.patch()
    except Exception as ex:  # noqa: BLE001 - never block the rest of the mod over loot
        if "register" not in _said:
            _said.add("register")
            _log(f"not ready: {type(ex).__name__}: {ex}")


@hook(maps.ARRIVED, Type.POST)
@hook(maps.NEW_WORLD, Type.POST)
def _on_new_map(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    for path in list(_patched):
        coll = _find("WeaponPartListCollectionDefinition", path)
        if coll is None or not _ends_in_cryo(coll):          # unloaded with the old map, or loaded afresh
            _patched.pop(path, None)
            _tails.pop(path, None)
        else:
            _patched[path] = WeakPointer(coll)               # the object that is at that path now
    _put_back()                                              # what a last frame of the old map left out
    register()


def _cryo_entries(coll: Any) -> list[int]:
    cryo_paths = {p._path_name() for p in _parts.values()}
    return [i for i, e in enumerate(coll.ElementalPartData.WeightedParts)
            if e.Part is not None and e.Part._path_name() in cryo_paths]


def _is_cryo(part: Any) -> bool:
    return part is not None and any(part == p for p in _parts.values())


def _ends_in_cryo(coll: Any) -> bool:
    parts = coll.ElementalPartData.WeightedParts
    return len(parts) > 0 and _is_cryo(parts[len(parts) - 1].Part)


def _tail(coll: Any) -> list[Any]:
    """The cryo entries at a list's end, as copies that can be appended again."""
    parts = coll.ElementalPartData.WeightedParts
    found = []
    for i in range(len(parts) - 1, -1, -1):
        if not _is_cryo(parts[i].Part):
            break
        found.append(_copy_entry(parts[i], parts[i].Part))
    return found[::-1]


def _take_out() -> None:
    """Cryo out of every list it was added to. Its entries are each list's last (`_add_cryo` appends them), and
    copies of them are kept, so this pops them off the end; a list that doesn't end in cryo is left as it is."""
    for path, ref in _patched.items():
        coll = ref()
        if coll is None:
            continue                             # gone with its map: the next map's start drops it from the table
        tail = _tails.get(path)
        if tail is None:
            tail = _tails[path] = _tail(coll)
        if not tail or not _ends_in_cryo(coll):
            continue
        parts = coll.ElementalPartData.WeightedParts
        for _ in tail:
            parts.pop()
        _removed.append(path)


def _put_back() -> None:
    while _removed:
        path = _removed.pop()
        ref = _patched.get(path)
        coll = ref() if ref is not None else None
        if coll is None:
            continue
        parts = coll.ElementalPartData.WeightedParts
        for entry in _tails.get(path, ()):
            parts.append(entry)


def _remove_cryo() -> int:
    """ "Cryo Item Drops" on No: cryo comes out of every list it was added to, for good; the lists are BL2's again.
    Its entries are each list's last ones (`_take_out`), so they come off the end. Returns how many lists changed."""
    _put_back()                                  # a frame's enemies had it out: back first, then all of it goes
    count = 0
    for path, ref in _patched.items():
        coll = ref()
        if coll is None:
            continue
        tail = _tails.get(path)
        if tail is None:
            tail = _tail(coll)
        if not tail or not _ends_in_cryo(coll):
            continue
        parts = coll.ElementalPartData.WeightedParts
        for _ in tail:
            parts.pop()
        count += 1
    _patched.clear()                             # every list is looked at again when the setting goes back to Yes
    _tails.clear()
    return count


def _drops_changed(_option: BoolOption, rolls: bool) -> None:
    """ "Cryo Item Drops" was switched (the new value comes before the option holds it): the lists follow at once."""
    if not _parts:
        return                                   # nothing built yet: what is built later asks the option itself
    try:
        count = patch_part_lists(rolls) if rolls else _remove_cryo()
        _log(f"cryo item drops {'on' if rolls else 'off'}: cryo {'added to' if rolls else 'taken out of'} "
             f"{count} gun lists")
    except Exception as ex:  # noqa: BLE001 - a setting never breaks the menu
        _log(f"cryo item drops not applied to the gun lists: {type(ex).__name__}: {ex}")
    for listener in ON_DROPS:
        try:
            listener(rolls)
        except Exception as ex:  # noqa: BLE001
            _log(f"cryo item drops not applied ({getattr(listener, '__module__', '?')}): {type(ex).__name__}: {ex}")


drops_option.on_change_while_enabled = _drops_changed


# An E-tech gun is mostly glow: its barrel's shell, its sights and its seams are drawn with the element's colour, and
# BL2's four elements give one at three to four times full strength (fire 4.0, 0.3, 0; shock 0.1, 1.2, 3.6;
# corrosive 0.5, 3.1, 0; slag 0.6, 0.2, 4.3), which is what makes those parts shine. TPS's cryo colour is plain white
# at 1 (TPS has no E-tech guns: its cryo guns have a stripe to colour, no more), so a cryo Dart's shell was a dull
# grey net (a Nexus comment, 2026-10-06: "I got a Dart with cryo on it ... and the texture on it broke"; the same
# element on a Rubi "worked perfectly fine"). The element part is one for all of a weapon type's guns, so the
# brighter white is given to the one gun: when the game has built a gun's material (once for each gun it makes),
# a gun with cryo's part and an E-tech barrel gets the trails' white (tracers.WHITE) at ETECH_GAIN on that
# material. Only where the glow reads as cryo's own: a colour another part or another mod has put there is left.
@hook("WillowGame.WillowWeapon:BuildWeaponMaterial", Type.POST)
def _etech_glow(obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    data = obj.DefinitionData
    if not _is_cryo(data.ElementalPartDefinition):
        return
    barrel, material = data.BarrelPartDefinition, obj.WeaponMaterial
    if barrel is None or material is None or ETECH not in barrel.Name:
        return
    known, now = material.GetVectorParameterValue(GLOW, unrealsdk.make_struct("LinearColor"))
    if not known or any(abs(float(have) - want) > 0.01 for have, want in zip((now.R, now.G, now.B), EMISSIVE)):
        return
    r, g, b = (ETECH_GAIN * c for c in tracers.WHITE)
    material.SetVectorParameterValue(GLOW, unrealsdk.make_struct("LinearColor", R=r, G=g, B=b, A=EMISSIVE[3]))


# Until 0.4.5 this took cryo out and put it back around every single enemy, each time by going through all of the
# 195 lists entry by entry and building them again: 130 ms an enemy with the setting off (measured, §6.44), a freeze
# at every spawn. Now the entries go out once, when the first enemy of a frame is armed, and back at that frame's
# end: two short passes for a whole wave. Loot rolled in between, in that same frame, has no cryo in it.
@hook("WillowGame.WillowAIPawn:AddDefaultInventory")
def _before_enemy_inventory(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    """Setting off: no cryo entries while enemies' guns are rolled. (With cryo drops off there are none.)"""
    if enemy_option.value or _removed or not drops_option.value:
        return
    _take_out()
    if _removed:
        _restore.enable()


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def _restore(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    """The frame's end, after enemies were armed without cryo: it goes back. On only for that one call."""
    _restore.disable()
    _put_back()


# The game's check of a saved gun (WillowPlayerController.ValidateWeaponDefinition, the "sanity check"): each part must
# be in the list its balance rolls that part from, or the gun is thrown away as the save is read. Measured with the
# check live (2026-10-04; it is patched out of the game copy the mod is developed on, and was put back in memory for
# this): a cryo gun passes while cryo is in its list and fails once it is out; with this hook it passes again, and
# a gun with a barrel of another weapon type still fails. A hook's change to the argument doesn't reach the game's
# function, so the hook asks again itself, with shock in cryo's place.
# It only ever answers yes. A game patched to skip the check (a common patch; the copy the mod is developed on has
# it) answers no to whatever is asked of it this way, and keeps every gun all the same: handed back, that no threw
# her cryo guns away as her save was read (the first run of this hook). On a no the game is left to its own answer.
# And it only answers where the game itself would say no, and no other mod reads saved items in the game's place
# (0.4.13). Until then it said yes for every cryo gun, whoever asked. Sanity Saver asks with the check blocked, to
# have the game's reader make nothing while it makes every gun itself: our yes got through its block (a hook's
# return value stands whatever the other hooks do), the game made the cryo guns too, and they were there twice
# after every load (RESEARCH.md §6.59).
@hook("WillowGame.WillowPlayerController:ValidateWeaponDefinition")
def _check_as_shock(obj: UObject, args: WrappedStruct, _ret: Any, _func: BoundFunction) -> Any:
    """A cryo gun the game would throw away is checked as the same gun with shock: cryo's entry in a list is a copy
    of shock's, so the answer is the one cryo would get there ("Cryo Item Drops" on No; Bandit's lists)."""
    part = args.DefinitionData.ElementalPartDefinition
    if part is None:
        return None
    shock = next((twin for mine, twin in _copies if mine == part), None)
    if shock is None or items.check_taken_over():
        return None
    asked = copy.copy(args.DefinitionData)
    with prevent_hooking_direct_calls():
        if obj.ValidateWeaponDefinition(asked):
            return None                          # cryo is in its list: the game says yes itself
        asked.ElementalPartDefinition = shock
        passes = bool(obj.ValidateWeaponDefinition(asked))
    return (Block, True) if passes else None


def stop() -> None:
    """The mod is switched off in the MODS menu: cryo goes back into the lists if this frame's enemies had it out,
    and the hook that would have put it back is turned off (it is not one of the mod's listed hooks)."""
    _restore.disable()
    _put_back()


def _dump(dumps: saveedit.Dumps) -> None:
    """For the save editor's data: the weapon part lists cryo was added to."""
    for ref in _patched.values():
        coll = ref()
        if coll is not None:
            dumps.weapon_part_collection(coll)


saveedit.CONTRIBUTORS["cryo guns"] = _dump

# _restore is not listed: _before_enemy_inventory turns it on
HOOKS = (_on_new_map, _before_enemy_inventory, _check_as_shock, _etech_glow)
OPTIONS = (drops_option, enemy_option)
