"""Radiation grenades and shields: a grenade mod or shield with an element part can be radioactive, like the radiation guns.

Which ones is rolled once per item, from its unique number (core.remembered: saved with the mod's records, the way the anointments are), with the
chances below; only items that have an element part to turn into radiation's (a plain shield, a cryo item or a legendary's own parts never are).
A radioactive item is radiation for real (see "the real radiation parts" below): its element part is swapped for a copy with radiation's explosion,
damage type and effects, and its name and card say radiation.

- A radiation grenade: its explosion is radiation's (the yellow gas blast, radiation damage), and the grenade damage the enemies take in the next few
  seconds irradiates them (radiation.infect: they then give off their aura and blast like any irradiated enemy) and leaves a cloud.
- A radiation nova shield releases a radiation nova (gas blast) when it breaks; a spike shield's spikes are radiation's. Any radiation shield also
  irradiates the enemies around it when it breaks (a 6 m burst, with a cooldown).
Both need the player to hold a gun: radiation's damage over time takes its number from it.
"""

import copy
import re
import sys
import traceback

import unrealsdk
from mods_base import SliderOption, get_pc, hook
from ui_utils import show_hud_message
from unrealsdk.hooks import Block, Type, add_hook, prevent_hooking_direct_calls
from unrealsdk.unreal import UObject, WeakPointer

from . import radiation, radiation_fx
from .core import held_weapon, log, now as game_now, remembered, state

grenade_chance = SliderOption("Radiation Grenade Chance", 15, 0, 100, 5, True,
                              description="Chance that a grenade mod is radioactive (rolled once per grenade mod). Its explosions irradiate the enemies they hit and leave a radiation cloud. 0: none.")
shield_chance = SliderOption("Radiation Shield Chance", 15, 0, 100, 5, True,
                             description="Chance that a shield is radioactive (rolled once per shield). When it breaks it releases a radiation burst around you. 0: none.")
OPTIONS = [grenade_chance, shield_chance]

LEGENDARY_LEVELS = {5, 7, 8, 9, 10}  # item rarity levels of legendary and unique gear (the same set gear.py calls legendary; level 6 is the purple-class one)
WINDOW = 10.0  # seconds after a radiation grenade is thrown in which the player's grenade explosions are radiation's (a fuse and a MIRV's children)
MAX_BLASTS = 8  # explosions of one throw
SHIELD_RADIUS = 600.0  # units: the radiation burst of a broken shield (6 m)
SHIELD_COOLDOWN = 6.0  # seconds
GRENADE_MIN_RADIUS = 300.0  # units: a radiation explosion is at least this wide (some grenades' radius is tiny)

_armed_until = 0.0
_blasts = 0
_last_boom: tuple[float, float, float] | None = None
_last_boom_at = -1e9
BOOM_GAP = 0.8  # seconds: hits of one explosion arrive together; a later one this far apart is another explosion
_shield_ready = 0.0


def flagged(item: UObject | None, kind: str) -> bool:
    """Is this grenade mod / shield radioactive? (rolled the first time it is looked at, then remembered)"""
    if item is None:
        return False
    chance = (grenade_chance if kind == "grenade" else shield_chance).value

    def make(rng) -> list[str]:
        if element_source(item) is None:  # no element part to turn into radiation's (a plain shield, a cryo item, a legendary's own parts): never radioactive
            return []
        try:
            ret = item.StaticCalculateItemRarityLevel(item.DefinitionData)
            level = int(ret[0] if isinstance(ret, tuple) else ret)
        except Exception:  # noqa: BLE001
            level = 0
        if level in LEGENDARY_LEVELS or level >= 500:  # a legendary or unique shield or grenade (the Impaler, the Bee...) is its own item: never radioactive
            return []
        return ["radiation"] if rng.random() * 100.0 < chance else []

    return bool(remembered(f"rad{kind}", item, make)) and element_source(item) is not None  # (a flag from before the rule, on a cryo or plain item, counts for nothing)


# ------------------------------------------------------------------------------------------------ the real radiation parts
# A flagged item is made radiation for real: the part that gives it its element is swapped for a runtime copy whose behaviours are radiation's (the way a
# radiation gun's element part is). Grenade: the "Accessory_<element>" part (its explosion, its in-flight effect and damage type are instance data in
# its behaviour provider). Shield: the Nova / Spike accessory (its skill explodes, or spikes, with corrosive's damage type). The copy keeps the source
# part's own numbers (grades, status damage); only the element's behaviours are corrosive's, edited. Runtime parts can't be saved in an item (a save keeps
# parts by their place in BL2's own lists), so the item stays what it was in the save, flagged by its number, and is converted again whenever it is looked at.
GRENADE_PART = re.compile(r"^GD_GrenadeMods\.Accessory\.Accessory_(Corrosive|Incendiary|Shock|Slag)(_Grade\d)?$")
SHIELD_PART = re.compile(r"^GD_Shields\.Accessory\.Accessory(1_Spike|6_Nova)_(Corrosive|Explosive|Fire|Shock)$")
CORROSIVE_BEHAVIOURS = {  # kind -> the corrosive part whose behaviours are edited into radiation's
    "grenade": "GD_GrenadeMods.Accessory.Accessory_Corrosive",
    "spike": "GD_Shields.Accessory.Accessory1_Spike_Corrosive",
    "nova": "GD_Shields.Accessory.Accessory6_Nova_Corrosive",
}
GRENADE_STAND_IN = "GD_GrenadeMods.Accessory.Accessory_Corrosive_Grade3"
NOVA_EXPLOSION = "GD_Explosions.corrosive.Explosion_CorrosiveMaster_ShieldNova"
GRENADE_EXPLOSION = "GD_Explosions.corrosive.Explosion_CorrosiveMaster"
NOVA_SCALE = 0.5  # the nova is played on the player: a gas blast at the gun explosions' size fills the screen
PART_FIELD = "DeltaItemPartDefinition"
_made: dict[str, UObject] = {}


def _fx():
    return sys.modules.get(f"{__package__}.radfx")


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def element_source(item: UObject) -> tuple[str, UObject] | None:
    """(kind, part) when the item's element part can become radiation's: kind is "grenade", "spike" or "nova"."""
    from . import gear  # noqa: PLC0415

    kind = gear.item_kind(item)
    part = getattr(item.DefinitionData, PART_FIELD, None) if kind in ("grenade", "shield") else None
    if part is None and kind in ("grenade", "shield"):  # a save drops a runtime part: the item comes back without one, and its original is remembered
        saved = state.value.get(f"radsrc:{item.DefinitionData.UniqueId}")
        if not saved and state.value.get(f"rad{kind}:{item.DefinitionData.UniqueId}"):  # a radioactive item saved before its original part was remembered: a stand-in
            balance = item.DefinitionData.BalanceDefinition
            spike = balance is not None and "_Spike_" in balance.Name
            saved = [GRENADE_STAND_IN if kind == "grenade" else CORROSIVE_BEHAVIOURS["spike" if spike else "nova"]]
        part = _find("GrenadeModPartDefinition" if kind == "grenade" else "ShieldPartDefinition", saved[0]) if saved else None
    if part is None:
        return None
    path = part._path_name()
    if part.Name.endswith("_Radiation"):
        return ("grenade" if kind == "grenade" else "spike" if "Spike" in path else "nova"), part
    if kind == "grenade" and GRENADE_PART.match(path):
        return "grenade", part
    match = SHIELD_PART.match(path) if kind == "shield" else None
    return (("spike" if match.group(1) == "1_Spike" else "nova"), part) if match else None


def _own_provider(source: UObject, outer: UObject, name: str, edit) -> UObject:
    """A copy of a part's / skill's behaviour provider whose behaviours `edit(behaviour, provider)` may replace (it returns the replacement, or None)."""
    own = radiation._clone(source.BehaviorProviderDefinition, outer, name)
    for sequence in own.BehaviorSequences:
        for data in sequence.BehaviorData2:
            behaviour = data.Behavior
            replacement = edit(behaviour, own) if behaviour is not None else None
            if replacement is not None:
                data.Behavior = replacement
    return own


def _edit_grenade(behaviour: UObject, own: UObject) -> UObject | None:
    if behaviour.Class.Name != "Behavior_AddInstanceData":
        return None
    fx = _fx()
    copy_ = radiation._clone(behaviour, own, behaviour.Name + "_Radiation")
    for entry in copy_.InstanceData:
        if entry.Name == "DamageTypeOverride":
            entry.Object = radiation.build()["impact"]
        elif entry.Name == "ExplosionOverride":
            entry.Object = fx.radiation_explosion(GRENADE_EXPLOSION)
        elif entry.Name == "InFlightEffectsOverride" and fx.shot_system("Part_InFlight_Corrosive") is not None:
            entry.Object = fx.shot_system("Part_InFlight_Corrosive")
    return copy_


def _edit_skill(behaviour: UObject, own: UObject) -> UObject | None:
    fx, kind = _fx(), behaviour.Class.Name
    if kind == "Behavior_Explode":
        copy_ = radiation._clone(behaviour, own, behaviour.Name + "_Radiation")
        copy_.Definition = fx.radiation_explosion(NOVA_EXPLOSION, NOVA_SCALE, "Small")
    elif kind == "Behavior_CauseDamage":
        copy_ = radiation._clone(behaviour, own, behaviour.Name + "_Radiation")
        copy_.DamageTypeDefinition = radiation.build()["impact"]
    elif (kind == "Behavior_SpawnParticleSystem" and behaviour.ParticleEffect is not None and behaviour.ParticleEffect.Name.endswith("Spike_Corrosive")
          and fx.shot_system("Part_Shield_Spike_Corrosive") is not None):
        copy_ = radiation._clone(behaviour, own, behaviour.Name + "_Radiation")
        copy_.ParticleEffect = fx.shot_system("Part_Shield_Spike_Corrosive")
    elif kind == "Behavior_SpawnParticleSystem" and behaviour.ParticleEffect is not None and behaviour.ParticleEffect.Name == "Part_Shield_NovaCharge":
        copy_ = radiation._clone(behaviour, own, behaviour.Name + "_Radiation")  # the nova's own charge-up dome is a purple energy sphere: a small gas puff on the player instead
        copy_.ParticleEffect = _find("ParticleSystem", fx.SYSTEM.format("Small"))
    else:
        return None
    return copy_


def _radiation_skill(path: str) -> UObject | None:
    source = _find("SkillDefinition", path)
    if source is None:
        return None
    skill = radiation._clone(source, source.Outer, source.Name + "_Radiation")
    skill.BehaviorProviderDefinition = _own_provider(source, skill, "BehaviorProviderDefinition_0", _edit_skill)
    return skill


def _edit_shield_part(behaviour: UObject, own: UObject) -> UObject | None:
    kind = behaviour.Class.Name
    if kind not in ("Behavior_ActivateSkill", "Behavior_DeactivateSkill"):
        return None
    field = "SkillToActivate" if kind == "Behavior_ActivateSkill" else "SkillToDeactivate"
    skill = _radiation_skill(getattr(behaviour, field)._path_name())
    if skill is None:
        return None
    copy_ = radiation._clone(behaviour, own, behaviour.Name + "_Radiation")
    setattr(copy_, field, skill)
    return copy_


def radiation_part(kind: str, source: UObject) -> UObject | None:
    """The radiation copy of an element part (made once; None while the effects aren't ready)."""
    if source.Name.endswith("_Radiation"):
        return source
    path = source._path_name()
    if path in _made:
        return _made[path]
    fx = _fx()
    base = _find("GrenadeModPartDefinition" if kind == "grenade" else "ShieldPartDefinition", CORROSIVE_BEHAVIOURS[kind])
    needs = {"grenade": GRENADE_EXPLOSION, "nova": NOVA_EXPLOSION}.get(kind)  # (a spike shield has no explosion)
    if fx is None or base is None or radiation.build() is None or (needs is not None and fx.radiation_explosion(needs, NOVA_SCALE if kind == "nova" else None, "Small" if kind == "nova" else None) is None):
        return None
    clone = radiation._clone(source, source.Outer, source.Name + "_Radiation")
    clone.BehaviorProviderDefinition = _own_provider(base, clone, "BehaviorProviderDefinition_0", _edit_grenade if kind == "grenade" else _edit_shield_part)
    _made[path] = clone
    return clone


def convert(item: UObject) -> bool:
    """Make a flagged grenade mod or shield radiation for real (False: already is, not flagged, or not possible now)."""
    found = element_source(item)
    if found is None or found[1].Name.endswith("_Radiation"):
        return False
    kind, part = found
    if not flagged(item, "grenade" if kind == "grenade" else "shield"):
        return False
    new = radiation_part(kind, part)
    if new is None:
        return False
    remembered("radsrc", item, lambda rng: [part._path_name()])  # (what a save forgets)
    fresh = copy.copy(item.DefinitionData)
    setattr(fresh, PART_FIELD, new)
    item.DefinitionData = fresh
    log(f"radiation gear: {item.DefinitionData.BalanceDefinition.Name} #{item.DefinitionData.UniqueId} now has {new.Name}")
    _mark(item)
    return True


def _converted(slot: str, only: str | None = None) -> bool:
    """Is the equipped grenade mod / shield a real radiation one (of this kind: "nova", "spike")?"""
    from . import gear  # noqa: PLC0415

    item = gear._equipped(slot)
    found = element_source(item) if item is not None else None
    return found is not None and found[1].Name.endswith("_Radiation") and (only is None or found[0] == only)


_last_tick = 0.0


def tick(pawn: UObject) -> None:
    """Once a second: an equipped radiation grenade mod or shield that still has its original element part (it came out of the save, or was equipped as it was)
    is made radiation. A shield's skill was started by the part it had when it was equipped, so it is taken off and put back on around the swap."""
    global _last_tick
    t = game_now()
    if t - _last_tick < 1.0:
        return
    _last_tick = t
    from . import gear  # noqa: PLC0415

    for slot in ("grenade", "shield"):
        item = gear._equipped(slot)
        found = element_source(item) if item is not None else None
        if found is None or found[1].Name.endswith("_Radiation") or not flagged(item, slot) or radiation_part(found[0], found[1]) is None:
            continue
        manager = pawn.InvManager
        room = manager.GetEmptyBackpackSlots()
        if slot == "shield" and int(room[0] if isinstance(room, tuple) else room) < 1:  # it goes through the backpack: with no room it would be dropped
            continue
        if slot == "shield":
            manager.SendEquipLocThingToBackpack(item.GetEquipmentLocation())
        convert_quietly(item)
        if slot == "shield":
            manager.ServerReadyItemFromBackpack(item.DefinitionData, False)
        log(f"radiation gear: the equipped {slot} was made radiation")


def _mark(item: UObject) -> None:
    from . import elementicons  # noqa: PLC0415

    elementicons.mark_item(item)


def convert_quietly(item: UObject) -> None:
    try:
        if not convert(item) and is_radiation_gear(item):
            _mark(item)
    except Exception:  # noqa: BLE001
        log("radiation gear conversion failed: " + traceback.format_exc()[-300:])


def card_line(item: UObject) -> str | None:
    from . import gear  # noqa: PLC0415 - gear imports this module's callers

    kind = gear.item_kind(item)
    if kind not in ("grenade", "shield") or not flagged(item, kind):
        return None
    convert_quietly(item)
    return ("Element: Radiation. Its explosion irradiates and leaves a cloud" if kind == "grenade"
            else "Element: Radiation. When it breaks it releases a radiation burst")


def _burst(pc: UObject, centre: tuple[float, float, float], radius: float, weapon: UObject, infect: bool = True, look: bool = True, cloud: bool = True) -> int:
    """The radiation blast at `centre`: its look and sound, the enemies in `radius` irradiated, a cloud left. Returns how many were irradiated."""
    pawn = pc.Pawn
    args = (pawn, radius, 1.0, True, 0.0, unrealsdk.find_class("WillowDmgSource_StatusEffect"), radiation._vec(*centre), radiation._vec(0, 0, 1),
            True, False, radiation._vec(0, 0, 0), weapon, False, True, None, 0.0, 0.0)
    if look:  # (a real radiation grenade or nova explodes with the gas blast by itself now)
        radiation_fx.play(radiation.YELLOW, *args, sound_on=pawn)
    victims = radiation._near(centre, radiation._enemies(pc, set()), radius) if infect else []
    count = sum(1 for victim in victims if victim.IsAliveAndWell() and radiation.infect(pc, victim, weapon))
    ticks = [entry["tick"] for entry in radiation._hot.values() if entry["tick"] > 0]
    if cloud:  # (not on the player: the camera sits in the gas)
        radiation._leave_cloud({"loc": centre, "weapon": WeakPointer(weapon)}, max(ticks) * radiation.TICKS_PER_SECOND if ticks else 100.0)
    return count


def on_throw() -> None:
    """Called by gear.on_grenade, once per throw: a radiation grenade arms the next explosions."""
    global _armed_until, _blasts
    from . import gear  # noqa: PLC0415

    if flagged(gear._equipped("grenade"), "grenade"):
        _armed_until, _blasts = game_now() + WINDOW, 0
        log("radiation grenade thrown")
        show_hud_message("Radiation Grenade", "Its explosion will irradiate", 1.5)


@hook("WillowGame.WillowAIPawn:TakeDamage", Type.POST, hook_identifier="bl3w_radgear_hit")
def on_grenade_hit(obj: UObject, args, *_: object) -> None:
    """An enemy hurt by the player's grenade while a radiation grenade is armed: it is irradiated, and the first hit of each explosion plays the radiation
    blast there. (Hooking the explosion itself did not fire for real grenades: the game plays them from native code; their damage does come through here,
    which is how the grenade anointment sees it too.)"""
    global _blasts, _last_boom, _last_boom_at
    if game_now() > _armed_until:
        return
    try:
        pc = get_pc(possibly_loading=True)
        if (pc is None or pc.Pawn is None or args.InstigatedBy is None or args.InstigatedBy._get_address() != pc._get_address()
                or args.DamageType is None or args.DamageType.Name != "WillowDmgSource_Grenade" or not obj.IsAliveAndWell()):
            return
        weapon = held_weapon()
        if weapon is None:
            return
        radiation.infect(pc, obj, weapon)
        loc = obj.Location
        here = (loc.X, loc.Y, loc.Z)
        t = game_now()
        near = _last_boom is not None and sum((a - b) ** 2 for a, b in zip(here, _last_boom)) ** 0.5 < GRENADE_MIN_RADIUS * 1.5
        if (t - _last_boom_at > BOOM_GAP or not near) and _blasts < MAX_BLASTS:
            _blasts, _last_boom, _last_boom_at = _blasts + 1, here, t
            _burst(pc, here, GRENADE_MIN_RADIUS, weapon, infect=False, look=not _converted("grenade"))  # the blast's look (when the grenade has none of its own), its sound and the cloud; the grenade's own damage already reached the enemies
            log("radiation grenade exploded")
    except Exception:  # noqa: BLE001
        log("radiation grenade failed: " + traceback.format_exc()[-300:])


ELEMENT_WORDS = re.compile(r"\b(Acid|Corrosive|Shock|Fire|Incendiary|Slag|Explosive|Cryo|Ice|Frost|Asidy|Burnin|Lectrik|Sluj)\b", re.IGNORECASE)  # (the last three: Bandit grenades\' own spellings)


def radiation_name(item: UObject, name: str) -> str:
    """A radiation grenade mod's or shield's name with its element word turned into radiation's ("Resplendant Acid Nova Shield" -> "Resplendant Radiation
    Nova Shield"); a name with no element word in it gets "Radioactive" in front."""
    try:
        bandit = item.DefinitionData.ManufacturerDefinition.Name == "Bandit"
    except Exception:  # noqa: BLE001 - no item, no maker: the plain word
        bandit = False
    word = "Kemik" if bandit else "Radiation"  # (Bandits misspell everything)
    renamed, count = ELEMENT_WORDS.subn(word, name, count=1)
    return renamed if count else f"{'Kemik' if bandit else 'Radioactive'} {name}"


def _gear_name(obj: UObject, _args, _ret, func):
    """The name everywhere BL2 asks a grenade mod or shield for it (the card, the backpack list, the item's HUD)."""
    try:
        from . import gear  # noqa: PLC0415

        kind = gear.item_kind(obj)
        if kind not in ("grenade", "shield") or obj.DefinitionData.UniqueId == 0 or not flagged(obj, kind):
            return None
        convert_quietly(obj)
        with prevent_hooking_direct_calls():
            plain = str(getattr(obj, func.func.Name)())  # the same function (the long name or the short one), called for the plain name
        return Block, radiation_name(obj, plain)
    except Exception:  # noqa: BLE001
        return None


ELEMENT_TEXT = re.compile(r'<font color="#[0-9A-Fa-f]{6}">(Fire|Incendiary|Corrosive|Corrode|Acid|Shock|Electrocute|Ignite|Burn|Slag|Explosive)( Damage[^<]*)?</font>')


VERBS = {"Corrode", "Ignite", "Electrocute", "Burn", "Slag"}  # the card's verbs ("Chance to Corrode enemies."): "Irradiate" for radiation


def radiation_text(text: str) -> str:
    """The card's description lines of a radiation item: the element word (in its colour) becomes "Radiation", "Corrode Damage / sec." becomes "Radiation
    Damage / sec.", and the verb ("Chance to Corrode enemies.") becomes "Irradiate", in radiation's yellow."""
    def word(m):
        verb = m.group(2) is None and m.group(1) in VERBS
        return f'<font color="{radiation.TEXT_COLOUR}">{"Irradiate" if verb else "Radiation"}{m.group(2) or ""}</font>'

    return ELEMENT_TEXT.sub(word, text)


def is_radiation_gear(item: UObject) -> bool:
    from . import gear  # noqa: PLC0415

    kind = gear.item_kind(item)
    return kind in ("grenade", "shield") and item.DefinitionData.UniqueId != 0 and flagged(item, kind)


def install_name_hooks() -> None:
    """Hook the name functions of items (shortcut: they stay hooked while the mod is switched off in the menu, and only ever change a flagged item's name). They are added here, one by one, because a function this game doesn't have must not stop the mod."""
    for path in ("Engine.WillowInventory:GetHumanReadableName", "WillowGame.WillowItem:GetShortHumanReadableName"):
        try:
            add_hook(path, Type.PRE, f"bl3w_radgear_{path.rsplit(':', 1)[1]}", _gear_name)
        except Exception as ex:  # noqa: BLE001
            log(f"radiation gear names: no hook on {path}: {ex}")


def on_shield_break() -> None:
    """Called by gear.watch_shield when the shield reaches 0: a radiation shield releases its burst."""
    global _shield_ready
    from . import gear  # noqa: PLC0415

    try:
        if game_now() < _shield_ready or not flagged(gear._equipped("shield"), "shield"):
            return
        pc, weapon = get_pc(possibly_loading=True), held_weapon()
        if pc is None or pc.Pawn is None or weapon is None:
            return
        _shield_ready = game_now() + SHIELD_COOLDOWN
        loc = pc.Pawn.Location
        count = _burst(pc, (loc.X, loc.Y, loc.Z), SHIELD_RADIUS, weapon, look=not _converted("shield", "nova"), cloud=False)
        show_hud_message("Radiation Shield", f"Radiation burst: {count} irradiated", 2)
        log(f"radiation shield broke: {count} enemies irradiated")
    except Exception:  # noqa: BLE001
        log("radiation shield failed: " + traceback.format_exc()[-300:])


HOOKS = [on_grenade_hit]


install_name_hooks()
