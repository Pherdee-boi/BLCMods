"""HeartShatter: a legendary Vladof sniper rifle of the mod's own, found as a world drop in different forms.

A slow, hard-hitting normal (not burst) sniper built only from real BL2 parts, so a saved gun loads like any other. Locked on every
HeartShatter:
  barrel   Maliwan's Volcano barrel (SR_Barrel_Maliwan_Volcano): a slower, heavier barrel; its Rarity attribute is what makes the gun
           legendary (Lyudmila's, the first choice, was too fast),
  sight    the Dahl PISTOL sight (drawn on the sniper by sights.py, like every close-range sight),
  bipod    Vladof's BL3 bipod underbarrel (vladof.py rolls a Vladof gun's underbarrel at random; a HeartShatter always gets this one),
  feel     tune(): a long pause between shots, a small magazine and extra damage (sliders), held on the gun while it is in hand.
  element  always CRYO (the name is a pun: freeze enemies, then shatter them), through the Aurelia mod's cryo part (see Credits); without
           that mod the gun has no element.
Random on every new HeartShatter (Random Look option): body, grip, stock, accessory and skin; with Random Look off the look is the
classic icy-blue one and only the accessory rolls (Random Accessory option). Like a vanilla legendary (the Unkempt
Harold that sometimes drops as the "Double Penetrating Unkempt Harold"), its NAME takes a prefix from one of its parts: the accessory's
Vladof prefix (Britva, Zammechat, Razrez, Dobby, Bolshy, Skorry or Gromky: "Bolshy HeartShatter"), or, with no accessory, the cryo
element's ("Cold HeartShatter"). With Random Look off it is always the
classic icy-blue one.
The gun is recognised by the combination Volcano barrel + Dahl pistol sight, which BL2's own loot never makes (the real Volcano
carries Maliwan's scope), so no id has to be remembered. A share of new plain Vladof snipers (Drop Chance) roll as a HeartShatter,
like sights.py rerolls sniper sights; `heartshatter give` (console) puts one in the backpack. It is legendary, so with the
Anointments option on it carries a BL3 anointment like any legendary of this mod.

Legendary effect, the TRIPLE RICOCHET: a critical hit ricochets to the nearest other enemy within reach (20 m), and from there to the
next nearest one, so three enemies that stand close together are all hit by one shot. Each ricochet is a STICKY BOMB, like a Torgue
sticky: it sticks to the enemy and, about a second later, goes off as a cryo explosion: the game's own explosion effect and sound
(the Aurelia mod's ice explosion, see Credits; the Torgue explosion without that mod), the bomb's damage (a share of the crit,
75%) on every enemy in the blast, and the cryo status (Aurelia's `Status_Ice`) applied to each of them: the ricochets freeze, and
whatever Aurelia's shatter rules do to frozen enemies follows. It is the one mod gun that has all of it: Dahl sight, Vladof bipod,
Jakobs ricochet, Torgue stickies, cryo. The card says nothing of this: players find it out.
Why not let the explosion do the damage: `ExplosionDefinition.PlayExplosion` called from script plays the effect and sound but
dealt no damage in any variant tried (enemy 40 m away, seven argument variants), so the damage and the status are applied by hand.

Credits: the cryo element is the work of Grim Reader and Claude, from the Aurelia mod for Borderlands 2 (its cryo weapon parts,
`GD_AureliaCryo.SR_Elemental_Cryo`, and its ice explosion `gd_co_effects.Shared.Explosions.Explosion_IceMaster`, built when that mod starts; its freezing and shattering rules and the shot effects come with it
and with the cryo shot FX mod). This file only points at the part by name, copies none of Aurelia's code, and works without it.
Used with Grim Reader's permission, on the condition that they are credited.
"""

import copy
import random
import traceback

import unrealsdk
from mods_base import BoolOption, SliderOption, command, get_pc, hook, keybind
from unrealsdk.hooks import Block, Type, prevent_hooking_direct_calls
from ui_utils import show_hud_message
from unrealsdk.unreal import UObject, WeakPointer

from .core import held_weapon, log, now as game_now, pawns, rebuild, state
from .jakobs import GUN_DAMAGE, _dist, _show

NAME = "HeartShatter"
BALANCE = "GD_Weap_SniperRifles.A_Weapons.Sniper_Vladof_4_VeryRare"  # a plain Vladof sniper balance; every part is overridden below
# DefinitionData field -> full part path (the DLC packages repeat several of these names, so short names are ambiguous)
LOCKED = {
    "BarrelPartDefinition": "GD_Weap_SniperRifles.Barrel.SR_Barrel_Maliwan_Volcano",
    "SightPartDefinition": "GD_Weap_Pistol.Sight.Pistol_Sight_Dahl",
}
CLASSIC = {  # the first HeartShatter's look: used when Random Look is off
    "BodyPartDefinition": "GD_Weap_SniperRifles.Body.SR_Body_Vladof_4",
    "GripPartDefinition": "GD_Weap_SniperRifles.Grip.SR_Grip_Vladof",
    "StockPartDefinition": "GD_Weap_SniperRifles.Stock.SR_Stock_Vladof",
    "Accessory1PartDefinition": "GD_Weap_SniperRifles.Accessory.Sniper_Accessory_Foregrip_Stability",
    "MaterialPartDefinition": "GD_Weap_SniperRifles.ManufacturerMaterials.Material_Hyperion_3_Morningstar",  # icy white-blue
}
MAKERS = ("Dahl", "Hyperion", "Jakobs", "Maliwan", "Vladof")
ACCESSORIES = ("Bayonet1", "Bipod1_Accuracy", "Bipod2_Critical", "Foregrip_Stability", "Mount1_Mag", "Mount2_FireRate", "Mount3_Damage")
SKINS = (
    "Dahl_1", "Dahl_2", "Dahl_3", "Dahl_3_Sloth", "Dahl_4", "Dahl_5_Legendary", "Hyperion_1", "Hyperion_2", "Hyperion_3",
    "Hyperion_3_FremingtonsEdge", "Hyperion_3_Longbow", "Hyperion_3_Morningstar", "Hyperion_4", "Hyperion_5_Legendary",
    "Jakobs_1", "Jakobs_2", "Jakobs_3", "Jakobs_3_Buffalo", "Jakobs_3_Trespasser", "Jakobs_4", "Jakobs_5_Legendary",
    "Maliwan_1", "Maliwan_2", "Maliwan_3", "Maliwan_3_ChereAmie", "Maliwan_4", "Maliwan_5_Legendary",
    "Vladof_1", "Vladof_2", "Vladof_3", "Vladof_4", "Vladof_5_Legendary",
)
NO_ACCESSORY = "GD_Weap_SniperRifles.Accessory.Sniperl_Accessory_None"  # (sic: BL2's own spelling)
POOLS = {  # what a random HeartShatter picks from: real BL2 sniper parts only; a part whose package is not loaded is skipped
    "BodyPartDefinition": [f"GD_Weap_SniperRifles.Body.SR_Body_{m}_{t}" for m in MAKERS for t in (3, 4)],  # rare and very rare bodies
    "GripPartDefinition": [f"GD_Weap_SniperRifles.Grip.SR_Grip_{m}" for m in MAKERS],
    "StockPartDefinition": [f"GD_Weap_SniperRifles.Stock.SR_Stock_{m}" for m in MAKERS],
    "Accessory1PartDefinition": [f"GD_Weap_SniperRifles.Accessory.Sniper_Accessory_{a}" for a in ACCESSORIES] + [NO_ACCESSORY],
    "MaterialPartDefinition": [f"GD_Weap_SniperRifles.ManufacturerMaterials.Material_{m}" for m in SKINS],
}
NO_ELEMENT = "GD_Weap_SniperRifles.elemental.SR_Elemental_None"  # only when the Aurelia mod (cryo) is not installed
VLADOF_SNIPERS = "GD_Weap_SniperRifles.A_Weapons.Sniper_Vladof"  # the balances a HeartShatter is made from (and a world roll turns into one)
FINGERPRINT = tuple(LOCKED)  # the look is not part of it: any body, skin, accessory
CRYO_PART = "GD_AureliaCryo.SR_Elemental_Cryo"  # the Aurelia mod's cryo sniper part (Grim Reader); exists only while that mod is installed
TEMPLATE_POOL = "GD_Itempools.WeaponPools.Pool_Weapons_SMG_04_Rare"  # same pickup-free generator trick as the Adventure Guide
TEMPLATE_MISSION = "GD_Episode05.M_Ep5_ThePhoenix"

drop_chance = SliderOption(
    "HeartShatter Drop Chance",
    1,
    0,
    25,
    1,
    True,
    description="Percent of new plain Vladof sniper rifles (world drops, chests, vendors) that roll as a HeartShatter. 0 turns the drop off.",
)
shot_delay = SliderOption(
    "HeartShatter Time Between Shots",
    6,
    1,
    30,
    1,
    True,
    description="Seconds between shots in tenths (6 = 0.6 s): the HeartShatter is a slow, hard-hitting sniper.",
)
magazine = SliderOption("HeartShatter Magazine", 8, 1, 30, 1, True, description="Rounds in the HeartShatter's magazine.")
damage_pct = SliderOption("HeartShatter Damage", 300, 100, 1000, 25, True, description="Bullet damage as a percent of what the gun's parts give it.")
reload_pct = SliderOption("HeartShatter Reload Time", 100, 25, 200, 5, True, description="Reload time as a percent of the barrel's own reload (lower is faster).")
random_look = BoolOption(
    "HeartShatter Random Look",
    True,
    description="New HeartShatters get a random body, grip, stock, accessory and skin (the barrel, Dahl sight, bipod and cryo stay). Off: always the classic icy-blue look.",
)
random_accessory = BoolOption(
    "HeartShatter Random Accessory",
    True,
    description="With Random Look off: keep the classic look but roll the accessory (bayonet, bipod, mount, foregrip or none). The accessory decides the name prefix: Britva, Zammechat, Razrez, Dobby, Bolshy, Skorry, Gromky or Cold.",
)
ricochet_pct = SliderOption(
    "HeartShatter Ricochet Damage",
    75,
    10,
    150,
    5,
    True,
    description="Percent of a critical hit's damage dealt by each of the HeartShatter's two ricochets.",
)
ricochet_range = SliderOption(
    "HeartShatter Ricochet Range",
    20,
    5,
    60,
    1,
    True,
    description="How far, in metres, a ricochet can jump to the next enemy.",
)
sticky_fuse = SliderOption(
    "HeartShatter Sticky Fuse",
    10,
    3,
    30,
    1,
    True,
    description="Seconds, in tenths, between a ricochet sticking to an enemy and its cryo explosion (10 = 1 s).",
)
blast_radius = SliderOption(
    "HeartShatter Blast Radius",
    4,
    2,
    8,
    1,
    True,
    description="Radius of a ricochet's cryo explosion in metres (smaller when you stand close, so it never hurts you).",
)
OPTIONS = [drop_chance, random_look, random_accessory, shot_delay, magazine, damage_pct, reload_pct, ricochet_pct, ricochet_range, sticky_fuse, blast_radius]
RICOCHETS = 2  # the enemy that was hit + 2 more = triple
ICE_EXPLOSION = "gd_co_effects.Shared.Explosions.Explosion_IceMaster"  # the Aurelia mod's cryo explosion (Grim Reader): cryo damage type
TORGUE_ROCKET = "GD_Weap_AssaultRifle.Projectiles.Projectile_Rocket_Torgue"  # its DefaultExplosionDefinition is the Torgue boom
CRYO_STATUS = "GD_Ice.StatusEffect.Status_Ice"  # the Aurelia mod's cryo status effect (what its cryo damage type applies)
RED_TEXT = "Love at first sight. Heartbreak at third."  # the legendary's red flavour text on the item card (the user's pick)
RED = "#dc4646"  # the red BL2's own item cards use for a legendary's flavour text


def fun_stats_text(text: str) -> str:
    """The card's text block for a HeartShatter: the Volcano barrel's own red line ("Pele humbly requests a sacrifice...") is dropped, the
    HeartShatter's red text leads, and the other lines (such as the cryo element's "Freezes enemies solid.") stay."""
    kept = [line for line in (text or "").split("\n") if RED not in line and line.strip()]
    return "\n".join([f"\u2022 <font color='{RED}'>{RED_TEXT}</font>", *kept]) + "\n"

_counter = 0


def _part(path: str) -> UObject:
    return unrealsdk.find_object("WeaponPartDefinition", path)


def _available(paths: list[str]) -> list[UObject]:
    out = []
    for path in paths:
        try:
            out.append(_part(path))
        except ValueError:  # a part of a package that is not loaded: skip it
            pass
    return out


def _parts_for_new_gun() -> dict[str, UObject]:
    """The parts of a new HeartShatter: the locked ones, cryo as its element (Aurelia's cryo sniper part; none without that mod), then
    a random look (or the classic one) for the rest."""
    parts = {field: _part(path) for field, path in LOCKED.items()}
    try:
        element = _part(CRYO_PART)
    except ValueError:  # Aurelia is not installed (or not enabled yet): no element
        element = _part(NO_ELEMENT)
    if random_look.value:
        for field, paths in POOLS.items():
            parts[field] = random.choice(_available(paths))
    else:
        for field, path in CLASSIC.items():
            parts[field] = _part(path)
        if random_accessory.value:  # the classic look, but the accessory (and so the name prefix) rolls
            parts["Accessory1PartDefinition"] = random.choice(_available(POOLS["Accessory1PartDefinition"]))
    parts["ElementalPartDefinition"] = element
    prefix = name_prefix(parts.get("Accessory1PartDefinition"), element)
    parts["PrefixPartDefinition"] = prefix  # None: no prefix
    barrel_titles = [t for t in parts["BarrelPartDefinition"].TitleList if t is not None]
    if barrel_titles:
        parts["TitlePartDefinition"] = barrel_titles[0]
    return parts


def _vladof_prefix(part: UObject | None) -> UObject | None:
    """The Vladof name prefix a part carries (each part lists one prefix per manufacturer, as BL2's own naming does)."""
    if part is None:
        return None
    for prefix in part.PrefixList:
        if prefix is not None and "Vladof" in prefix._path_name():
            return prefix
    return None


def name_prefix(accessory: UObject | None, element: UObject | None) -> UObject | None:
    """The accessory's prefix wins; a gun without an accessory takes its element's (what BL2's own name selection does)."""
    return _vladof_prefix(accessory) or _vladof_prefix(element)


def display_name(weapon: UObject) -> str:
    """'Bolshy HeartShatter': the part-driven prefix (vanilla legendaries such as the Unkempt Harold also drop with one) + the name."""
    d = weapon.DefinitionData
    prefix = name_prefix(d.Accessory1PartDefinition, d.ElementalPartDefinition)
    return f"{prefix.PartName} {NAME}" if prefix is not None else NAME


def has_fingerprint(weapon: UObject) -> bool:
    """True for a HeartShatter: the barrel check comes first because this runs for every weapon name BL2 asks for."""
    return fingerprint_data(weapon.DefinitionData)


def fingerprint_data(d) -> bool:
    """The same test on a weapon definition (what the game's save check is asked about)."""
    for field in FINGERPRINT:
        part = getattr(d, field)
        if part is None or part._path_name() != LOCKED[field]:
            return False
    # The Volcano barrel and the Dahl pistol sight alone are not enough: BL2's own Volcano (a Maliwan legendary that burns) can roll that sight
    # now that snipers get the close-range sights, and was taken for a HeartShatter (2026-10-13). A HeartShatter is a Vladof sniper.
    maker, bal = d.ManufacturerDefinition, d.BalanceDefinition
    return maker is not None and maker.Name == "Vladof" and bal is not None and bal._path_name().startswith(VLADOF_SNIPERS)


# ---- persistence ---------------------------------------------------------------------------------------------------------------
# A saved gun keeps only the parts its own part lists can name: a Maliwan barrel on a Vladof sniper, a pistol sight and Aurelia's cryo part are
# not in them, so a HeartShatter came back from a save as an ordinary Vladof sniper (2026-10-15: none of 54 guns in the save was one). So every
# HeartShatter's parts are remembered under the gun's unique id (core.state, "heartshatter:<id>", one "field|class|path" entry per part) and
# given back to a gun that is found without them, the way radiation.py gives its element back.

LOOK_FIELDS = tuple(LOCKED) + ("BodyPartDefinition", "GripPartDefinition", "StockPartDefinition", "Accessory1PartDefinition", "MaterialPartDefinition",
                               "ElementalPartDefinition", "PrefixPartDefinition", "TitlePartDefinition")
_any: bool | None = None  # whether any gun is remembered (looking through every saved key each frame would cost too much)
_given_up: set[int] = set()  # unique ids whose parts could not be found: not tried again until the game is restarted


def _key(unique_id: int) -> str:
    return f"heartshatter:{unique_id}"


def flagged(unique_id: int) -> bool:
    return _key(unique_id) in state.value


def _has_flags() -> bool:
    global _any
    if _any is None:
        _any = any(k.startswith("heartshatter:") for k in state.value)
    return _any


def _entry(prefix: str, field: str, part: UObject | None) -> str:
    return f"{prefix}{field}|{part.Class.Name}|{part._path_name()}" if part is not None else f"{prefix}{field}||"


def remember(definition_data, base=None) -> None:
    """Store the parts of a HeartShatter under its unique id: its look, and `base`, the valid parts of the gun it was made from (kept from an
    earlier call when none is given). A save keeps a gun only when the game accepts all its parts, and it silently drops one with a Maliwan
    barrel, a pistol sight, another maker's body or a skin of the wrong maker (2026-10-15), so the saved gun is made of the base parts."""
    global _any
    key = _key(definition_data.UniqueId)
    entries = [_entry("", field, getattr(definition_data, field, None)) for field in LOOK_FIELDS]
    if base is not None:
        entries += [_entry("base:", field, getattr(base, field, None)) for field in LOOK_FIELDS]
    else:
        entries += [e for e in state.value.get(key, []) if e.startswith("base:")]
    state.value = {**state.value, key: entries}
    state.save()
    _any = True
    _given_up.discard(definition_data.UniqueId)


def forget(unique_id: int) -> None:
    if flagged(unique_id):
        state.value = {k: v for k, v in state.value.items() if k != _key(unique_id)}
        state.save()


def recall(unique_id: int, base: bool = False) -> dict[str, UObject | None] | None:
    """The remembered parts of a gun (its look, or with base=True the valid parts it was made from), found again by path; None when a part
    that makes the gun a HeartShatter is not there (or, for the base, there is no base)."""
    out: dict[str, UObject | None] = {}
    wanted = "base:" if base else ""
    for entry in state.value.get(_key(unique_id), []):
        if base != entry.startswith("base:"):
            continue
        field, cls, path = (entry[len(wanted):].split("|", 2) + ["", ""])[:3]
        if not cls:
            out[field] = None
            continue
        try:
            out[field] = unrealsdk.find_object(cls, path)
        except ValueError:
            if field in LOCKED and not base:
                return None
            # (any other part that is not there - Aurelia's cryo without Aurelia - is left as the game loaded it)
    return out or None


def restore(weapon: UObject) -> bool:
    """Give a HeartShatter that came out of a save without its parts its parts back."""
    d = weapon.DefinitionData
    if d is None or d.UniqueId in _given_up or not flagged(d.UniqueId) or has_fingerprint(weapon):
        return False
    parts = recall(d.UniqueId)
    if parts is None:
        _given_up.add(d.UniqueId)
        log("heartshatter: a remembered gun's parts are not in this game: left as it is")
        return False
    rebuild(weapon, **parts)
    log(f"heartshatter: gave the parts back to a saved HeartShatter ({describe(weapon)})")
    return True


_backpack = {"active": False, "index": 0, "size": -1, "arrived": 0.0}
BACKPACK_WAIT = 4.0  # s after a map arrives before the backpack is looked through (the save has finished loading by then)
BACKPACK_STEP = 6  # items looked at in one frame
_last_inventory = 0.0


def restore_inventory(pawn: UObject) -> None:
    """The held and equipped guns, every half second (only while some gun is remembered). Only through the game's own weapon-slot calls and
    never the whole backpack at once: see radiation.restore_inventory (a hook on every gun build crashed the game, a backpack scan hung it)."""
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
        if weapon.Class.Name == "WillowWeapon":
            restore(weapon)
    if not _backpack["active"] and len(manager.Backpack) != _backpack["size"]:  # something came in (a bank withdrawal, a trade)
        _backpack.update(active=True, index=0, arrived=now - BACKPACK_WAIT)


def restore_backpack(pawn: UObject) -> None:
    """The backpack's remembered guns, a few items a frame, once after each map arrival (and again when the backpack's size changes)."""
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
            return
        _backpack["index"] = index + 1
        item = items[index]
        if item is not None and item.Class.Name == "WillowWeapon":
            restore(item)


@hook("WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie", Type.POST, hook_identifier="bl3w_heartshatter_arrived")
def on_arrival(*_: object) -> None:
    _backpack.update(active=True, index=0, size=-1, arrived=game_now())
    _given_up.clear()


# ---- saving: the valid form ---------------------------------------------------------------------------------------------------
_swapped: list[tuple[WeakPointer, object]] = []  # (gun, its real definition) while a save is being written


def _guns(pawn: UObject) -> list[UObject]:
    manager = pawn.InvManager
    guns = []
    if manager is None:
        return guns
    for weapon in [pawn.Weapon, *(manager.GetWeaponInSlot(i) for i in range(1, 5)), *list(manager.Backpack)]:
        if weapon is not None and weapon.Class.Name == "WillowWeapon" and all(weapon._get_address() != g._get_address() for g in guns):
            guns.append(weapon)
    return guns


def sanitize() -> int:
    """Just before the game writes a save: every HeartShatter in the inventory is given its base parts for the moment (the saved gun is then an
    ordinary valid Vladof sniper with the same unique id; `restore` rebuilds the HeartShatter when it is found after the load)."""
    if _swapped or not _has_flags() or not SWAP_ON_SAVE:
        return 0
    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if pawn is None:
        return 0
    for weapon in _guns(pawn):
        d = weapon.DefinitionData
        if d is None or not flagged(d.UniqueId) or not has_fingerprint(weapon):
            continue
        base = recall(d.UniqueId, base=True)
        if not base:
            log("heartshatter: a gun with no remembered base parts cannot be made valid: it will not survive the save")
            continue
        plain = copy.copy(d)
        for field, part in base.items():
            setattr(plain, field, part)
        weapon.DefinitionData = plain
        _swapped.append((WeakPointer(weapon), d))
    return len(_swapped)


def unsanitize() -> None:
    """The save is written: the guns are what they were."""
    for ref, real in _swapped:
        weapon = ref()
        if weapon is not None:
            weapon.DefinitionData = real
    _swapped.clear()


@hook("WillowGame.WillowPlayerController:GeneratePlayerSaveGame", Type.PRE, hook_identifier="bl3w_hs_save_pre")
def on_save_start(*_: object) -> None:
    try:
        n = sanitize()
        if n:
            log(f"heartshatter: {n} HeartShatter(s) saved in their valid form")
    except Exception:  # noqa: BLE001
        log("heartshatter sanitize failed: " + traceback.format_exc()[-300:])


@hook("WillowGame.WillowPlayerController:GeneratePlayerSaveGame", Type.POST, hook_identifier="bl3w_hs_save_post")
def on_save_end(*_: object) -> None:
    try:
        unsanitize()
    except Exception:  # noqa: BLE001
        log("heartshatter unsanitize failed: " + traceback.format_exc()[-300:])


# ---- the game's check of a saved gun ----------------------------------------------------------------------------------------------
# When a save (or the bank/stash) is read the game asks WillowPlayerController.ValidateWeaponDefinition about each gun: every part must be in the list
# its balance rolls that part from, or the gun is thrown away - that is what made a HeartShatter vanish from saves. The Aurelia mod answers the same
# check for its cryo guns with a hook; this does it for ours. It only ever says yes, and only where the game's own answer would be no. SWAP_ON_SAVE
# (the swap in `sanitize`) stays on: it keeps the saved gun an ordinary valid gun, so a save made with the mod loads without it.
SWAP_ON_SAVE = True
CHECK_TAKERS = ("Sanity Saver",)  # a mod that reads saved guns in the game's place (it blocks the check and rebuilds every gun itself: a yes of ours would duplicate them)
_answered = 0


def check_taken_over() -> bool:
    try:
        from mods_base import get_ordered_mod_list  # noqa: PLC0415

        return any(mod.is_enabled and mod.name in CHECK_TAKERS for mod in get_ordered_mod_list())
    except Exception:  # noqa: BLE001 - a mod list that cannot be read is no reason to lose the answer
        return False


@hook("WillowGame.WillowPlayerController:ValidateWeaponDefinition", hook_identifier="bl3w_hs_validate")
def validate(obj: UObject, args, _ret: object, _func: object):
    global _answered
    try:
        d = args.DefinitionData
        if d is None or not fingerprint_data(d) or check_taken_over():
            return None
        with prevent_hooking_direct_calls():
            if obj.ValidateWeaponDefinition(copy.copy(d)):
                return None  # the game says yes itself
        _answered += 1
        if _answered <= 5:
            log(f"heartshatter: the save check answered yes for a gun it would have thrown away (#{int(d.UniqueId)})")
        return Block, True
    except Exception:  # noqa: BLE001
        log("heartshatter validate failed: " + traceback.format_exc()[-300:])
        return None


# ---- world drops -------------------------------------------------------------------------------------------------------------


def roll_into_heartshatter(weapon: UObject, chance: float | None = None) -> bool:
    """A brand-new plain Vladof sniper becomes a HeartShatter with the Drop Chance. Called right after the game rolls a new gun's
    parts (ChooseRandomParts runs only for new guns, never for a saved gun loading)."""
    pct = drop_chance.value if chance is None else chance
    if pct <= 0:
        return False
    d = weapon.DefinitionData
    wtype, maker, bal = d.WeaponTypeDefinition, d.ManufacturerDefinition, d.BalanceDefinition
    if wtype is None or maker is None or bal is None or maker.Name != "Vladof":
        return False
    path = bal._path_name()
    if not path.startswith("GD_Weap_SniperRifles.A_Weapons.") or "Alien" in path:
        return False  # only the plain Vladof sniper balances, never a legendary, unique or e-tech one
    if random.random() * 100 >= pct:
        return False
    fresh = copy.copy(d)
    for field, part in _parts_for_new_gun().items():
        setattr(fresh, field, part)
    weapon.DefinitionData = fresh
    remember(fresh, base=d)
    log(f"heartshatter: a new Vladof sniper rolled as a HeartShatter ({bal.Name})")
    return True


@hook("WillowGame.WillowWeapon:ChooseRandomParts", Type.POST, hook_identifier="bl3w_heartshatter_roll")
def on_roll(obj: UObject, *_: object) -> None:
    try:
        roll_into_heartshatter(obj)
    except Exception:  # noqa: BLE001
        log("heartshatter roll failed: " + traceback.format_exc()[-300:])


# ---- the gun's name ----------------------------------------------------------------------------------------------------------


@hook("WillowGame.WillowWeapon:GetHumanReadableName", hook_identifier="bl3w_heartshatter_name")
def on_name(obj: UObject, *_: object):
    """The name everywhere BL2 asks the weapon for it (its own parts would call it a Vladof something)."""
    try:
        if has_fingerprint(obj):
            return Block, display_name(obj)
    except Exception:  # noqa: BLE001
        pass
    return None


@hook("WillowGame.WillowWeapon:GetShortHumanReadableName", hook_identifier="bl3w_heartshatter_short_name")
def on_short_name(obj: UObject, *_: object):
    try:
        if has_fingerprint(obj):
            return Block, display_name(obj)
    except Exception:  # noqa: BLE001
        pass
    return None


# ---- the legendary effect: triple ricochet ---------------------------------------------------------------------------------------


def chain_targets(player_pawn: UObject, first: UObject, hops: int, reach: float) -> list[tuple[UObject, UObject]]:
    """[(from, to), ...]: from the enemy that was hit, the nearest other living enemy within reach, then the nearest one to that,
    never the same enemy twice. All of them are chosen before any damage is dealt (a kill can change the pawn list)."""
    skip = {first._get_address(), player_pawn._get_address()}
    out, here = [], first
    for _ in range(hops):
        best, best_d = None, reach
        for pawn in pawns(player_pawn):
            if pawn._get_address() in skip or not pawn.IsAliveAndWell() or not player_pawn.IsEnemy(pawn):
                continue
            d = _dist(pawn, here)
            if d < best_d:
                best, best_d = pawn, d
        if best is None:
            break
        out.append((here, best))
        skip.add(best._get_address())
        here = best
    return out


_pending: list[dict] = []  # sticky bombs waiting for their fuse: {"due", "target", "at", "damage", "weapon"}
_logged_bomb = 0
STICKY_LIFETIME = 6.0  # s: a bomb that was not set off by then is dropped (a map change, a long pause)


def explosion_definition() -> UObject | None:
    """Aurelia's cryo explosion when that mod is installed, else the plain Torgue rocket's."""
    try:
        return unrealsdk.find_object("ExplosionDefinition", ICE_EXPLOSION)
    except ValueError:
        pass
    try:
        return unrealsdk.find_object("ProjectileDefinition", TORGUE_ROCKET).DefaultExplosionDefinition
    except ValueError:
        return None


def _vec(x: float, y: float, z: float):
    return unrealsdk.make_struct("Vector", X=x, Y=y, Z=z)


def _cryo_status() -> UObject | None:
    try:
        return unrealsdk.find_object("StatusEffectDefinition", CRYO_STATUS)
    except ValueError:
        return None


def _enemies_in_blast(player_pawn: UObject, x: float, y: float, z: float, radius: float) -> list[UObject]:
    """Living enemies whose position is within the blast (all chosen before anything is hurt: a kill can change the pawn list)."""
    out = []
    for pawn in pawns(player_pawn):
        if pawn._get_address() == player_pawn._get_address() or not pawn.IsAliveAndWell() or not player_pawn.IsEnemy(pawn):
            continue
        loc = pawn.Location
        if ((loc.X - x) ** 2 + (loc.Y - y) ** 2 + (loc.Z - z) ** 2) ** 0.5 <= radius:
            out.append(pawn)
    return out


def _detonate(entry: dict, pc: UObject) -> None:
    """Set a sticky bomb off where the enemy is now (or was, if it died): the game's explosion effect and sound, then the bomb's damage
    and the cryo status on every enemy in the blast. The blast is as big as the radius allows without reaching the player."""
    pawn = pc.Pawn
    if pawn is None:
        return
    target = entry["target"]()
    x, y, z = entry["at"]
    if target is not None:
        loc = target.Location
        x, y, z = loc.X, loc.Y, loc.Z + 40
    radius = blast_radius.value * 100.0
    mine = pawn.Location
    radius = min(radius, max(0.0, ((mine.X - x) ** 2 + (mine.Y - y) ** 2 + (mine.Z - z) ** 2) ** 0.5 - 80))  # never reach the player
    weapon = entry["weapon"]() or pawn.Weapon or pawn
    source = unrealsdk.find_class("WillowDmgSource_Rocket")
    boom = explosion_definition()
    if boom is not None and radius >= 120:
        try:  # the effect and the sound; PlayExplosion deals no damage from a script, so it is asked for 1
            boom.PlayExplosion(
                pawn, radius, 1.0, True, 0.0, source, _vec(x, y, z), _vec(0, 0, 1),
                True, False, _vec(0, 0, 0), weapon, False, True, None, 0.0, 0.0,
            )
        except Exception:  # noqa: BLE001
            log("heartshatter explosion effect failed: " + traceback.format_exc()[-300:])
    victims = _enemies_in_blast(pawn, x, y, z, max(radius, 150.0))
    if target is not None and target.IsAliveAndWell() and all(v._get_address() != target._get_address() for v in victims):
        victims.append(target)  # the enemy the bomb is stuck to is always hit, even when the player is too close for a blast
    status = _cryo_status()
    for victim in victims:
        with prevent_hooking_direct_calls():  # the bomb's own damage must not start another ricochet
            victim.TakeDamage(entry["damage"], pc, victim.Location, _vec(0, 0, 0), source)
        if status is not None and victim.IsAliveAndWell():
            try:
                victim.GetStatusEffectsComponent().ApplyStatusEffect(pc, source, status, weapon, None, False)
            except Exception:  # noqa: BLE001
                log("heartshatter cryo status failed: " + traceback.format_exc()[-300:])
    global _logged_bomb
    if _logged_bomb < 6:
        _logged_bomb += 1
        log(f"heartshatter: a cryo bomb hit {len(victims)} enemies for {entry['damage']:.0f} each (blast {radius / 100:.1f} m)")


def detonate_due() -> None:
    if not _pending:
        return
    pc = get_pc(possibly_loading=True)
    if pc is None or pc.Pawn is None:
        _pending.clear()
        return
    now = game_now()
    due = [e for e in _pending if e["due"] <= now]
    if not due:
        return
    _pending[:] = [e for e in _pending if e["due"] > now and e["due"] - now < STICKY_LIFETIME]
    for entry in due:
        try:
            _detonate(entry, pc)
        except Exception:  # noqa: BLE001
            log("heartshatter sticky failed: " + traceback.format_exc()[-400:])


@hook("WillowGame.WillowAIPawn:TakeDamage", Type.POST_UNCONDITIONAL, hook_identifier="bl3w_heartshatter_ricochet")
def ricochet(obj: UObject, args, *_: object) -> None:
    """A critical hit with a HeartShatter ricochets to up to two more enemies as sticky cryo bombs (post-unconditional, like jakobs.py,
    so it also runs when an anointment blocked and re-issued the hit)."""
    pc = get_pc(possibly_loading=True)
    if pc is None or pc.Pawn is None or args.InstigatedBy is None:
        return
    if args.InstigatedBy._get_address() != pc._get_address() or args.DamageType is None:
        return
    if args.DamageType.Name.removeprefix("WillowDmgSource_") not in GUN_DAMAGE or not obj.bWasLastDamageACriticalHit:
        return
    weapon = held_weapon()
    if weapon is None or not has_fingerprint(weapon):
        return
    try:
        hops = chain_targets(pc.Pawn, obj, RICOCHETS, ricochet_range.value * 100.0)  # 1 metre = 100 Unreal units
        if not hops:
            return
        damage = args.Damage * ricochet_pct.value / 100
        for i, (src, target) in enumerate(hops):
            loc = target.Location
            _pending.append({"due": game_now() + sticky_fuse.value / 10.0, "target": WeakPointer(target), "at": (loc.X, loc.Y, loc.Z + 40),
                             "damage": damage, "weapon": WeakPointer(weapon)})
            try:
                _show(pc, weapon, args.HitLocation if i == 0 else src.Location, target)
            except Exception as e:  # noqa: BLE001 - the visual must never break the ricochet
                log(f"heartshatter ricochet tracer error: {e!r}")
        log(f"heartshatter: triple ricochet stuck {len(hops)} cryo bombs for {damage:.0f} each")
    except Exception:  # noqa: BLE001
        log("heartshatter ricochet failed: " + traceback.format_exc()[-400:])


# ---- the gun's feel ----------------------------------------------------------------------------------------------------------

_held: dict[int, dict] = {}  # weapon address -> {"base": {attr: value BL2 last computed}, "written": {attr: value we set}}


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-4 * max(1.0, abs(b))


def tune(weapon: UObject) -> None:
    """Hold the HeartShatter's feel on the held gun every frame (BL2 recomputes attributes on zoom, reload and so on): base values are
    re-read whenever BL2 has changed them. Bullet damage is scaled through InstantHitDamageBaseValue, which the hit maths reads
    (InstantHitDamage is only the displayed result; see the Hellwalker mod)."""
    entry = _held.setdefault(weapon._get_address(), {"base": {}, "written": {}})
    for attr in ("FireInterval", "ReloadTime", "InstantHitDamageBaseValue"):
        current = getattr(weapon, attr)
        if attr not in entry["written"] or not _close(current, entry["written"][attr]):
            entry["base"][attr] = current
    weapon.FireInterval = max(entry["base"]["FireInterval"], shot_delay.value / 10.0)  # never faster than the barrel's own rate
    weapon.ReloadTime = entry["base"]["ReloadTime"] * reload_pct.value / 100.0
    weapon.InstantHitDamageBaseValue = entry["base"]["InstantHitDamageBaseValue"] * damage_pct.value / 100.0
    for attr in ("FireInterval", "ReloadTime", "InstantHitDamageBaseValue"):
        entry["written"][attr] = getattr(weapon, attr)
    if weapon.ClipSize != magazine.value:
        weapon.ClipSize = magazine.value
    if weapon.ReloadCnt > magazine.value:  # a gun that was last held with a bigger magazine comes up with rounds too many
        weapon.ReloadCnt = magazine.value


@hook("WillowGame.WillowGameViewportClient:Tick", hook_identifier="bl3w_heartshatter_tick")
def on_tick(*_: object) -> None:
    try:
        detonate_due()
    except Exception:  # noqa: BLE001
        log("heartshatter bombs failed: " + traceback.format_exc()[-300:])
    try:
        pc = get_pc(possibly_loading=True)
        pawn = pc.Pawn if pc is not None else None
        if pawn is not None:
            restore_inventory(pawn)
            restore_backpack(pawn)
        weapon = pawn.Weapon if pawn is not None else None
        if weapon is not None and weapon.Class._inherits(unrealsdk.find_class("WillowWeapon")) and has_fingerprint(weapon):
            tune(weapon)
    except Exception:  # noqa: BLE001
        log("heartshatter tune failed: " + traceback.format_exc()[-300:])


# ---- giving one --------------------------------------------------------------------------------------------------------------


def _generate_raw():
    """A weapon definition for BALANCE at the player's level from the game's own reward generator (no pickup is ever spawned)."""
    global _counter
    _counter += 1
    pc = get_pc()
    bal = unrealsdk.find_object("WeaponBalanceDefinition", BALANCE)
    template = unrealsdk.find_object("ItemPoolDefinition", TEMPLATE_POOL)
    pool = unrealsdk.construct_object("ItemPoolDefinition", template.Outer, f"HS_Pool_{_counter}", template_obj=template)
    pool.BalancedItems = [template.BalancedItems[0]]
    pool.BalancedItems[0].InvBalanceDefinition = bal
    pool.BalancedItems[0].Probability.BaseValueConstant = 1.0
    base = unrealsdk.find_object("MissionDefinition", TEMPLATE_MISSION)
    mission = unrealsdk.construct_object("MissionDefinition", base.Outer, f"HS_Mission_{_counter}", template_obj=base)
    mission.GameStage = int(pc.Pawn.GetExpLevel())
    mission.Reward.RewardItemPools = [pool]
    out = mission.GetItemRewardsForPlayer(pc, unrealsdk.make_struct("PendingMissionRewardData"))[1]
    for x in out.WeaponRewards:
        if x.BalanceDefinition is not None:
            return x
    raise RuntimeError("the reward generator returned no weapon")


def _generate():
    """A weapon definition for BALANCE: a plain one (no HeartShatter roll while it is made, which would hand back a gun with the foreign parts)."""
    saved = drop_chance.value
    drop_chance.value = 0
    try:
        return _generate_raw()
    finally:
        drop_chance.value = saved


def make_heartshatter(add_to_backpack: bool = True) -> UObject:
    """A new HeartShatter; in the backpack unless add_to_backpack is False."""
    pawn = get_pc().Pawn
    dd = _generate()
    base = copy.copy(dd)
    for field, part in _parts_for_new_gun().items():
        setattr(dd, field, part)
    remember(dd, base=base)
    weapon = unrealsdk.find_class("WillowWeapon").ClassDefaultObject.CreateWeaponFromDef(dd, pawn, False)
    if add_to_backpack:
        if pawn.InvManager.AddInventoryToBackpack(weapon) is False:  # the backpack is full
            weapon.Destroy()
            raise RuntimeError("the backpack is full")
    log(f"heartshatter: made one ({'backpack' if add_to_backpack else 'not added'})")
    return weapon


@command("heartshatter", description="heartshatter give: put a new HeartShatter in the backpack")
def command_heartshatter(args) -> None:
    try:
        if args.subcommand == "give":
            make_heartshatter()
            print("HeartShatter added to your backpack.")
    except Exception:  # noqa: BLE001
        log("heartshatter give failed: " + traceback.format_exc()[-500:])
        print("HeartShatter: could not create the gun (see unrealsdk.log).")


command_heartshatter.add_argument("subcommand", choices=["give"], help="subcommand")

# ---- keys to look at the different forms ------------------------------------------------------------------------------------


def describe(weapon: UObject) -> str:
    """'Maliwan_5_Legendary skin | SR_Body_Dahl_4 | Mount3_Damage | Shock' for the HUD message."""
    d = weapon.DefinitionData

    def name(part: UObject | None) -> str:
        return part.Name.replace("Material_", "").replace("SR_Body_", "").replace("SR_Grip_", "").replace("SR_Stock_", "").replace("Sniper_Accessory_", "").replace("SR_Elemental_", "") if part is not None else "-"

    return (f"{display_name(weapon)}: skin {name(d.MaterialPartDefinition)} | body {name(d.BodyPartDefinition)} | grip {name(d.GripPartDefinition)} | stock {name(d.StockPartDefinition)} | "
            f"{name(d.Accessory1PartDefinition)} | {name(d.ElementalPartDefinition)}")


@keybind("Reroll HeartShatter Look", "F6", description="Test: give the HeartShatter in your hands a new random look (its barrel, Dahl sight and bipod stay).")
def reroll_look() -> None:
    try:
        weapon = held_weapon()
        if weapon is None or not has_fingerprint(weapon):
            show_hud_message("HeartShatter", "Hold a HeartShatter first (spawn one with the Spawn HeartShatter key).", 2.5)
            return
        parts = _parts_for_new_gun()
        rebuild(weapon, **{f: p for f, p in parts.items() if f not in LOCKED})
        remember(weapon.DefinitionData)
        show_hud_message("HeartShatter", describe(weapon), 4)
        log("heartshatter: rerolled look: " + describe(weapon))
    except Exception:  # noqa: BLE001
        log("heartshatter reroll failed: " + traceback.format_exc()[-400:])


@keybind("Spawn HeartShatter", "F5", description="Test: put a new random HeartShatter in your backpack.")
def spawn_one() -> None:
    try:
        weapon = make_heartshatter(add_to_backpack=True)
        if weapon.GetAmmoCount() == 0:
            weapon.AddAmmo(60)
        show_hud_message("HeartShatter added to your backpack", describe(weapon), 4)
    except Exception as ex:  # noqa: BLE001
        log("heartshatter spawn failed: " + traceback.format_exc()[-400:])
        show_hud_message("HeartShatter", f"Could not spawn one: {ex}", 3)


KEYBINDS = [reroll_look, spawn_one]
HOOKS = [on_roll, on_name, on_short_name, on_tick, ricochet, on_arrival, on_save_start, on_save_end, validate]
