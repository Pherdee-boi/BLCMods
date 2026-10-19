"""Test buttons (mod options): test loot in the backpack, and the loot drop check (lootcheck.py).

The loot: a radiation E-tech gun, a radiation legendary gun, a radiation shield and grenade and a cryo shield and grenade.

The items come from the game's own reward generator (as the radiation guns do: no pickup is ever spawned), at the player's level. A radiation
shield is a Corrosive Nova or Spike shield and a radiation grenade any grenade mod, both flagged radioactive (radgear.py). A cryo shield is one of the
Cryo mod's (Nova or Spike); a cryo grenade is a grenade rolled until it has the Cryo mod's accessory (the Cryo mod must be installed).
"""

import random
import traceback

import unrealsdk
from mods_base import ButtonOption, get_pc
from ui_utils import show_hud_message
from unrealsdk.unreal import UObject

from . import lootcheck, radgear, radiation
from .core import log, state

GRADES = ("05_Legendary", "04_VeryRare", "03_Rare")  # tried from the best down: some grades only exist from a higher level
RADIATION_GRADES = ("04_VeryRare", "03_Rare")  # radiation shields are never legendary
VANILLA_SHIELD = "GD_ItemGrades.Shields.ItemGrade_Gear_Shield_{kind}_{element}_{grade}"
CRYO_SHIELD = "GD_AureliaCryoGear.ItemGrade_Gear_Shield_{kind}_Ice_{grade}"
PART_SLOTS = ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Material")
CRYO_ACCESSORY = "GD_AureliaCryoGear.Accessory_Ice"
TRIES = 120  # rolls for a cryo grenade

_pool: dict = {}


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _pin(feature: str, item: UObject, value: list[str]) -> None:
    """Set an item's saved roll outright (core.remembered keeps a roll that was already made: adding the item to the backpack names it, and a name rolls it)."""
    state.value = {**state.value, f"{feature}:{item.DefinitionData.UniqueId}": value}
    state.save()


def _definition(balance: UObject):
    """An item definition for this balance at the player's level (the reward generator; None when it makes nothing at this level)."""
    pc = get_pc()
    if not _pool:
        template = unrealsdk.find_object("ItemPoolDefinition", radiation.TEMPLATE_POOL)
        pool = radiation._existing("ItemPoolDefinition", template.Outer, "RAD_ItemPool")
        if pool is None:
            pool = unrealsdk.construct_object("ItemPoolDefinition", template.Outer, "RAD_ItemPool", template_obj=template)
            pool.BalancedItems = [template.BalancedItems[0]]
        base = unrealsdk.find_object("MissionDefinition", radiation.TEMPLATE_MISSION)
        mission = radiation._existing("MissionDefinition", base.Outer, "RAD_ItemMission")
        if mission is None:
            mission = unrealsdk.construct_object("MissionDefinition", base.Outer, "RAD_ItemMission", template_obj=base)
        radiation._root(pool)
        radiation._root(mission)
        mission.Reward.RewardItemPools = [pool]
        _pool.update(pool=pool, mission=mission)
    pool, mission = _pool["pool"], _pool["mission"]
    pool.BalancedItems[0].InvBalanceDefinition = balance
    pool.BalancedItems[0].Probability.BaseValueConstant = 1.0
    mission.GameStage = int(pc.Pawn.GetExpLevel())
    out = mission.GetItemRewardsForPlayer(pc, unrealsdk.make_struct("PendingMissionRewardData"))[1]
    return next((x for x in out.ItemRewards if x.BalanceDefinition is not None), None)


def _give(dd) -> UObject:
    pawn = get_pc().Pawn
    item = unrealsdk.find_class("WillowItem").ClassDefaultObject.CreateItemFromDef(dd, pawn, 1, False)
    if pawn.InvManager.AddInventoryToBackpack(item) is False:
        item.Destroy()
        raise RuntimeError("the backpack is full")
    return item


def _shield(template: str, grades: tuple[str, ...] = GRADES, **fields: str) -> tuple[UObject, str] | None:
    for grade in grades:
        balance = _find("InventoryBalanceDefinition", template.format(grade=grade, **fields))
        dd = _definition(balance) if balance is not None else None
        if dd is not None:
            return _give(dd), balance.Name
    return None


def _grenade_balances() -> list[UObject]:
    out = [b for b in unrealsdk.find_all("InventoryBalanceDefinition", exact=False)
           if b._path_name().startswith("GD_GrenadeMods.A_Item.GM_") and b._path_name().endswith(("_3_Rare", "_4_VeryRare"))]
    return out or [b for b in unrealsdk.find_all("InventoryBalanceDefinition", exact=False) if b._path_name().startswith("GD_GrenadeMods.A_Item.GM_")]


def _has_cryo(dd) -> bool:
    return any((getattr(dd, f"{slot}ItemPartDefinition", None) is not None
                and getattr(dd, f"{slot}ItemPartDefinition")._path_name().startswith(CRYO_ACCESSORY)) for slot in PART_SLOTS)


def _gun(balance: str) -> UObject:
    return radiation.make_gun(balance=balance)


def spawn_test_loot(_button: ButtonOption | None = None) -> None:
    """One button: a radiation E-tech gun and a radiation legendary gun, then a radiation shield and grenade and a cryo shield and grenade."""
    got, failed = [], []

    def attempt(label: str, make) -> None:
        try:
            made = make()
            if made is not None:
                got.append(label)
            else:
                failed.append(label)
        except Exception as ex:  # noqa: BLE001
            failed.append(f"{label} ({ex})")
            log(f"test loot: {label} failed: " + traceback.format_exc()[-300:])

    etech, legendary = radiation.special_balances("etech"), radiation.special_balances("legendary")
    attempt("radiation E-tech gun", lambda: _gun(random.choice(etech)) if etech else None)
    attempt("radiation legendary gun", lambda: _gun(random.choice(legendary)) if legendary else None)
    attempt("radiation shield", _radiation_shield)
    attempt("radiation grenade", _radiation_grenade)
    attempt("cryo shield", _cryo_shield)
    attempt("cryo grenade", _cryo_grenade)
    log(f"test loot: {got}; not made: {failed}")
    show_hud_message("Test loot", "Added: " + (", ".join(got) or "nothing") + (f". Not made: {', '.join(failed)}" if failed else ""), 8)


def _radiation_shield():
    made = _shield(VANILLA_SHIELD, RADIATION_GRADES, kind=random.choice(("Nova", "Spike")), element=random.choice(("Acid", "Fire", "Shock")))  # (BL2 calls corrosive "Acid" in shield names)
    if made is not None:
        _pin("radshield", made[0], ["radiation"])
        radgear.convert(made[0])
    return made


def _radiation_grenade():
    for _ in range(TRIES):
        dd = _definition(random.choice(_grenade_balances()))
        if dd is None or not radgear.GRENADE_PART.match(getattr(dd, radgear.PART_FIELD)._path_name() if getattr(dd, radgear.PART_FIELD, None) is not None else ""):
            continue
        item = _give(dd)
        _pin("radgrenade", item, ["radiation"])
        radgear.convert(item)
        return item
    return None


def _cryo_shield():
    if _find("InventoryBalanceDefinition", CRYO_SHIELD.format(kind="Nova", grade=GRADES[0])) is None:
        raise RuntimeError("the Cryo mod isn't loaded")
    made = _shield(CRYO_SHIELD, kind=random.choice(("Nova", "Spike")))
    if made is not None:
        _pin("radshield", made[0], [])  # a cryo test item must not also roll radiation
    return made


def _cryo_grenade():
    balances = _grenade_balances()
    for _ in range(TRIES):
        dd = _definition(random.choice(balances))
        if dd is not None and _has_cryo(dd):
            item = _give(dd)
            _pin("radgrenade", item, [])
            return item
    return None


spawn_button = ButtonOption("Spawn Test Loot (E-tech + legendary radiation guns, radiation and cryo gear)", on_press=spawn_test_loot,
                            description="Test: puts in your backpack a radiation E-tech gun, a radiation legendary gun, a radiation shield and grenade and a cryo shield and grenade (use it in game, from the pause menu; needs 6 free slots).")
check_button = ButtonOption("Loot Drop Check (test)", on_press=lootcheck.press,
                            description="Test: draws 400 weapons from the game's loot pools at your level and counts rarity and element, and what this mod rolls on top. The report is in bl3_weapons/loot_check.txt.")
OPTIONS = [spawn_button, check_button]
