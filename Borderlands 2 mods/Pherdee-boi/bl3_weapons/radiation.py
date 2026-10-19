"""Radiation: a new element for BL2 guns, after Borderlands 3's.

In BL3 radiation is a damage over time (8 seconds, 24 ticks) that makes the enemy a source of damage for the ones around it (its aura hurts
them for as long as it lasts, about as much as the damage over time itself), and an irradiated enemy that dies explodes and irradiates the
enemies the blast hurts. Against enemy health types it does 100% to flesh, 120% to shields and 70% to armour (150% and 50% in the harder
playthroughs). The numbers are the ones the BL3 wiki and the guides give (checked 2026-10-14; the aura and blast radius there is about 2 m).

BL2 has no radiation, so the element is built at start-up out of BL2's own corrosive objects (copies, nothing of the game is edited): a
damage type with its own damage-number colour and card lines, a damage-over-time status effect, and for every gun type an elemental part
(`<Type>_Elemental_Radiation`, next to the corrosive one, with name prefixes such as "Irradiated", "Fallout" or "Chernobyl"). A gun with
that part does radiation damage and every hit infects the enemy. Its card icon is drawn in code (elementicons.py). The aura, the death
blast and the older "chain infection" are done here: the status effect's own spread settings only passed the status on now and then.

Radiation guns also drop (the Drop Chance), and they keep the element when the game is saved and loaded. The radiation parts are made at
run time, and the game saves a gun's parts as places in its part lists (and tells other players of an object by a network index) that
objects made at run time do not have: a saved radiation gun would come back with no element at all. So the mod remembers which guns
are radioactive by the unique number every gun has (core.state, "radiation:<number>") and gives the element back whenever such a gun
is found: held, loaded from the save, or picked up. Maliwan guns can switch to and from radiation like any element (maliwan.py).

The looks are the mod's own (the Cryo mod's RadiationFX package, cryo/radfx.py): the gas and bubbles on an irradiated enemy, the death cloud
and blast with its Geiger sound, the bullet impact (small gas and bubbles on top of BL2's plain bullet impact), the yellow muzzle flashes, tracers
and trails (BL2's effect lists get a radiation entry), and the explosions of rockets and E-tech bullets (BL2's explosion lists get one too, with
radiation's damage type). Grenade mods and shields are radiation for real too (radgear.py). What is still corrosive's: the status effect's
screen look when the player is hit, and the card's element line text (see ASSETS in the README of the mod).
"""

import copy
import math
import random
import re
import time
import traceback

import unrealsdk
from mods_base import DropdownOption, SliderOption, command, get_pc, hook, keybind
from ui_utils import show_hud_message
from unrealsdk.hooks import Type, prevent_hooking_direct_calls
from unrealsdk.unreal import UObject, WeakPointer

from . import radiation_fx
from .core import find_part, held_weapon, log, now as game_now, pawns, rebuild, state
from .jakobs import GUN_DAMAGE

TYPES = {"Pistol": "GD_Weap_Pistol", "SMG": "GD_Weap_SMG", "AR": "GD_Weap_AssaultRifle", "Shotgun": "GD_Weap_Shotgun",
         "SR": "GD_Weap_SniperRifles", "RL": "GD_Weap_Launchers"}  # BL2's gun types and their packages (the corrosive part is <package>.elemental.<Type>_Elemental_Corrosive)
PREFIXES = {"": "Irradiated", "Bandit": "Glowy", "Dahl": "Fallout", "Hyperion": "Glowing", "Maliwan": "Radiant", "Tediore": "Leaky", "Vladof": "Chernobyl"}  # name prefix by manufacturer
YELLOW = (0.839, 0.906, 0.098)  # #D6E719, the colour of the element everywhere in this mod (the player's pick): damage numbers, glow, card, icon
GLOW = (2.52, 2.72, 0.29)  # the gun's emissive colour (the corrosive part's is 0.51, 3.07, 0.0)
TEXT_COLOUR = "#D6E719"  # the same yellow as HTML text
SUFFIX = "_Elemental_Radiation"
PLAIN_BALANCE = r"^GD_Weap_(Pistol|SMG|AssaultRifle|Shotgun|SniperRifles|Launchers)\.A_Weapons\.(\w+_(2_Uncommon|3_Rare|4_VeryRare)|\w+_Maliwan)$"  # plain guns only (no uniques, legendaries, aliens); Maliwan's white guns have no tier in their name and always carry an element
TEMPLATE_POOL = "GD_Itempools.WeaponPools.Pool_Weapons_SMG_04_Rare"  # same pickup-free generator trick as the HeartShatter and the Adventure Guide
TEMPLATE_MISSION = "GD_Episode05.M_Ep5_ThePhoenix"
BLAST_EXPLOSION = "GD_Explosions.corrosive.Explosion_CorrosiveMaster"  # the death blast's look for now (corrosive's): its colour is not changeable at run time (see art/ASSETS.md)
TICKS_PER_SECOND = 3.0  # BL3: 24 ticks in 8 seconds
BL3_MODE, CHAIN_MODE = "Aura and death blast (BL3)", "Chain infection"
GUN_TYPES = r"(Pistol|SMG|AssaultRifle|Shotgun|SniperRifles|Launchers)"
ETECH_BALANCE = rf"^GD_Weap_{GUN_TYPES}\.A_Weapons\.\w+_(5_Alien|3_Rare_Alien)$"  # E-tech guns
LEGENDARY_BALANCE = rf"^GD_Weap_{GUN_TYPES}\.A_Weapons_Legendary\.\w+$"  # the legendary guns that are not one-off uniques (Conference Call, Skullmasher...)
LEFT_OUT = ("Torgue",)  # makers whose guns never get radiation for now (the player's wish, 2026-10-14: their shots are explosions)
# The card's elemental stat rows ("Corrode Chance", "Corrode Damage / sec.") are picked by three selectors that map a gun's damage type to the
# element's line (GD_AttributePresentation.Weapons.WeapStatusEffectAttrPresent_<key>); radiation is added to each, with lines copied from corrosive's.
ROW_PACKAGE = "GD_AttributePresentation.Weapons_ElementalDamage"
ROW_SELECTOR = "GD_AttributePresentation.Weapons.WeapStatusEffectAttrPresent_{}"
ROW_LINES = {  # selector key -> (corrosive's line it is copied from, its text)
    "StatusEffectDamage": ("AttrPresent_WeaponCorrosiveStatusEffectDamage", "Radiation Damage / sec."),
    "StatusEffectChance": ("AttrPresent_WeaponCorrosiveStatusEffectChance", "Radiation Chance"),
    "CombinedStatusEffectChance": ("AttrPresent_WeaponCorrosiveCombinedStatusEffectChance", "Radiation Chance"),
}

drop_chance = SliderOption(
    "Radiation Drop Chance",
    6,
    0,
    30,
    1,
    True,
    description="Percent of new uncommon, rare and epic guns (world drops, chests, vendors; not white, e-tech, unique or legendary ones) that roll with the radiation element. 6 is about what each BL2 element has among them (measured 2026-10-19). 0 turns radiation drops off.",
)
etech_drop_chance = SliderOption(
    "Radiation E-tech Drop Chance",
    16,
    0,
    30,
    1,
    True,
    description="Percent of new E-tech guns and Maliwan guns (the ones that always have an element; world drops, chests, vendors) that roll with the radiation element. 16 makes it as common as each of the other elements (they have 17 to 21 each; measured 2026-10-19). 0 turns it off.",
)
legendary_drop_chance = SliderOption(
    "Radiation Legendary Drop Chance",
    8,
    0,
    30,
    1,
    True,
    description="Percent of new legendary guns that roll with the radiation element, keeping their name. Only the legendaries that roll an element at all (Conference Call, Lyudmila...): not the ones with a fixed element or none (Hornet, Maggie...). 8 is about what each BL2 element has on them (measured 2026-10-19). 0 turns it off.",
)
# Legendaries that never roll radiation: their element, or having none, is part of what they are (the other elements, cryo included, never roll on them either).
FIXED_LEGENDARIES = frozenset({
    "Pistol_Dahl_5_Hornet", "Pistol_Dahl_5_Hector_Hornet", "Pistol_Bandit_5_Gub", "Pistol_Hyperion_5_LogansGun", "Pistol_Maliwan_5_ThunderballFists",
    "Pistol_Vladof_5_Infinity_DD", "RL_Maliwan_5_Pyrophobia", "SMG_Bandit_5_Slagga", "SMG_Maliwan_5_HellFire", "Sniper_Maliwan_5_Volcano",
    "AR_Jakobs_5_HammerBuster", "Pistol_Jakobs_5_Maggie", "SG_Jakobs_5_Striker", "Sniper_Jakobs_5_Skullmasher"})
spread_mode = DropdownOption(
    "Radiation Spread",
    BL3_MODE,
    [BL3_MODE, CHAIN_MODE],
    description="BL3: an irradiated enemy hurts the enemies around it and explodes when it dies, irradiating those the blast hurts. Chain infection: it passes the radiation itself on to its neighbours every second.",
)
duration = SliderOption(
    "Radiation Duration",
    8,
    2,
    20,
    1,
    True,
    description="Seconds the radiation damage-over-time lasts on an enemy (renewed by every hit). BL3: 8.",
)
aura_radius = SliderOption("Radiation Aura Radius", 3, 1, 10, 1, True, description="Metres around an irradiated enemy that its aura hurts. BL3: about 2.")
aura_damage = SliderOption("Radiation Aura Damage", 100, 0, 300, 10, True, description="Percent of the damage over time that the aura deals to each enemy near an irradiated one. 0 turns the aura off. BL3: about 100.")
blast_radius = SliderOption("Radiation Death Blast Radius", 3, 1, 10, 1, True, description="Metres around an irradiated enemy that dies in which its blast hurts and irradiates. BL3: about 2.")
blast_damage = SliderOption("Radiation Death Blast Damage", 100, 0, 300, 10, True, description="Percent of the damage over time times three, dealt by the blast of an irradiated enemy that dies. 0 turns the blast off.")
cloud_duration = SliderOption("Radiation Cloud Duration", 8, 0, 20, 1, True, description="Seconds the radiation cloud that an irradiated enemy leaves where it dies lasts. 0 leaves no cloud.")
cloud_radius = SliderOption("Radiation Cloud Radius", 3, 1, 10, 1, True, description="Metres around the spot where an irradiated enemy died in which its cloud hurts enemies.")
cloud_damage = SliderOption("Radiation Cloud Damage", 50, 0, 300, 10, True, description="0 turns the cloud off. Enemies standing in a radiation cloud are irradiated: they take the radiation damage over time (the gun's damage), renewed each second.")
spread_radius = SliderOption("Radiation Chain Radius", 6, 2, 20, 1, True, description="Chain infection only: how far, in metres, an infected enemy passes radiation on to its neighbours.")
spread_count = SliderOption("Radiation Chain Count", 2, 1, 6, 1, True, description="Chain infection only: how many new enemies one infected enemy infects each second.")
OPTIONS = [drop_chance, etech_drop_chance, legendary_drop_chance, spread_mode, duration, aura_radius, aura_damage, blast_radius, blast_damage, cloud_duration, cloud_radius, cloud_damage, spread_radius, spread_count]

# ---- the element -------------------------------------------------------------------------------------------------------------

_built: dict | None = None  # {"status", "impact", "status_dt", "parts": {type: part}} once build() has run

_RF_ROOTSET = 0x4000  # the flag that keeps an object out of the garbage collector


def _root(obj: UObject) -> UObject:
    """Keep a run-time object alive. Nothing in the game refers to these objects until a gun uses one, and a Python reference does not count:
    after a map change the game cleared them, a gun's part turned into another object, and the game crashed on the next radiation hit or
    roll (2026-10-13)."""
    if not int(obj.ObjectFlags) & _RF_ROOTSET:
        obj.ObjectFlags = int(obj.ObjectFlags) | _RF_ROOTSET
    return obj


def _existing(cls: str, outer: UObject, name: str) -> UObject | None:
    """The object `name` inside `outer` if there is one. A name in use must never be constructed again: the engine stops the game
    with a fatal error (2026-10-13). Inside a package the path separator is a dot, inside an object it is a colon."""
    sep = "." if outer.Class.Name == "Package" else ":"
    try:
        return unrealsdk.find_object(cls, f"{outer._path_name()}{sep}{name}")
    except ValueError:
        return None


def _clone(template: UObject, outer: UObject, name: str) -> UObject:
    """A copy of `template` called `name` inside `outer`; the same object when it already exists (a second build, a hot reload)."""
    found = _existing(template.Class.Name, outer, name)
    if found is not None:
        return _root(found)
    return _root(unrealsdk.construct_object(template.Class, outer, name, 0, template))


def _colour(struct, rgb: tuple[float, float, float]) -> None:
    struct.R, struct.G, struct.B = rgb


def _still_alive(built: dict) -> bool:
    """True while the built objects are still the ones registered under their names."""
    try:
        status = built["status"]
        if unrealsdk.find_object("StatusEffectDefinition", status._path_name())._get_address() != status._get_address():
            return False
        return all(unrealsdk.find_object("WeaponPartDefinition", part._path_name())._get_address() == part._get_address() for part in built["parts"].values())
    except Exception:  # noqa: BLE001
        return False


def playthrough_modifiers() -> tuple[float, float, float]:
    """Damage against flesh, armour and shields (BL3's radiation: 100/70/120% in the first playthrough, 100/50/150% in the harder ones)."""
    pc = get_pc(possibly_loading=True)
    try:
        harder = int(pc.GetCurrentPlaythrough()) > 0 if pc is not None else False
    except Exception:  # noqa: BLE001
        harder = False
    return (1.0, 0.5, 1.5) if harder else (1.0, 0.7, 1.2)


def _apply_numbers(built: dict) -> None:
    """The things that change with the playthrough and the options: the health-type multipliers, the status's duration and the card text.
    The copied damage types would otherwise keep corrosive's (more against armour, much less against shields), which are tables the game
    reads by playthrough; they are replaced by plain numbers."""
    flesh, armour, shield = playthrough_modifiers()
    for dt in (built["impact"], built["status_dt"]):
        for field, value in (("FleshDamageModifier", flesh), ("ArmorDamageModifier", armour), ("ShieldDamageModifier", shield)):
            modifier = getattr(dt, field)
            modifier.InitializationDefinition = None
            modifier.BaseValueAttribute = None
            modifier.BaseValueConstant = value
    built["status"].BaseDuration.BaseValueConstant = float(duration.value)
    lines = ["Spreads to nearby enemies."] if spread_mode.value == CHAIN_MODE else ["Hurts nearby, explodes on death."]
    for card, text in zip(built["cards"], lines + [""] * len(built["cards"])):
        card.Description = text
    for index, card in enumerate(built["cards"]):
        card.BasePriority = 0.15 + 0.01 * (len(built["cards"]) - index)  # the first line first
    try:
        from . import elementicons

        icon = elementicons.ROW_FRAME if elementicons.ready else "elementCorrosive"  # the stat rows' icon: radiation's own once the card movie has it
    except Exception:  # noqa: BLE001
        icon = "elementCorrosive"
    for line in built["rows"].values():
        line.Icon = icon


def ensure_rows(built: dict) -> None:
    """The card's chance and damage rows (see ROW_LINES). Made at the first map, not at start-up: the game crashed in the front end when this
    read the card selectors there (2026-10-14: an access violation in pyunrealsdk while the mod loaded)."""
    if built["rows"]:
        return
    f = unrealsdk.find_object
    impact = built["impact"]
    rows = built["rows"]
    for key, (source, text) in ROW_LINES.items():
        corrosive_line = f("AttributePresentationDefinition", f"{ROW_PACKAGE}.{source}")
        line = _clone(corrosive_line, corrosive_line.Outer, source.replace("Corrosive", "Radiation"))
        line.Description = text
        rows[key] = line
        selector = f("WeaponStatusEffectAttributePresentationDefinition", ROW_SELECTOR.format(key))
        mapping = list(selector.MappingData)
        if not any(e.DamageType is not None and e.DamageType._get_address() == impact._get_address() for e in mapping):
            mapping.append(unrealsdk.make_struct(mapping[0]._type.Name, Presentation=line, DamageType=impact))
            selector.MappingData = mapping
    _apply_numbers(built)


ACID = ("Behavior_ActivateSkill", "Behavior_DeactivateSkill")  # the behaviours that start and end corrosive's green acid on the skin


def _strip_acid(status: UObject) -> None:
    """The clone of corrosive's status keeps its sound and its pain reaction but not its acid look: that is radiation's gas now (radiation_fx.puff)."""
    for field in ("OnApplication", "OnDurationBegin", "OnDurationEnd"):
        try:
            items = list(getattr(status, field))
            kept = [b for b in items if b.Class.Name not in ACID]
            if len(kept) != len(items):
                setattr(status, field, kept)
                log(f"radiation status: {field} {[b.Class.Name for b in items]} -> {[b.Class.Name for b in kept]}")
        except Exception:  # noqa: BLE001
            log(f"radiation status: could not strip the acid from {field}: " + traceback.format_exc()[-200:])
    try:
        status.StatusEffectType = 0  # corrosive's type (2) makes the pawn attach corrosive's acid drips; 0 keeps the damage over time (checked 2026-10-18 with a weapon as its source) and loses the drips
    except Exception:  # noqa: BLE001
        log("radiation status: could not clear the status effect type: " + traceback.format_exc()[-200:])


def _own_behavior(part: UObject, corrosive: UObject, impact: UObject) -> None:
    """The clone of corrosive's part has corrosive's behavior provider, and its "DamageTypeOverride" (what rockets and other projectiles read before
    the part's own damage type) is corrosive: a radiation rocket launcher did corrosive damage (found 2026-10-18). The part gets its own copy of the
    provider and of that behavior, with radiation's impact as the override."""
    bpd = corrosive.BehaviorProviderDefinition
    if bpd is None:
        return
    own = _clone(bpd, corrosive.Outer, f"{part.Name}_BehaviorProvider")
    for sequence in own.BehaviorSequences:
        for data in sequence.BehaviorData2:
            behavior = data.Behavior
            if behavior is None or behavior.Class.Name != "Behavior_AddInstanceData":
                continue
            copy_ = _clone(behavior, own, f"{part.Name}_AddInstanceData")
            for entry in copy_.InstanceData:
                if entry.Name == "DamageTypeOverride":
                    entry.Object = impact
            data.Behavior = copy_
    part.BehaviorProviderDefinition = own


def build() -> dict | None:
    """Make the radiation objects (once); None when BL2's corrosive parts are not there."""
    global _built
    if _built is not None and _still_alive(_built):
        return _built
    _built = None
    f = unrealsdk.find_object
    impact_base = f("WillowDamageTypeDefinition", "GD_Corrosive.DamageType.DmgType_Corrosive_Impact")
    status_dt_base = f("WillowDamageTypeDefinition", "GD_Corrosive.DamageType.DmgType_Corrosive_Status")
    status_base = f("StatusEffectDefinition", "GD_Corrosive.StatusEffect.Status_Corrosive")
    impact = _clone(impact_base, impact_base.Outer, "DmgType_Radiation_Impact")
    status_dt = _clone(status_dt_base, status_dt_base.Outer, "DmgType_Radiation_Status")
    status = _clone(status_base, status_base.Outer, "Status_Radiation")
    status.DamageTypeDefinition = status_dt
    impact.StatusEffect = status
    status.bCanSpread = False  # spreading is done in this module
    _strip_acid(status)
    for dt in (impact, status_dt):
        _colour(dt.DamageColor, YELLOW)
        dt.HUDDamageColor.R, dt.HUDDamageColor.G, dt.HUDDamageColor.B = (round(c * 255) for c in YELLOW)
    template = list(impact_base.WeaponCardPresentations)[0]  # corrosive's one card line ("Highly effective vs Armor."): radiation has one too
    cards = []
    for index in range(1):
        card = _clone(template, impact, f"AttributePresentationDefinition_{index}")
        card.TextColor.R, card.TextColor.G, card.TextColor.B = (round(c * 255) for c in YELLOW)
        cards.append(card)
    impact.WeaponCardPresentations = cards
    parts = {}
    for kind, package in TYPES.items():
        corrosive = find_part(f"{package}.elemental.{kind}_Elemental_Corrosive")
        if corrosive is None:
            continue
        part = _clone(corrosive, corrosive.Outer, f"{kind}{SUFFIX}")
        part.CustomDamageTypeDefinition = impact
        _own_behavior(part, corrosive, impact)
        names = []
        for prefix in corrosive.PrefixList:
            maker = prefix.Outer.Name.removeprefix("Prefix").removeprefix("_")
            new = _clone(prefix, prefix.Outer, "Prefix_Elemental_Radiation")
            new.PartName = PREFIXES.get(maker, PREFIXES[""])
            names.append(new)
        part.PrefixList = names
        for entry in part.MaterialVectorParameterValues:
            if entry.ParameterName == "p_EmissiveColor":
                _colour(entry.ParameterValue, GLOW)
        parts[kind] = part
    _built = {"status": status, "impact": impact, "status_dt": status_dt, "cards": cards, "rows": {}, "parts": parts}
    _apply_numbers(_built)
    log(f"radiation: built the element ({len(parts)} gun types)")
    return _built


def is_radiation(weapon: UObject) -> bool:
    part = weapon.DefinitionData.ElementalPartDefinition
    return part is not None and part.Name.endswith(SUFFIX)


def gun_type(weapon: UObject) -> str | None:
    """'Pistol', 'SMG', 'AR', 'Shotgun', 'SR' or 'RL' from the gun's balance package."""
    bal = weapon.DefinitionData.BalanceDefinition
    path = bal._path_name() if bal is not None else ""
    for kind, package in TYPES.items():
        if path.startswith(package + "."):
            return kind
    return None


# ---- infection, aura, death blast and chain infection -----------------------------------------------------------------------------

# enemy address -> {"pawn": WeakPointer, "until": game time its infection ends, "hit": last time it was infected, "weapon": WeakPointer or None,
#                   "tick": the damage of its last damage-over-time tick (0 until one was seen), "loc": where it was last seen}
_hot: dict[int, dict] = {}
_last_scan = 0.0
_last_aura = 0.0
SCAN_EVERY = 0.25  # s: how often infected enemies are looked at (a death is noticed within this)
PUFF_EVERY = 1.0  # s between the gas and bubbles an irradiated enemy gives off
AURA_EVERY = 1.0  # s between the aura's hits (and between the chain infection's jumps)
_infecting = False
# the clouds left by dead irradiated enemies: {"centre": (x, y, z), "until": game time, "dps": the dead enemy's radiation damage per second, "last": last damage time, "weapon": WeakPointer or None}
_clouds: list[dict] = []
CLOUD_EVERY = 0.5  # s between a cloud's hits
MAX_CLOUDS = 12
CLOUD_PUFF_EVERY = 1.2  # s between the puffs that show a cloud
CLOUD_PUFFS = 3  # puffs each time
REINFECT = 0.4  # s: an enemy is not given the status again sooner (shotguns hit with many pellets)


def infect(pc: UObject, target: UObject, weapon: UObject | None) -> bool:
    """Put the radiation status on an enemy (renewing it) and remember it as infected."""
    global _infecting
    if _infecting:  # applying a status can deal damage of its own, which comes back through the hit hook
        return False
    built = _built or build()  # (the map-arrival hook keeps _built current: no name lookups on every hit)
    if built is None or not target.IsAliveAndWell():
        return False
    now = game_now()
    entry = _hot.get(target._get_address())
    if entry is not None and now - entry["hit"] < REINFECT:
        return False
    status = built["status"]
    status.BaseDuration.BaseValueConstant = float(duration.value)
    loc = target.Location
    _hot[target._get_address()] = {
        "pawn": WeakPointer(target), "until": now + duration.value, "hit": now, "weapon": WeakPointer(weapon) if weapon is not None else None,
        "tick": entry["tick"] if entry is not None else 0.0, "loc": (loc.X, loc.Y, loc.Z),
    }
    _infecting = True
    try:
        target.GetStatusEffectsComponent().ApplyStatusEffect(pc, unrealsdk.find_class("WillowDmgSource_Bullet"), status, weapon, None, False)
    finally:
        _infecting = False
    return True


def _dist(a: UObject, b: UObject) -> float:
    pa, pb = a.Location, b.Location
    return ((pa.X - pb.X) ** 2 + (pa.Y - pb.Y) ** 2 + (pa.Z - pb.Z) ** 2) ** 0.5


def _vec(x: float, y: float, z: float):
    return unrealsdk.make_struct("Vector", X=x, Y=y, Z=z)


def _enemies(pc: UObject, skip: set[int]) -> list[UObject]:
    """Every living enemy near the player's world, chosen before anything is hurt (a kill can change the pawn list)."""
    pawn = pc.Pawn
    mine = pawn._get_address()
    return [p for p in pawns(pawn) if p._get_address() != mine and p._get_address() not in skip and p.IsAliveAndWell() and pawn.IsEnemy(p)]


def _hurt(pc: UObject, victim: UObject, damage: float) -> None:
    """Damage dealt by the mod itself: it does not come back through the hit hook, and the player gets the credit."""
    with prevent_hooking_direct_calls():
        victim.TakeDamage(damage, pc, victim.Location, _vec(0, 0, 0), unrealsdk.find_class("WillowDmgSource_StatusEffect"))


def _near(centre: tuple[float, float, float], candidates: list[UObject], reach: float) -> list[UObject]:
    cx, cy, cz = centre
    out = []
    for p in candidates:
        loc = p.Location
        if ((loc.X - cx) ** 2 + (loc.Y - cy) ** 2 + (loc.Z - cz) ** 2) ** 0.5 <= reach:
            out.append(p)
    return out


def _blast(pc: UObject, entry: dict) -> None:
    """An irradiated enemy died: it explodes, hurting (3 x its damage per second) and irradiating the enemies around it."""
    dps = entry["tick"] * TICKS_PER_SECOND
    if dps <= 0 or blast_damage.value <= 0:
        return
    centre = entry["loc"]
    weapon = entry["weapon"]() if entry["weapon"] is not None else None
    victims = _near(centre, _enemies(pc, set()), blast_radius.value * 100.0)
    args = (pc.Pawn, blast_radius.value * 100.0, 1.0, True, 0.0, unrealsdk.find_class("WillowDmgSource_StatusEffect"), _vec(*centre), _vec(0, 0, 1),
            True, False, _vec(0, 0, 0), weapon or pc.Pawn, False, True, None, 0.0, 0.0)  # the look and the sound only: an explosion played from a script deals no damage
    played = False
    try:
        played = radiation_fx.play(YELLOW, *args, sound_on=entry["pawn"]())  # (the dead body: the sound comes from where it lay)  # slag's explosion turned into a yellow gas cloud for a few seconds
    except Exception:  # noqa: BLE001
        log("radiation fx failed: " + traceback.format_exc()[-300:])
    if not played:
        try:
            boom = unrealsdk.find_object("ExplosionDefinition", BLAST_EXPLOSION)
            boom.PlayExplosion(*args)
        except Exception:  # noqa: BLE001
            log("radiation blast effect failed: " + traceback.format_exc()[-200:])
    for victim in victims:
        _hurt(pc, victim, 3.0 * dps * blast_damage.value / 100.0)
        if victim.IsAliveAndWell():
            infect(pc, victim, weapon)
    log_once("blast", f"radiation: an irradiated enemy died and its blast hurt {len(victims)} enemies ({3.0 * dps * blast_damage.value / 100.0:.0f} each)")
    _leave_cloud(entry, dps)


def _leave_cloud(entry: dict, dps: float) -> None:
    """The cloud a dead irradiated enemy leaves: for a few seconds it hurts every enemy standing in it."""
    if cloud_duration.value <= 0 or cloud_damage.value <= 0 or dps <= 0:
        return
    now = game_now()
    _clouds.append({"centre": entry["loc"], "until": now + cloud_duration.value, "dps": dps, "last": now, "weapon": entry["weapon"]})
    del _clouds[:-MAX_CLOUDS]
    log_once("cloud", f"radiation: a cloud was left ({cloud_radius.value} m, {cloud_duration.value} s)")


def _cloud_tick(pc: UObject, now: float) -> None:
    """Hurt the enemies standing in a cloud (every CLOUD_EVERY seconds) and forget the clouds that have faded."""
    _clouds[:] = [c for c in _clouds if now < c["until"]]
    for cloud in _clouds:  # the cloud is seen as well as felt: smoke and bubbles standing over its ground, a few every CLOUD_PUFF_EVERY seconds
        if now >= cloud.get("puff", 0.0):
            cloud["puff"] = now + CLOUD_PUFF_EVERY
            cx, cy, cz = cloud["centre"]
            reach = cloud_radius.value * 100.0 * 0.6
            for _ in range(CLOUD_PUFFS):
                angle, dist = random.uniform(0, 6.283), reach * random.random() ** 0.5
                radiation_fx.spot(pc, (cx + dist * math.cos(angle), cy + dist * math.sin(angle), cz - 40.0))
    due = [c for c in _clouds if now - c["last"] >= CLOUD_EVERY]
    if not due:
        return
    candidates = _enemies(pc, set())
    for cloud in due:
        elapsed = now - cloud["last"]
        cloud["last"] = now
        damage = cloud["dps"] * elapsed * cloud_damage.value / 100.0
        weapon = (cloud["weapon"]() if cloud["weapon"] is not None else None) or held_weapon()
        for victim in _near(cloud["centre"], candidates, cloud_radius.value * 100.0):
            if victim.IsAliveAndWell():
                # standing in the cloud irradiates: the damage is the radiation status's own (yellow numbers, radiation's damage type), and the
                # victim then gives off its aura and blast like any other. Plain damage only where the status can't be given (no gun to take it from).
                if weapon is None or not infect(pc, victim, weapon):
                    _hurt(pc, victim, damage)
    log_once("cloud_hurt", "radiation: a cloud hurt the enemies in it")


def _aura(pc: UObject, sources: list[tuple[UObject, dict]]) -> None:
    """Every irradiated enemy hurts the enemies around it for about as much as its damage over time does (they are not irradiated by it)."""
    if aura_damage.value <= 0:
        return
    candidates = _enemies(pc, set())
    reach = aura_radius.value * 100.0
    for source, entry in sources:
        if entry["tick"] <= 0:
            continue
        damage = entry["tick"] * TICKS_PER_SECOND * AURA_EVERY * aura_damage.value / 100.0
        loc = source.Location
        for victim in _near((loc.X, loc.Y, loc.Z), candidates, reach):
            if victim._get_address() != source._get_address() and victim.IsAliveAndWell():
                _hurt(pc, victim, damage)
    log_once("aura", f"radiation: irradiated enemies hurt their neighbours (aura {aura_radius.value} m)")


def _chain(pc: UObject, sources: list[tuple[UObject, dict]]) -> None:
    """Chain infection: every infected enemy infects up to the Chain Count of the nearest not yet infected enemies within the Chain Radius."""
    candidates = _enemies(pc, set(_hot))
    reach = spread_radius.value * 100.0  # 1 metre = 100 Unreal units
    for source, entry in sources:
        loc = source.Location
        near = sorted(((_dist(source, p), p) for p in _near((loc.X, loc.Y, loc.Z), candidates, reach)), key=lambda t: t[0])
        for _, victim in near[: spread_count.value]:
            if victim._get_address() in _hot:
                continue
            infect(pc, victim, entry["weapon"]() if entry["weapon"] is not None else None)
            log_once("chain", "radiation: an infected enemy passed it on")


_logged: set[str] = set()


def log_once(key: str, msg: str) -> None:
    if key not in _logged:
        _logged.add(key)
        log(msg)


def scan(pc: UObject) -> None:
    """Look at the infected enemies: drop the ones whose infection ended, blast the ones that died, and every second run the aura (or the chain)."""
    global _last_aura
    pawn = pc.Pawn
    if pawn is None:
        _hot.clear()
        _clouds.clear()
        return
    now = game_now()
    bl3 = spread_mode.value != CHAIN_MODE
    if _clouds:
        _cloud_tick(pc, now)
    sources = []
    for addr, entry in list(_hot.items()):
        source = entry["pawn"]()
        alive = source is not None and source.IsAliveAndWell()
        if alive:
            loc = source.Location
            entry["loc"] = (loc.X, loc.Y, loc.Z)
        if now >= entry["until"]:
            del _hot[addr]
        elif not alive:
            del _hot[addr]
            if bl3:
                _blast(pc, entry)
        else:
            sources.append((source, entry))
            if now >= entry.get("puff", 0.0):
                entry["puff"] = now + PUFF_EVERY
                radiation_fx.puff(pc, source, YELLOW)
    if sources and now - _last_aura >= AURA_EVERY:
        _last_aura = now
        if bl3:
            _aura(pc, sources)
        else:
            _chain(pc, sources)


@hook("WillowGame.WillowAIPawn:TakeDamage", Type.POST_UNCONDITIONAL, hook_identifier="bl3w_radiation_hit")
def on_hit(obj: UObject, args, *_: object) -> None:
    """A hit from a radiation gun infects the enemy; a damage-over-time tick on an infected enemy tells its damage (for the aura and the blast)."""
    try:
        pc = get_pc(possibly_loading=True)
        if pc is None or pc.Pawn is None or args.DamageType is None:
            return
        name = args.DamageType.Name.removeprefix("WillowDmgSource_")
        if name == "StatusEffect":
            entry = _hot.get(obj._get_address())
            if entry is not None and float(args.Damage) > 0:
                entry["tick"] = float(args.Damage)
            return
        if args.InstigatedBy is None or args.InstigatedBy._get_address() != pc._get_address() or name not in GUN_DAMAGE:
            return
        weapon = held_weapon()
        if weapon is None or not is_radiation(weapon) or not pc.Pawn.IsEnemy(obj):
            return
        infect(pc, obj, weapon)
    except Exception:  # noqa: BLE001
        log("radiation hit failed: " + traceback.format_exc()[-300:])


@hook("WillowGame.WillowGameViewportClient:Tick", hook_identifier="bl3w_radiation_tick")
def on_tick(*_: object) -> None:
    global _last_scan
    radiation_fx.tick()
    if _built is not None and not _built.get("impact_done"):
        try:
            own_impact(_built)
        except Exception:  # noqa: BLE001
            log("radiation impact failed: " + traceback.format_exc()[-300:])
    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if pawn is not None:
        try:
            restore_inventory(pawn)
            restore_backpack(pawn)
            from . import radgear  # noqa: PLC0415 - radgear imports this module

            radgear.tick(pawn)
        except Exception:  # noqa: BLE001
            log("radiation inventory check failed: " + traceback.format_exc()[-300:])
    if not _hot and not _clouds:
        return
    now = game_now()
    if now - _last_scan < SCAN_EVERY:
        return
    _last_scan = now
    try:
        if pc is not None:
            scan(pc)
    except Exception:  # noqa: BLE001
        log("radiation scan failed: " + traceback.format_exc()[-300:])


def reset() -> None:
    _hot.clear()
    _clouds.clear()
    radiation_fx.reset()


# ---- radiation guns in the world ----------------------------------------------------------------------------------------------


def _key(unique_id: int) -> str:
    return f"radiation:{unique_id}"


def flagged(unique_id: int) -> bool:
    return _key(unique_id) in state.value


_any: bool | None = None  # whether any gun is remembered as radioactive (looking through every saved key each frame would cost too much)


def _has_flags() -> bool:
    global _any
    if _any is None:
        _any = any(k.startswith("radiation:") for k in state.value)
    return _any


PRUNE_DAYS = 90  # a flag whose gun has not been seen in the inventory for this many days is forgotten (the guns of the bank, the stash and other
# characters are seen when they come back; 2026-10-15: 94 of 135 flags belonged to guns that no longer exist)
_flush_at = 0.0
_pruned = False


def _today() -> int:
    return int(time.time() // 86400)


def flag(unique_id: int) -> None:
    global _any
    if not flagged(unique_id):
        state.value = {**state.value, _key(unique_id): ["1", str(_today())]}
        state.save()
        _any = True


def _touch(unique_id: int) -> None:
    """The gun is in the inventory today: its flag remembers the day (saved at most every 30 s)."""
    global _flush_at
    key = _key(unique_id)
    value = state.value.get(key)
    day = str(_today())
    if value is None or (len(value) > 1 and value[1] == day):
        return
    state.value = {**state.value, key: ["1", day]}
    if time.monotonic() >= _flush_at:
        _flush_at = time.monotonic() + 30.0
        state.save()


def prune_flags() -> int:
    """Forget the flags of guns that have not been seen for PRUNE_DAYS days. A flag from before the days were kept gets today's (nobody is
    pruned the first time). Called once a session, when the backpack has been looked through (every gun in it was seen today by then)."""
    global _pruned, _any
    if _pruned:
        return 0
    _pruned = True
    today = _today()
    kept: dict[str, list[str]] = {}
    gone = stamped = 0
    for key, value in state.value.items():
        if key.startswith("radiation:"):
            if len(value) < 2:
                value = ["1", str(today)]
                stamped += 1
            else:
                try:
                    if today - int(value[1]) > PRUNE_DAYS:
                        gone += 1
                        continue
                except ValueError:
                    value = ["1", str(today)]
        kept[key] = value
    if gone or stamped:
        state.value = kept
        state.save()
        _any = any(k.startswith("radiation:") for k in kept)
        log(f"radiation: {gone} flags of long-gone guns forgotten, {stamped} stamped with today ({sum(k.startswith('radiation:') for k in kept)} remain)")
    return gone


def unflag(unique_id: int) -> None:
    if flagged(unique_id):
        state.value = {k: v for k, v in state.value.items() if k != _key(unique_id)}
        state.save()


def note_element(weapon: UObject, element: str) -> None:
    """Called when a Maliwan gun switches element: it is remembered as radioactive while its element is radiation (a saved gun comes back
    without the element, and gets it back only if it is remembered)."""
    uid = weapon.DefinitionData.UniqueId
    if element == "Radiation":
        flag(uid)
    else:
        unflag(uid)


def _prefix(part: UObject, definition_data) -> UObject | None:
    """The name prefix an elemental part gives a gun of the definition's manufacturer (a legendary keeps the prefix it has: it is "Conference
    Call" with the radiation element, not "Fallout Conference Call")."""
    bal = definition_data.BalanceDefinition
    if bal is not None and re.match(LEGENDARY_BALANCE, bal._path_name()):
        current = definition_data.PrefixPartDefinition
        # an element's name ("Ice Infinity", a cryo roll) is dropped with the element; any other prefix is the legendary's own
        return None if current is not None and re.search(r"Elemental|Cryo|Fire|Shock|Corrosive|Slag|Radiation|Incendiary|Explosive", current.Name) else current
    maker = definition_data.ManufacturerDefinition.Name if definition_data.ManufacturerDefinition is not None else ""
    named = {x.Outer.Name.removeprefix("Prefix").removeprefix("_"): x for x in part.PrefixList}
    return named.get(maker, named.get(""))


def make_radiation(definition_data, kind: str):
    """A copy of a weapon definition with the radiation element of its gun type, remembered as radioactive (None when there is no such part)."""
    built = build()
    part = built["parts"].get(kind) if built else None
    if part is None:
        return None
    fresh = copy.copy(definition_data)
    fresh.ElementalPartDefinition = part
    fresh.PrefixPartDefinition = _prefix(part, fresh)  # the gun is named after its element ("Fallout Pistal"), whatever accessory prefix it rolled
    flag(fresh.UniqueId)
    return fresh


_impact_try = 0.0


def own_impact(built: dict) -> None:
    """The radiation guns' bullet impact: a copy of corrosive's impact definition whose effect is the Cryo mod's own (yellow gas and a spark), not corrosive's
    goop and PhysX fluid. Done once, as soon as that effect's materials are ready."""
    global _impact_try
    if built.get("impact_done"):
        return
    t = game_now()
    if t - _impact_try < 3.0:
        return
    _impact_try = t
    import sys  # noqa: PLC0415
    module = sys.modules.get(f"{__package__}.cryo.cryo.radfx")
    if module is None:
        return
    try:
        module.use_damage_type(built["impact"])  # the explosion lists get a radiation entry (rockets, E-tech bullets); again each try until the blast is ready
    except Exception:  # noqa: BLE001
        log("radiation explosions failed: " + traceback.format_exc()[-300:])
    if not module.impact_ready():
        return
    base = unrealsdk.find_object("WillowImpactDefinition", "GD_Impacts.Bullets.Bullet_Corrosive")
    definition = _clone(base, base.Outer, "Bullet_Radiation")
    definition.FallbackEffect.ParticleTemplate = module.impact_system()
    for part in built["parts"].values():
        part.WeaponImpact = definition
    if not module.explosions_ready():
        return
    built["impact_done"] = True
    log(f"radiation: the bullet impact is the mod's own now ({len(built['parts'])} gun types)")


def safe_build() -> None:
    """The mod's start-up: build the element, so that a saved gun finds its parts when the save loads."""
    try:
        build()
    except Exception:  # noqa: BLE001
        log("radiation build failed: " + traceback.format_exc()[-400:])
    try:
        from . import elementicons

        elementicons.apply()
        if _built is not None:
            _apply_numbers(_built)  # (the stat rows' icon is radiation's own once the card movie has it)
    except Exception:  # noqa: BLE001
        log("radiation icon failed: " + traceback.format_exc()[-400:])


def _left_out(definition_data) -> bool:
    maker = definition_data.ManufacturerDefinition
    return maker is not None and maker.Name in LEFT_OUT


def drop_rate(balance_path: str, maker: str | None = None) -> float:
    """Percent chance that a new gun of this balance rolls with radiation (0 when it never does): the plain guns' Drop Chance, or the E-tech and
    legendary guns' own one. Used by the roll and by the loot check (lootcheck.py)."""
    if maker in LEFT_OUT:
        return 0.0
    if re.match(PLAIN_BALANCE, balance_path):
        return float(etech_drop_chance.value if maker == "Maliwan" else drop_chance.value)  # (Maliwan's guns all have an element: radiation gets an even share)
    if re.match(ETECH_BALANCE, balance_path):
        return float(etech_drop_chance.value)
    if re.match(LEGENDARY_BALANCE, balance_path):
        return 0.0 if balance_path.rpartition(".")[2] in FIXED_LEGENDARIES else float(legendary_drop_chance.value)
    return 0.0


def roll_into_radiation(weapon: UObject) -> bool:
    """A brand-new plain gun becomes radioactive with the Drop Chance (called right after the game rolled its parts: ChooseRandomParts runs
    for new guns only, never for a saved gun loading)."""
    d = weapon.DefinitionData
    bal = d.BalanceDefinition
    maker = d.ManufacturerDefinition
    chance = drop_rate(bal._path_name(), maker.Name if maker is not None else None) if bal is not None else 0.0
    if chance <= 0 or random.random() * 100 >= chance:
        return False
    from . import heartshatter  # a HeartShatter keeps its cryo

    kind = gun_type(weapon)
    if kind is None or heartshatter.has_fingerprint(weapon):
        return False
    fresh = make_radiation(d, kind)
    if fresh is None:
        return False
    weapon.DefinitionData = fresh
    log(f"radiation: a new {bal.Name} rolled with radiation")
    return True


@hook("WillowGame.WillowWeapon:ChooseRandomParts", Type.POST, hook_identifier="bl3w_radiation_roll")
def on_roll(obj: UObject, *_: object) -> None:
    try:
        roll_into_radiation(obj)
    except Exception:  # noqa: BLE001
        log("radiation roll failed: " + traceback.format_exc()[-300:])


def restore(weapon: UObject) -> bool:
    """Give the radiation element back to a radioactive gun that was built without it (loaded from the save, where it was stored with none)."""
    d = weapon.DefinitionData
    if d is None or not flagged(d.UniqueId):
        return False
    _touch(d.UniqueId)
    if is_radiation(weapon):
        return False
    if _left_out(d):  # a gun from before the rule: it stays what the game saved it as
        unflag(d.UniqueId)
        return False
    kind = gun_type(weapon)
    built = build()
    part = built["parts"].get(kind) if built and kind else None
    if part is None:
        return False
    rebuild(weapon, ElementalPartDefinition=part, PrefixPartDefinition=_prefix(part, d))
    log(f"radiation: gave the element back to {d.BalanceDefinition.Name if d.BalanceDefinition else '?'}")
    return True


def restore_inventory(pawn: UObject) -> None:
    """Look at the held gun and the equipped guns every half second (only while some gun is remembered as radioactive). This is done from the
    game's own tick and only through the game's own weapon-slot calls: a hook on every gun build runs while a save loads, on the loader's
    threads, and crashed the game; and scanning the whole backpack (every item's class tested) is what the game hung on (2026-10-13), so a
    radioactive gun waiting in the backpack gets its element when it is equipped."""
    global _last_inventory
    if not _has_flags():
        return
    now = game_now()
    if now - _last_inventory < 0.5:
        return
    _last_inventory = now
    manager = pawn.InvManager
    if manager is None:
        return
    seen = set()
    for weapon in [pawn.Weapon, *(manager.GetWeaponInSlot(i) for i in range(1, 5))]:
        if weapon is None or weapon._get_address() in seen:
            continue
        seen.add(weapon._get_address())
        if weapon.Class.Name == "WillowWeapon":  # (a class-chain walk on a gun that is being destroyed can loop for ever)
            restore(weapon)
    if not _backpack["active"] and len(manager.Backpack) != _backpack["size"]:  # something came in (a bank withdrawal, a trade)
        _backpack.update(active=True, index=0, arrived=now - BACKPACK_WAIT)


_backpack = {"active": False, "index": 0, "size": -1, "arrived": 0.0}
BACKPACK_WAIT = 4.0  # s after a map arrives before the backpack is looked through (the save has finished loading by then)
BACKPACK_STEP = 6  # items looked at in one frame: a long backpack is gone through over a few seconds, never in one go


def restore_backpack(pawn: UObject) -> None:
    """The backpack's radioactive guns come back from the save without the element: give it back, a few items a frame, once after each map
    arrival (and again when the backpack's size changes). Only the class NAME of an item is tested (no class-chain walks), see restore_inventory."""
    if not _backpack["active"] or not _has_flags():
        return
    if game_now() - _backpack["arrived"] < BACKPACK_WAIT:
        return
    manager = pawn.InvManager
    if manager is None:
        return
    items = manager.Backpack
    count = len(items)
    for _ in range(BACKPACK_STEP):
        index = _backpack["index"]
        if index >= count:
            _backpack.update(active=False, size=count)
            try:
                prune_flags()
            except Exception:  # noqa: BLE001
                log("radiation flag pruning failed: " + traceback.format_exc()[-300:])
            return
        _backpack["index"] = index + 1
        item = items[index]
        if item is not None and item.Class.Name == "WillowWeapon":
            restore(item)


_last_inventory = 0.0


@hook("WillowGame.FrontendGFxMovie:Start", Type.PRE, hook_identifier="bl3w_radiation_menu")
def on_pause(*_: object) -> None:
    """The pause menu (or the main menu) opens: the slag effects are put back NOW, while the map and its objects are alive. The edits wait on the game
    clock, which stops while paused: a player who paused and quit to the menu had them put back after the map was gone (a crash, 2026-10-15).
    The same movie is the main menu too, and by then the map the edits were made in is gone: nothing is written then, they are let go."""
    try:
        pc = get_pc(possibly_loading=True)
        playing = pc is not None and pc.WorldInfo is not None and str(pc.WorldInfo.GetMapName(False)).lower() not in ("menumap", "loader", "")
    except Exception:  # noqa: BLE001 - when in doubt, write nothing
        playing = False
    if playing:
        radiation_fx.restore()
    else:
        radiation_fx.forget()


@hook("Engine.GameInfo:PreCommitMapChange", Type.PRE, hook_identifier="bl3w_radiation_leaving")
def on_leaving(*_: object) -> None:
    """A map is about to be left: nothing is written to the slag objects any more (writing to them here crashed the game, 2026-10-15); the edits are let go."""
    radiation_fx.forget()


@hook("WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie", Type.POST, hook_identifier="bl3w_radiation_arrived")
def on_map(*_: object) -> None:
    radiation_fx.forget()
    _backpack.update(active=True, index=0, size=-1, arrived=game_now())
    safe_build()
    if _built is not None:
        try:
            ensure_rows(_built)
        except Exception:  # noqa: BLE001
            log("radiation card rows failed: " + traceback.format_exc()[-400:])
        _apply_numbers(_built)  # the playthrough may have changed with the character


# ---- giving one --------------------------------------------------------------------------------------------------------------

_generator: dict = {}  # the one pool and mission the generator reuses (a 32-bit game runs out of memory if every gun leaves two objects behind)


def _generate(balance_path: str):
    """A weapon definition for a balance at the player's level from the game's own reward generator (no pickup is ever spawned)."""
    pc = get_pc()
    bal = unrealsdk.find_object("WeaponBalanceDefinition", balance_path)
    if not _generator:
        template = unrealsdk.find_object("ItemPoolDefinition", TEMPLATE_POOL)
        pool = _existing("ItemPoolDefinition", template.Outer, "RAD_Pool")
        if pool is None:
            pool = unrealsdk.construct_object("ItemPoolDefinition", template.Outer, "RAD_Pool", template_obj=template)
            pool.BalancedItems = [template.BalancedItems[0]]
        base = unrealsdk.find_object("MissionDefinition", TEMPLATE_MISSION)
        mission = _existing("MissionDefinition", base.Outer, "RAD_Mission")
        if mission is None:
            mission = unrealsdk.construct_object("MissionDefinition", base.Outer, "RAD_Mission", template_obj=base)
        _root(pool)
        _root(mission)
        mission.Reward.RewardItemPools = [pool]
        _generator.update(pool=pool, mission=mission)
    pool, mission = _generator["pool"], _generator["mission"]
    pool.BalancedItems[0].InvBalanceDefinition = bal
    pool.BalancedItems[0].Probability.BaseValueConstant = 1.0
    mission.GameStage = int(pc.Pawn.GetExpLevel())
    out = mission.GetItemRewardsForPlayer(pc, unrealsdk.make_struct("PendingMissionRewardData"))[1]
    for x in out.WeaponRewards:
        if x.BalanceDefinition is not None:
            return x
    raise RuntimeError("the reward generator returned no weapon")


def plain_balances(kind: str | None = None) -> list[str]:
    out = []
    for bal in unrealsdk.find_all("WeaponBalanceDefinition", exact=False):
        path = bal._path_name()
        if "Default__" not in path and re.match(PLAIN_BALANCE, path) and (kind is None or path.startswith(TYPES[kind] + ".")):
            out.append(path)
    return sorted(out)


def special_balances(which: str) -> list[str]:
    """The E-tech ("etech") or legendary ("legendary") weapon balances, without the left-out makers."""
    pattern = ETECH_BALANCE if which == "etech" else LEGENDARY_BALANCE
    return sorted(b._path_name() for b in unrealsdk.find_all("WeaponBalanceDefinition", exact=False)
                  if "Default__" not in b._path_name() and re.match(pattern, b._path_name()) and not any(f"_{m}_" in b._path_name() for m in LEFT_OUT)
                  and b.Name not in FIXED_LEGENDARIES)


def make_gun(kind: str | None = None, add_to_backpack: bool = True, balance: str | None = None) -> UObject:
    """A new radiation gun (a random plain balance of the type; any type when none is given; or the given balance: an E-tech or legendary one)."""
    pawn = get_pc().Pawn
    kinds = [kind] if kind else list(TYPES)
    if balance is None:
        balances = [b for b in plain_balances(random.choice(kinds)) if not any(f"_{m}_" in b for m in LEFT_OUT)] or [b for b in plain_balances() if not any(f"_{m}_" in b for m in LEFT_OUT)]
        balance = random.choice(balances)
    dd = make_radiation(_generate(balance), next(k for k, pkg in TYPES.items() if balance.startswith(pkg + ".")))
    if dd is None:
        raise RuntimeError("the radiation element could not be built")
    weapon = unrealsdk.find_class("WillowWeapon").ClassDefaultObject.CreateWeaponFromDef(dd, pawn, False)
    if not add_to_backpack:  # a gun made to be looked at and thrown away must not stay in the list of radioactive guns
        unflag(dd.UniqueId)
    elif pawn.InvManager.AddInventoryToBackpack(weapon) is False:
        unflag(dd.UniqueId)
        weapon.Destroy()
        raise RuntimeError("the backpack is full")
    log(f"radiation: made {balance.split('.')[-1]} ({'backpack' if add_to_backpack else 'not added'})")
    return weapon


@keybind("Spawn Radiation Gun", "F7", description="Test: put a new random radiation gun in your backpack.")
def spawn_one() -> None:
    try:
        weapon = make_gun()
        if weapon.GetAmmoCount() == 0:
            weapon.AddAmmo(60)
        show_hud_message("Radiation gun added to your backpack", str(weapon.GetHumanReadableName()), 4)
    except Exception as ex:  # noqa: BLE001
        log("radiation spawn failed: " + traceback.format_exc()[-400:])
        show_hud_message("Radiation", f"Could not spawn one: {ex}", 3)


def convert(weapon: UObject) -> str:
    """Make the held gun radioactive (a test and repair tool: it keeps its parts, gets the radiation element and is remembered as radioactive)."""
    d = weapon.DefinitionData
    if _left_out(d):
        return f"{d.ManufacturerDefinition.Name} guns are left out for now."
    kind = gun_type(weapon)
    built = build()
    part = built["parts"].get(kind) if built and kind else None
    if part is None:
        return "This kind of gun has no radiation element."
    flag(d.UniqueId)
    rebuild(weapon, ElementalPartDefinition=part, PrefixPartDefinition=_prefix(part, d))
    return f"{weapon.GetHumanReadableName()} is radioactive now."


@command("radiation", description="radiation give [type]: put a new radiation gun in the backpack (type: Pistol, SMG, AR, Shotgun, SR or RL); radiation apply: make the gun in your hands radioactive")
def command_radiation(args) -> None:
    try:
        if args.subcommand == "give":
            make_gun(args.type)
            print("Radiation gun added to your backpack.")
        elif args.subcommand == "apply":
            weapon = held_weapon()
            print(convert(weapon) if weapon is not None else "Hold a gun first.")
    except Exception:  # noqa: BLE001
        log("radiation give failed: " + traceback.format_exc()[-500:])
        print("Radiation: could not create the gun (see unrealsdk.log).")


command_radiation.add_argument("subcommand", choices=["give", "apply"], help="subcommand")
command_radiation.add_argument("type", nargs="?", choices=list(TYPES), help="gun type")

KEYBINDS = [spawn_one]
HOOKS = [on_hit, on_tick, on_roll, on_map, on_pause, on_leaving]
