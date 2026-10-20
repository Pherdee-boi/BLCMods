"""Maliwan: every elemental gun gets a second element; the mode key swaps between them (BL3 alt mode).

Radiation (this mod's own element) works like cryo: a Maliwan gun with radiation gets a second element out of BL2's four, and the key swaps.

Cryo (the aurelia mod, v0.37.1): its `<Type>_Elemental_Cryo` parts live in their own package (GD_AureliaCryo), not next
to BL2's `<weapons>.elemental.<Type>_Elemental_<Element>` parts, so the old "same package, other suffix" lookup found
nothing and a cryo Maliwan gun was treated as having no element. A cryo gun now gets a second element out of BL2's four
like any other. Cryo and radiation are in the pool a gun's second element is picked from, next to BL2's four (2026-10-18).
"""

from ui_utils import show_hud_message
from unrealsdk.unreal import UObject

from .core import addon_on, find_part, log, rebuild, remembered

ELEMENTS = ("Fire", "Corrosive", "Shock", "Slag")
CRYO = "Cryo"
RADIATION = "Radiation"  # this mod's own element (radiation.py): its parts sit next to BL2's, named like them
NICE = {"Fire": "Incendiary", "Corrosive": "Corrosive", "Shock": "Shock", "Slag": "Slag", CRYO: "Cryo", RADIATION: "Radiation"}
# weapon type (the first word of an elemental part's name) -> BL2's package of its parts; the same table aurelia uses
WEAPON_PACKAGES = {"Pistol": "GD_Weap_Pistol", "SMG": "GD_Weap_SMG", "AR": "GD_Weap_AssaultRifle",
                   "Shotgun": "GD_Weap_Shotgun", "SR": "GD_Weap_SniperRifles", "RL": "GD_Weap_Launchers"}
CRYO_PACKAGE = "GD_AureliaCryo"


def _element_of(part: UObject | None) -> str | None:
    """'GD_Weap_SMG.elemental.SMG_Elemental_Fire' -> 'Fire'; 'GD_AureliaCryo.SMG_Elemental_Cryo' -> 'Cryo';
    None for non-elemental parts."""
    if part is None:
        return None
    suffix = part.Name.rsplit("_", 1)[-1]
    return suffix if suffix in ELEMENTS or suffix in (CRYO, RADIATION) else None


def _sibling(part: UObject, element: str) -> UObject | None:
    """SMG_Elemental_Fire -> SMG_Elemental_Shock (same weapon type); any of them -> the cryo part and back. A Ribs Arsenal shotgun's
    Elemental_Tile_Fire -> its own Elemental_Tile_Shock; to radiation or cryo -> the shotgun type's part (v0.64.10: it had no sibling,
    so such a gun said "Radiation isn't available right now" whenever its element was the Ribs part, and worked while it was the vanilla one)."""
    kind = part.Name.split("_", 1)[0]  # 'SMG', 'Pistol', 'AR', ...
    if part.Name.startswith("Elemental_Tile_"):  # the Ribs Arsenal shotguns' own element parts (MaliwanShotgunDev): they have the four BL2 elements
        if element not in (CRYO, RADIATION):  # (their own parts, found below by name) but no radiation or cryo: those come from the vanilla shotgun parts
            return find_part(part._path_name().rsplit("_", 1)[0] + "_" + element)
        kind = "Shotgun"
    if element == CRYO:
        return find_part(f"{CRYO_PACKAGE}.{kind}_Elemental_Cryo")
    package = WEAPON_PACKAGES.get(kind)
    if package is None:  # a part of a type this table doesn't know: BL2's own naming, as before
        return find_part(part._path_name().rsplit("_", 1)[0] + "_" + element)
    return find_part(f"{package}.elemental.{kind}_Elemental_{element}")


def second_element(part: UObject, current: str, rng) -> list[str] | None:
    """[current, other] for a new gun: the other one is one of BL2's four, cryo or radiation, whichever this weapon type has a part for."""
    options = [e for e in (*ELEMENTS, CRYO, RADIATION) if e != current and _sibling(part, e) is not None]
    return [current, rng.choice(options)] if options else None


def alt_of(weapon: UObject) -> str | None:
    """The element the mode key switches the gun to (None: no element, or no second one rolled)."""
    if not addon_on("Maliwan"):
        return None
    part = weapon.DefinitionData.ElementalPartDefinition
    current = _element_of(part)
    if current is None:
        return None
    pair = remembered("maliwan", weapon, lambda rng: second_element(part, current, rng))
    if not pair or current not in pair:
        return None
    return pair[1] if current == pair[0] else pair[0]


def switch(weapon: UObject) -> None:
    part = weapon.DefinitionData.ElementalPartDefinition
    current = _element_of(part)
    if current is None:
        show_hud_message("Element Switch", "This Maliwan gun has no element.", 1.5)
        return

    target = alt_of(weapon)
    if target is None:
        log(f"no element pair for {part._path_name()}")
        return
    new_part = _sibling(part, target)
    if new_part is None:
        # e.g. aurelia was switched off after the gun was saved with cryo as its other element
        show_hud_message("Element Switch", f"{NICE[target]} isn't available right now.", 1.5)
        log(f"Maliwan #{weapon.DefinitionData.UniqueId}: no {target} part for {part._path_name()}")
        return

    rebuild(weapon, ElementalPartDefinition=new_part)
    log(f"Maliwan #{weapon.DefinitionData.UniqueId}: {current} -> {target}")
    from . import radiation

    radiation.note_element(weapon, target)  # a saved radiation gun is remembered as such (it comes back without the element otherwise)
    from . import indicator

    indicator.bump()
    indicator.flick(target)  # quick gun flick + colour flash
