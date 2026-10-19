"""Item cards: show the gun's BL3 anointment and a Maliwan gun's second element, like BL3's cards.

Every item card (inventory, vendors, the loot you look at) is an ItemCardGFxObject:
  SetItemCardEx(WPC, InventoryItem, CompareAgainstInventoryItem, CurrencyType, ...) / SetItemCard(...)
      -> tells us which item the card shows (remembered per card);
  SetFunStats(FunStatsText) -> the red flavour-text block; extra lines are appended there (block and
      re-issue once with the longer text, like the damage hook).
Rolls are the same ones the gun gets when equipped (seeded by UniqueId and saved), so the card tells the
truth before you pick the gun up. Items without a UniqueId yet are left alone (all of them would share
one roll).
"""

import unrealsdk
from mods_base import BoolOption, hook
from unrealsdk.hooks import Block, Type, prevent_hooking_direct_calls
from unrealsdk.unreal import UObject, WeakPointer

from .core import log, maker_of

show_on_card = BoolOption("Show On Item Cards", True, description="Show anointments and Maliwan second elements on item cards.")
OPTIONS = [show_on_card]

ANOINT_COLOUR = "#A6F06A"
UNDERBARREL_COLOUR = "#FFB45A"
ELEMENT_COLOURS = {"Fire": "#FF8A3D", "Shock": "#4FB0FF", "Corrosive": "#A4E34A", "Slag": "#C07BFF", "Cryo": "#5FE8FF", "Radiation": "#D6E719"}

_card_item: dict[int, WeakPointer] = {}  # card object address -> item it shows
_logged: set[str] = set()


def _log_once(key: str, msg: str) -> None:
    if key not in _logged:
        _logged.add(key)
        log(msg)


def _remember_item(obj: UObject, item: UObject | None) -> None:
    if item is None:
        _card_item.pop(obj._get_address(), None)
    else:
        _card_item[obj._get_address()] = WeakPointer(item)


ROW_PRESENTATIONS = "GD_AttributePresentation.Shields.AttrPresent_Att_Shield{kind}Damage_{element}"
_row_originals: dict[str, str] = {}


def _set_row_icons(radiation_gear: bool) -> None:
    """A shield's "Spike Damage" / "Nova Damage" row takes its icon from a presentation made for each element (the rows are filled natively, so
    that is where the icon can be changed): while a radiation shield's card is built they all say radiation's, and are put back after."""
    from . import elementicons

    for kind in ("Spike", "Nova"):
        for element in ("Corrosive", "Explosive", "Fire", "Shock"):
            path = ROW_PRESENTATIONS.format(kind=kind, element=element)
            try:
                presentation = unrealsdk.find_object("AttributePresentationDefinition", path)
            except ValueError:
                continue
            if radiation_gear:
                _row_originals.setdefault(path, presentation.Icon)
                presentation.Icon = elementicons.ROW_FRAME
            elif path in _row_originals:
                presentation.Icon = _row_originals.pop(path)


def _is_radiation_shield(item: UObject | None) -> bool:
    if item is None:
        return False
    try:
        from . import elementicons, gear, radgear

        return elementicons.ready and gear.item_kind(item) == "shield" and radgear.is_radiation_gear(item)
    except Exception as e:  # noqa: BLE001 - a card must always show
        _log_once("rg_row", f"radiation gear row icons: {e!r}")
        return False


@hook("WillowGame.ItemCardGFxObject:SetItemCardEx")
def card_item_ex(obj: UObject, args, *_: object) -> None:
    _remember_item(obj, args.InventoryItem)
    if _is_radiation_shield(args.InventoryItem):
        _set_row_icons(True)
    elif _row_originals:
        _set_row_icons(False)


@hook("WillowGame.ItemCardGFxObject:SetItemCardEx", Type.POST, hook_identifier="bl3w_card_rows_restore")
def card_item_ex_done(*_: object) -> None:
    if _row_originals:
        _set_row_icons(False)


@hook("WillowGame.ItemCardGFxObject:SetItemCard")
def card_item(obj: UObject, args, *_: object) -> None:
    _remember_item(obj, args.InventoryItem)
    if _is_radiation_shield(args.InventoryItem):
        _set_row_icons(True)
    elif _row_originals:
        _set_row_icons(False)  # (a card built for another item never shows radiation's icons)


def _maliwan_alt(weapon: UObject) -> str | None:
    """The element the mode key switches to (same roll as maliwan.switch)."""
    from . import maliwan

    return maliwan.alt_of(weapon)


TRAIT_COLOUR = "#D8DCE6"


def _trait(item: UObject) -> str | None:
    """One line naming the gun's BL3 manufacturer trait (v0.35.0)."""
    from . import dahl, tediore, torgue

    maker = maker_of(item)
    if maker == "Dahl":
        return f"Fire modes: full-auto / burst / semi (now: {dahl._label(item, dahl.mode_of(item)).lower()})"
    if maker == "Torgue":
        return None if torgue.special_fire(item) is not None else "Alt mode: sticky / impact rounds"
    if maker == "Tediore":
        return f"Thrown: {tediore.throw_of(item)}"
    if maker == "Hyperion":
        return "Aim down sights: frontal energy shield"
    if maker == "Jakobs":
        return "Critical hits ricochet"
    if maker == "Bandit":
        return "Overheats instead of reloading: reload when hot to vent"
    return None


def card_lines(item: UObject) -> list[str]:
    from . import gear

    if gear.item_kind(item) is not None:
        if item.DefinitionData.UniqueId == 0:
            return []
        line = gear.card_line(item)
        return [f'<font color="{ANOINT_COLOUR}">{line}</font>'] if line else []
    if not item.Class._inherits(unrealsdk.find_class("WillowWeapon")):
        return []
    if item.DefinitionData.UniqueId == 0:
        _log_once("uid0", "item card: gun without UniqueId yet, not shown")
        return []
    from . import anoint, maliwan

    lines = []
    kind = anoint.anointment_of(item)
    if kind:
        lines.append(f'<font color="{ANOINT_COLOUR}">Anointed: {anoint.ANOINTMENTS[kind][0]}</font>')
    trait = _trait(item)
    if trait:
        lines.append(f'<font color="{TRAIT_COLOUR}">{trait}</font>')
    if maker_of(item) == "Vladof":
        from . import vladof

        kind = vladof.underbarrel_kind(item)
        if kind:
            lines.append(f'<font color="{UNDERBARREL_COLOUR}">Underbarrel: {kind}</font>')
    if maker_of(item) == "Maliwan":
        alt = _maliwan_alt(item)
        if alt:
            lines.append(f'<font color="{ELEMENT_COLOURS.get(alt, "#FFFFFF")}">Alt element: {maliwan.NICE.get(alt, alt)}</font>')
    return lines


@hook("WillowGame.ItemCardGFxObject:SetFunStats")
def fun_stats(obj: UObject, args, _ret, func) -> type[Block] | None:
    if not show_on_card.value:
        return None
    ref = _card_item.get(obj._get_address())
    item = ref() if ref is not None else None
    if item is None:
        return None
    try:
        lines = card_lines(item)
    except Exception as e:  # noqa: BLE001 - a card must always show
        _log_once("card_error", f"item card error: {e!r}")
        return None
    from . import radgear

    gear_text = False
    try:
        gear_text = radgear.is_radiation_gear(item)
    except Exception as e:  # noqa: BLE001
        _log_once("rg_card", f"radiation gear card text: {e!r}")
    if not lines and not gear_text:
        return None
    text = args.FunStatsText or ""
    from . import heartshatter

    try:
        if item.Class._inherits(unrealsdk.find_class("WillowWeapon")) and heartshatter.has_fingerprint(item):
            text = heartshatter.fun_stats_text(text)  # its own red text leads; the Volcano barrel's is dropped
    except Exception as e:  # noqa: BLE001
        _log_once("hs_card", f"heartshatter card text: {e!r}")
    if gear_text:
        text = radgear.radiation_text(text)  # a radiation grenade's / shield's description says radiation, not the element it started as
    args.FunStatsText = text + ("\n" if text else "") + "\n".join(lines)
    _log_once("card_ok", f"item card lines for {item.Class.Name}: {lines}")
    with prevent_hooking_direct_calls():
        func(args)
    return Block


def _gear_icon(obj: UObject, args, func) -> type[Block] | None:
    """A radiation grenade mod's / shield's title icon: the card is told its original element's frame ("Fire", "amp"); it gets radiation's."""
    ref = _card_item.get(obj._get_address())
    item = ref() if ref is not None else None
    if item is None:
        return None
    try:
        from . import elementicons, radgear

        if not elementicons.ready or not radgear.is_radiation_gear(item):
            return None
        args.ElementalIcon = elementicons.TITLE_FRAME
    except Exception as e:  # noqa: BLE001 - a card must always show
        _log_once("rg_icon", f"radiation gear card icon: {e!r}")
        return None
    with prevent_hooking_direct_calls():
        func(args)
    return Block


@hook("WillowGame.ItemCardGFxObject:SetTitle")
def card_title(obj: UObject, args, _ret, func) -> type[Block] | None:
    return _gear_icon(obj, args, func)


@hook("WillowGame.ItemCardGFxObject:SetColor")
def card_colour(obj: UObject, args, _ret, func) -> type[Block] | None:
    return _gear_icon(obj, args, func)


HOOKS = [card_item_ex, card_item_ex_done, card_item, fun_stats, card_title, card_colour]
