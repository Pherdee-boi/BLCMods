"""Loot drop check: draws weapons from the game's own loot pools at the player's level and counts what comes out, so drop rates can be balanced.

The weapon pools are the game's (the reward generator, as the test spawns use: no pickup is ever made). The generator builds real guns, so this mod's
own rolls (radiation, HeartShatter, Hellwalker) happen in these draws too and are counted as they come, next to the rarity class and element the game
rolled (cryo included: it is in the loot lists). A small draw is a small sample: 100 guns give a share to within about +-5 points. The report goes to the log, to loot_check.txt beside this file, and a short line to the screen.
"""

import collections
import re
import sys
import time
import traceback
from pathlib import Path

import unrealsdk
from mods_base import get_pc
from ui_utils import show_hud_message

from . import heartshatter, radiation
from .core import log

REPORT = Path(__file__).with_name("loot_check.txt")
POOLS = (  # (label, pool, share of the draws)
    ("World drops: all weapons", "GD_Itempools.WeaponPools.Pool_Weapons_All", 0.5),
    ("E-tech weapon pool", "GD_Itempools.WeaponPools.Pool_Weapons_All_05_VeryRare_Alien", 0.25),
    ("Legendary weapon pool", "GD_Itempools.WeaponPools.Pool_Weapons_All_06_Legendary", 0.25),
)
TOTAL = 400  # draws in one check
_mission: dict = {}


def tier(path: str) -> str:
    """The rarity class of a gun from its balance's name and folder."""
    if "A_Weapons_Legendary" in path:
        return "legendary"
    if "A_Weapons_Unique" in path:
        return "unique"
    if "A_Weapons_Elemental" in path:
        return "elemental set"
    if "_Alien" in path:
        return "e-tech"
    grade = re.search(r"_(2_Uncommon|3_Rare|4_VeryRare)$", path)
    return {"2_Uncommon": "uncommon", "3_Rare": "rare", "4_VeryRare": "epic"}[grade.group(1)] if grade else "white/other"


def element(part) -> str:
    if part is None:
        return "none"
    path = part._path_name()
    if "AureliaCryo" in path:
        return "cryo"
    return re.sub(r"^.*Elemental_", "", part.Name).lower() or "none"


def _draw(pool_path: str, count: int):
    pc = get_pc()
    pool = unrealsdk.find_object("ItemPoolDefinition", pool_path)
    if not _mission:
        base = unrealsdk.find_object("MissionDefinition", radiation.TEMPLATE_MISSION)
        mission = radiation._existing("MissionDefinition", base.Outer, "RAD_CheckMission")
        if mission is None:
            mission = unrealsdk.construct_object("MissionDefinition", base.Outer, "RAD_CheckMission", template_obj=base)
        radiation._root(mission)
        _mission["mission"] = mission
    mission = _mission["mission"]
    mission.GameStage = int(pc.Pawn.GetExpLevel())
    mission.Reward.RewardItemPools = [pool]
    found = []
    for _ in range(count * 3):  # (a draw can come back empty at a level the pool has nothing for)
        out = mission.GetItemRewardsForPlayer(pc, unrealsdk.make_struct("PendingMissionRewardData"))[1]
        found += [x for x in out.WeaponRewards if x.BalanceDefinition is not None]
        if len(found) >= count:
            break
    return found[:count]


def _section(label: str, draws: list) -> list[str]:
    tiers, elements, by_tier = collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter)
    rolled = {"radiation": 0, "HeartShatter": 0, "Hellwalker": 0}
    for dd in draws:
        path = dd.BalanceDefinition._path_name()
        maker = dd.ManufacturerDefinition.Name if dd.ManufacturerDefinition is not None else None
        t, e = tier(path), element(dd.ElementalPartDefinition)
        tiers[t] += 1
        elements[e] += 1
        by_tier[t][e] += 1
        rolled["radiation"] += e == "radiation"
        rolled["HeartShatter"] += heartshatter.fingerprint_data(dd)
        hellwalker = sys.modules.get("hellwalker")
        rolled["Hellwalker"] += bool(hellwalker is not None and hellwalker.fingerprint_data(dd))
    n = max(1, len(draws))

    def table(counter: collections.Counter) -> str:
        return ", ".join(f"{k} {v} ({100 * v / sum(counter.values()):.1f}%)" for k, v in counter.most_common())

    lines = [f"== {label}: {len(draws)} guns ==", "rarity class: " + table(tiers), "element the game rolled: " + table(elements)]
    for t in ("e-tech", "legendary"):
        if by_tier.get(t):
            lines.append(f"  {t} guns' elements: " + table(by_tier[t]))
    lines.append("rolled by this mod in these draws: " + ", ".join(f"{k} {v} ({100 * v / n:.2f}%)" for k, v in rolled.items()))
    return lines


def run(total: int = TOTAL) -> list[str]:
    """Draw and count; returns the report's lines (also written to loot_check.txt and the log)."""
    started = time.monotonic()
    pc = get_pc()
    lines = [f"Loot check at level {int(pc.Pawn.GetExpLevel())}, {time.strftime('%Y-%m-%d %H:%M')}",
             f"drop chances now: radiation plain {radiation.drop_chance.value}%, radiation e-tech {radiation.etech_drop_chance.value}%, radiation legendary {radiation.legendary_drop_chance.value}%, "
             f"HeartShatter {heartshatter.drop_chance.value}%"]
    for label, pool, share in POOLS:
        try:
            lines += _section(label, _draw(pool, int(total * share)))
        except Exception:  # noqa: BLE001
            lines.append(f"== {label}: failed ({traceback.format_exc().strip().splitlines()[-1]}) ==")
    lines.append(f"(took {time.monotonic() - started:.1f} s)")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        log("loot check: " + line)
    return lines


def press(_button=None) -> None:
    try:
        lines = run()
        world = next((i for i, line in enumerate(lines) if line.startswith("== World")), 0)
        top = next((line for line in lines[world:] if line.startswith("rolled by this mod")), "").replace("rolled by this mod in these draws: ", "")
        show_hud_message("Loot check done", f"Report: {REPORT.name} (also in the log). World drops, rolled by this mod: {top}", 8)
    except Exception as ex:  # noqa: BLE001
        log("loot check failed: " + traceback.format_exc()[-400:])
        show_hud_message("Loot check", f"Failed: {ex}", 4)
