"""Shared helpers: find the held gun, look up parts, rebuild a gun live with swapped parts."""

import contextlib
import copy
import random
import time

import unrealsdk
from mods_base import HiddenOption, get_pc, hook
from unrealsdk.unreal import UObject

# Per-gun saved state: "<feature>:<UniqueId>" -> feature-specific JSON (e.g. an element pair)
state: HiddenOption[dict[str, list[str]]] = HiddenOption("state", {})


_clock = 0.0
_clock_last = time.monotonic()


def now() -> float:
    """Game time in seconds: stops while the game is paused (WorldInfo.Pauser), unlike time.monotonic().
    Every timer in this mod (cooldowns, heat, shield recharge, buffs, turrets) uses it."""
    return _clock


@hook("WillowGame.WillowGameViewportClient:Tick", hook_identifier="bl3w_game_clock")
def clock_tick(*_: object) -> None:
    global _clock, _clock_last
    t = time.monotonic()
    dt, _clock_last = min(t - _clock_last, 0.25), t
    pc = get_pc(possibly_loading=True)
    world = pc.WorldInfo if pc is not None else None
    if world is not None and world.Pauser is None:
        _clock += dt


def log(msg: str) -> None:
    unrealsdk.logging.info(f"[BL3Weapons] {msg}")


def held_weapon() -> UObject | None:
    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc else None
    weapon = pawn.Weapon if pawn else None
    if weapon is None or not weapon.Class._inherits(unrealsdk.find_class("WillowWeapon")):
        return None
    return weapon


def maker_of(weapon: UObject) -> str:
    """'Maliwan', 'Vladof', ... or '' if unknown."""
    maker = weapon.DefinitionData.ManufacturerDefinition
    return maker.Name if maker is not None else ""


PART_FIELDS = (
    "BodyPartDefinition", "GripPartDefinition", "BarrelPartDefinition", "SightPartDefinition",
    "StockPartDefinition", "ElementalPartDefinition", "Accessory1PartDefinition",
    "Accessory2PartDefinition", "MaterialPartDefinition",
)


def parts_of(weapon: UObject) -> list[UObject]:
    data = weapon.DefinitionData
    return [p for p in (getattr(data, f) for f in PART_FIELDS) if p is not None]


def describe(weapon: UObject) -> str:
    """'Pistol_Torgue_5_Calla #79365428' for logs."""
    bal = weapon.DefinitionData.BalanceDefinition
    return f"{bal.Name if bal is not None else '?'} #{weapon.DefinitionData.UniqueId}"


def pawns(player_pawn: UObject):
    """Walk WorldInfo.PawnList safely: stops on a cycle or after 1000 steps (a pawn removed from the
    list mid-walk, e.g. killed, can leave NextPawn pointing back into the list and hang the game).
    Callers must not damage/kill pawns while iterating: collect first, then act."""
    seen: set[int] = set()
    pawn = player_pawn.WorldInfo.PawnList
    while pawn is not None and len(seen) < 1000:
        addr = pawn._get_address()
        if addr in seen:
            return
        seen.add(addr)
        yield pawn
        pawn = pawn.NextPawn


def find_part(path: str) -> UObject | None:
    try:
        return unrealsdk.find_object("WeaponPartDefinition", path)
    except ValueError:
        return None


def remembered(feature: str, weapon: UObject, make: "callable") -> list[str] | None:
    """Saved per-gun value for a feature; created once with make(rng) and persisted."""
    key = f"{feature}:{weapon.DefinitionData.UniqueId}"
    saved = state.value.get(key)
    if saved is not None:
        return saved
    value = make(random.Random(key))  # string seed: independent rolls per feature for the same gun
    if value is None:
        return None
    state.value = {**state.value, key: value}
    state.save()
    return value


def forget(feature: str, weapon: UObject) -> None:
    key = f"{feature}:{weapon.DefinitionData.UniqueId}"
    if key in state.value:
        state.value = {k: v for k, v in state.value.items() if k != key}
        state.save()


# Context-manager factories (weapon -> context manager) a rebuild enters around the gun's re-initialisation, so a
# feature can change what the new mesh is built from (sights.py hides the scope-mount bracket this way).
mesh_guards: list = []


_attributes: list[str] | None = None  # the names of a gun's attributes (a number with a "<name>BaseValue" beside it), found once


def _attribute_names(weapon: UObject) -> list[str]:
    global _attributes
    if _attributes is None:
        names = {f.Name for f in weapon.Class._fields() if f.Class.Name not in ("Function", "ScriptStruct", "Enum", "Const")}
        _attributes = sorted(n[:-9] for n in names if n.endswith("BaseValue") and n[:-9] in names)
    return _attributes


def reset_attributes(weapon: UObject) -> None:
    """Put every attribute of the gun back to the class's own defaults, as a new gun has them. Initialising a gun again applies its parts'
    effects ON TOP of the values it has (found 2026-10-14): after a dozen element switches a Maliwan SMG's projectile speed was -0.8, its
    extra-shot delay a third of what it was and its status chance 4x, and its shots did no damage and left no marks; a Jakobs pistol gained a
    ricochet each time. Every use of rebuild() did this once. Starting from the defaults, one initialisation gives what a new gun has."""
    cdo = weapon.Class.ClassDefaultObject
    for name in _attribute_names(weapon):
        try:
            base = getattr(cdo, name + "BaseValue")
            setattr(weapon, name + "BaseValue", base)
            setattr(weapon, name, base)
        except Exception:  # noqa: BLE001 - an attribute of another type or one that cannot be written: left as it is
            pass


def rebuild(weapon: UObject, loaded: int | None = None, **parts: UObject) -> None:
    """Swap DefinitionData fields (e.g. BarrelPartDefinition=...) and re-init the held gun in place.

    WillowWeapon.InitializeFromDefinitionData(NewDefinitionData, InAdditionalQueryInterfaceSource,
    bForceSelectNameParts), verified in game 2026-10-02. `loaded` sets the rounds left in the
    magazine afterwards (WillowWeapon.ReloadCnt = rounds remaining, verified 2026-10-02);
    by default it keeps the current value. The gun's attributes are reset first (reset_attributes).
    """
    new_def = copy.copy(weapon.DefinitionData)
    for field, part in parts.items():
        setattr(new_def, field, part)
    if loaded is None:
        loaded = weapon.ReloadCnt
    reset_attributes(weapon)
    with contextlib.ExitStack() as stack:
        for guard in mesh_guards:
            stack.enter_context(guard(weapon))
        weapon.InitializeFromDefinitionData(new_def, weapon.Owner, False)
    weapon.ReloadCnt = max(0, min(loaded, weapon.ClipSize))
