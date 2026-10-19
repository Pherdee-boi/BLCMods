"""Adventure Guide UI: a sixth status-menu tab that lists bosses and their dedicated drops.

The tab reuses the Mission Log screen (summary and rewards panels on the left, list on the right). A patched status menu
movie (status_menu_patched.swf, built by bl2_guide/patch_status_swf_v2.py) adds the tab button; while the guide tab is
showing (guide_mode) the hooks below replace what the Mission Log would show. Everything else stays untouched.
Data comes from guide_data.json. Item names/types come from item_catalog.json or from real items that are generated
(never as pickups) the first time they are needed.
Reloaded by the dev trigger (exec.now.py), so keep all state in this module.
"""
import json
import re
import time
import traceback
from pathlib import Path

import unrealsdk
from mods_base import SETTINGS_DIR, get_pc, open_in_mod_dir
from unrealsdk.hooks import Block, Type, add_hook, remove_hook

HERE = Path(__file__).parent


def _read(name: str) -> bytes:
    """A file shipped with the mod (works inside a .sdkmod too)."""
    with open_in_mod_dir(HERE / name, True) as f:
        return f.read()


SM = "WillowGame.StatusMenuExGFxMovie:"
HOOKS = (
    (SM + "UpdateMissionTextList", Type.POST, "ag_fill"),
    (SM + "SetTooltipsText", Type.POST, "ag_tips_post"),
    (SM + "HitEnterOnLog", Type.PRE, "ag_enter"),
    (SM + "extLogListTrack", Type.PRE, "ag_track"),
    (SM + "OnClose", Type.PRE, "ag_close"),
    (SM + "Start", Type.PRE, "ag_start"),
    (SM + "SetActiveMission", Type.PRE, "ag_active"),
    (SM + "SetRewardsTotalCredits", Type.PRE, "ag_credits"),
    (SM + "SetRewardsTotalOtherCurrency", Type.PRE, "ag_other"),
    (SM + "ChangeMissionLogSorting", Type.PRE, "ag_sort"),
    (SM + "ToggleMissionFiltered", Type.PRE, "ag_ignore"),
    (SM + "extGenericButtonClicked", Type.PRE, "ag_nav"),
    (SM + "TrySwitchToScreen", Type.PRE, "ag_screen"),
    (SM + "GotoNextTab", Type.PRE, "ag_next"),
    (SM + "GotoPrevTab", Type.PRE, "ag_prev"),
    (SM + "HandleInputKey", Type.PRE, "ag_key"),
    ("WillowGame.WillowGameViewportClient:Tick", Type.PRE, "ag_tick"),
)
TEMPLATE_POOL = "GD_Itempools.WeaponPools.Pool_Weapons_SMG_04_Rare"
TEMPLATE_MISSION = "GD_Episode05.M_Ep5_ThePhoenix"
MISSION_SCREEN = 1  # StatusMenuExGFxMovie.CurrentScreen of the Mission Log, which the guide tab shares
FIRST_SCREEN = 1
CHALLENGES_SCREEN = 5
NB = chr(160)
TIPS_BROWSE = f"[Q]{NB}Sort     [R]{NB}Search     [Enter]{NB}Expand / Inspect     [Escape]{NB}Close     "
TIPS_RESULTS = f"[R]{NB}Search     [Enter]{NB}Inspect     [Escape]{NB}Clear search     "
TIPS_TYPING = f"Type to search     [Backspace]{NB}Delete     [Enter]{NB}Done     [Escape]{NB}Clear     "
SORT_MODES = ("Map", "Boss Name", "Source Type", "Item Type", "Rarity")
SEARCH_KEYS = {"SpaceBar": " ", "Apostrophe": "'", "Minus": "-", "Hyphen": "-", "Underscore": "-", "Period": ".", "Comma": ",", "Semicolon": "", "Slash": ""}
DIGITS = ("Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine")
SEARCH_OPEN = ("R",)  # starts a search: a common key the status menu does not use (it uses Q, E, F, Enter, Escape, +/-, Page keys)
MAX_QUERY = 32
SECTIONS = (("dedicated", "DEDICATED DROPS"), ("generic", "GENERIC DROPS"))
INDENT = "   "  # text indent of drop rows under their boss (their icon frames are indented too: "i_<icon>")
ICON_DLC = "new"
ICON_ZONE = "n1_inprogress"  # a map row's icon, one level to the right of the DLC row's (swf_patch.NATIVE_INDENTED)
ITEM_PREFIX = {1: "i_", 3: "i3_"}  # item icon frames by level (swf_patch.ITEM_INDENTS)
EXTRA_ZONES = ("Special Enemies", "Mission Rewards", "Vendors", "Other Sources", "World Drops", "Chests & Machines")  # zones that hold non-boss sources (see bl2_guide/build_guide_v2.py)
SOURCE_ORDER = ("Boss", "Special Enemies", "Mission Rewards", "Vendors", "Other Sources", "World Drops", "Chests & Machines")  # "Source Type" sorting
TYPE_LABEL = {"pistol": "Pistols", "shotgun": "Shotguns", "smg": "Sub-Machine Guns", "ar": "Assault Rifles", "sniper": "Sniper Rifles",
              "rocket": "Launchers", "shield": "Shields", "grenade": "Grenade Mods", "comm": "Class Mods", "artifact": "Relics"}
RARITY_LABEL = {10: "Rainbow", 8: "Seraph", 7: "Pearlescent", 5: "Legendary", 6: "E-tech", 9: "Gemstone", 4: "Epic", 3: "Rare", 2: "Uncommon", 1: "Common"}
DLC_COLOR = "#FFD24D"
ZONE_COLOR = "#99F0FF"
LEGENDARY = "#FF9900"
PLAIN = "#FFFFFF"
# BL2's own RarityLevelColors (GD_Globals) per rarity class; the class of an item's rarity level is rarity_class()
RARITY_TEXT = {2: "#3DD20B", 3: "#3C8EFF", 4: "#A83FE5", 5: LEGENDARY, 6: "#CA00A8", 7: "#00FFFF", 8: "#FF9AB8", 9: "#9132C8", 10: "#F2FFA1"}
RAINBOW = ("#FF4D4D", "#FF9A3D", "#FFE23D", "#5BE05B", "#3DC8FF", "#8F6BFF", "#E65BFF")  # the letters of the word "Rainbow" in the rarity headings
REWARD_RARITY = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 4, 7: 6, 8: 6, 9: 4, 10: 7}  # rarity class -> EItemRarity for the reward panel's icon tint
SHORT_DLC = {"Ultimate Vault Hunter Upgrade Pack 2": "UVHM Upgrade Pack 2"}
ICON_BOSS = "inprogressMission"  # list icon of a boss row (frames of the Mission Log's type icon clip)
HEADER_BOSS = "inprogressMission"  # frame of the summary box's title icon
TYPE_BY_CLASS = {"WillowArtifact": "Relic", "WillowClassMod": "Class Mod", "WillowGrenadeMod": "Grenade Mod", "WillowShield": "Shield"}
ICON_BY_CLASS = {"WillowArtifact": "artifact", "WillowClassMod": "comm", "WillowGrenadeMod": "grenade", "WillowShield": "shield"}
ICON_BY_WEAPON = (("Pistol", "pistol"), ("Shotgun", "shotgun"), ("Sub-Machine", "smg"), ("Assault", "ar"), ("Sniper", "sniper"), ("Launcher", "rocket"))
PRESENTATION = {  # item icon frame -> the game's reward presentation definition (draws the type icon, tinted by rarity)
    "pistol": "GD_InventoryPresentations.Definitions.Pistol",
    "shotgun": "GD_InventoryPresentations.Definitions.Shotgun",
    "smg": "GD_InventoryPresentations.Definitions.SMG",
    "ar": "GD_InventoryPresentations.Definitions.AssaultRifle",
    "sniper": "GD_InventoryPresentations.Definitions.SniperRifle",
    "rocket": "GD_InventoryPresentations.Definitions.RocketLauncher",
    "shield": "GD_InventoryPresentations.Definitions.Shield",
    "grenade": "GD_InventoryPresentations.Definitions.GrenadeMod",
    "comm": "GD_InventoryPresentations.Definitions.PersonalItem",
    "artifact": "GD_Artifacts.A_Item.InvPresentation_Artifact",
}
CATALOG_VERSION = 5
RP = "missions.rewardPanel"
MS = "missions.missionSummary"

GUIDE: list = []  # [(dlc, [(zone, [boss record])])]
rows: dict[int, tuple] = {}  # text-entry index -> (kind, boss record, drop); kind is "boss" or "drop"
expanded: set[str] = set()  # boss keys whose drop rows are open
collapsed: set[tuple] = set()  # (section, DLC name) whose maps are folded away (map sorting)
open_zones: set[tuple] = set()  # (section, DLC name, map) whose bosses are shown (map sorting); a map starts closed, like a DLC starts open
sort_mode = 0  # index into SORT_MODES
query = ""  # search text; while it is not empty the list shows the matching drops instead of the sort mode's view
typing = False  # True while keys are going into the search text
_eaten: set[str] = set()  # keys whose press the search used: their release is swallowed too
found = 0  # number of drops the current search matches
_hay: dict[str, str] = {}
LEVELS: dict[str, list[int]] = {}  # drop path -> [lowest, highest] game stage it can be generated at; only drops with a real floor (item_levels.json)  # drop path -> lower-case text the search matches against
sources: dict[str, list[dict]] = {}  # drop path -> the bosses that drop it
_info: dict[str, dict] = {}  # balance path -> {title, type, mfr, color, icon, rarity}
_stats: dict[str, str] = {}  # balance path -> special-effects text (level-dependent, so never persisted)
_counter = 0
_dirty = False
_last_virtual = None
log = print
TAB_MODE = False  # True once the patched status menu (with the sixth "guide" tab) is active
guide_mode = False  # True while the guide tab is the one on screen
_entering = False
_pending: list = []  # [(due time, callable)]


def load_data() -> None:
    GUIDE.clear()
    for g in json.loads(_read("guide_data.json")):
        dlc = SHORT_DLC.get(g["dlc"], g["dlc"])
        if not GUIDE or GUIDE[-1][0] != dlc:
            GUIDE.append((dlc, []))
        for b in g["bosses"]:
            b["dlc"], b["zone"], b["group"] = dlc, g["zone"], g.get("group", "dedicated")
        GUIDE[-1][1].append((g["zone"], g["bosses"]))
    sources.clear()
    _hay.clear()
    LEVELS.clear()
    try:
        LEVELS.update(json.loads(_read("item_levels.json")))
    except Exception:  # noqa: BLE001
        pass
    for _, zones in GUIDE:
        for _, bosses in zones:
            for b in bosses:
                for d in b.get("drops", []):
                    sources.setdefault(d["path"], []).append(b)


def rarity_class(level: int) -> int:
    """1 common .. 4 epic, 5 legendary, 6 e-tech, 7 pearlescent, 8 seraph, 9 gemstone, 10 rainbow (BL2's RarityLevelColors table)."""
    if 1 <= level <= 4:
        return level
    if level == 5 or 7 <= level <= 10:
        return 5
    return {6: 6, 500: 7, 501: 8, 503: 9, 506: 10}.get(level, 1)


def rarity_title(cls: int, color: str) -> str:
    """The rarity's name in its colour; Rainbow has a colour per letter."""
    name = RARITY_LABEL[cls]
    if cls == 10:
        return "".join(f"<font color='{RAINBOW[i % len(RAINBOW)]}'>{ch}</font>" for i, ch in enumerate(name))
    return f"<font color='{color}'>{name}</font>"


def _boss_key(rec: dict) -> str:
    return f"{rec['dlc']}|{rec['zone']}|{rec['name']}"


def _color(path: str) -> str:
    return LEGENDARY if ("Legendary" in path or "Unique" in path or "Pearl" in path) else PLAIN


# ---- item generation (pickup-free) -------------------------------------------------------------------------------------------


def _stage_for(bal, level: int) -> int:
    """The game stage to generate at: the player's level, moved into the balance's grade range when it has none for that level
    (the Seraph relic only exists from stage 50, so at level 30 the generator returns nothing)."""
    try:
        spans = [(int(g.GameStageRequirement.MinGameStage), int(g.GameStageRequirement.MaxGameStage)) for m in bal.Manufacturers for g in m.Grades]
    except Exception:  # noqa: BLE001
        return level
    if not spans or any(lo <= level <= hi for lo, hi in spans):
        return level
    lowest = min(lo for lo, _ in spans)
    return lowest if level < lowest else max(hi for _, hi in spans)


def _generate_definition(drop: dict):
    """Ask the game's own mission-reward generator for this drop at the player's level: no drop, no pickup.
    Returns (is_weapon, definition data)."""
    global _counter
    _counter += 1
    pc = get_pc()
    pawn = pc.Pawn
    src = drop.get("gen") or drop["path"]  # "gen": balance to generate from when the entry is not a balance itself (named class mods)
    try:
        bal = unrealsdk.find_object(drop["cls"], src)
    except ValueError:  # its (DLC) package is not loaded yet
        pkg = _item_packages().get(src)
        if pkg:
            unrealsdk.load_package(pkg)
        bal = unrealsdk.find_object(drop["cls"], src)
    template = unrealsdk.find_object("ItemPoolDefinition", TEMPLATE_POOL)
    pool = unrealsdk.construct_object("ItemPoolDefinition", template.Outer, f"AG_Pool_{_counter}", template_obj=template)
    pool.BalancedItems = [template.BalancedItems[0]]
    pool.BalancedItems[0].InvBalanceDefinition = bal
    pool.BalancedItems[0].Probability.BaseValueConstant = 1.0
    base = unrealsdk.find_object("MissionDefinition", TEMPLATE_MISSION)
    mission = unrealsdk.construct_object("MissionDefinition", base.Outer, f"AG_Mission_{_counter}", template_obj=base)
    mission.GameStage = _stage_for(bal, int(pawn.GetExpLevel()))
    mission.Reward.RewardItemPools = [pool]
    out = mission.GetItemRewardsForPlayer(pc, unrealsdk.make_struct("PendingMissionRewardData"))[1]
    for x in out.WeaponRewards:
        if x.BalanceDefinition is not None:
            return True, x
    for x in out.ItemRewards:
        if x.BalanceDefinition is not None:
            return False, x
    raise RuntimeError(f"generator returned nothing for {drop['path']}")


_pkgs: dict | None = None


def _item_packages() -> dict:
    global _pkgs
    if _pkgs is None:
        try:
            _pkgs = json.loads(_read("item_packages.json"))
        except Exception:  # noqa: BLE001
            _pkgs = {}
    return _pkgs


def make_item(drop: dict):
    """A real, player-owned item for this drop (never a pickup). The caller destroys it when finished."""
    pawn = get_pc().Pawn
    is_weapon, dd = _generate_definition(drop)
    if is_weapon:
        return unrealsdk.find_class("WillowWeapon").ClassDefaultObject.CreateWeaponFromDef(dd, pawn, False)
    return unrealsdk.find_class("WillowItem").ClassDefaultObject.CreateItemFromDef(dd, pawn, 1, False)


def _destroy(obj) -> None:
    try:
        if obj is not None:
            obj.Destroy()
    except Exception:  # noqa: BLE001
        pass


def make_virtual_item(drop: dict):
    """The item shown in the inspect view; the previous one is destroyed here (its viewer has been closed by now)."""
    global _last_virtual
    _destroy(_last_virtual)
    _last_virtual = None
    _last_virtual = make_item(drop)
    return _last_virtual


def inspect_drop(drop: dict) -> None:
    """Open the game's own item inspection (3D model + item card) for a generated copy of this drop.
    Start and InspectItem must happen in the same call, and nothing may touch the movie afterwards."""
    pc = get_pc()
    item = make_virtual_item(drop)
    d = unrealsdk.find_object("ItemInspectionGFxMovieDefinition", "UI_ItemInspect.ItemInspectDefinition")
    movie = d.SpawnPlayerMovie(pc, None, None)
    movie.Start(False)
    movie.InspectItem(item, -1, 0)


# ---- item names / types (catalog) ---------------------------------------------------------------------------------------------

CATALOG = HERE / "item_catalog.json" if HERE.is_dir() else SETTINGS_DIR / "adventure_guide_catalog.json"  # (inside an archive the cache cannot be written beside the code)


def load_catalog() -> None:
    for read in (CATALOG.read_bytes, lambda: _read("item_catalog.json")):  # the cache first, then the one that shipped
        try:
            data = json.loads(read())
            if data.get("_v") == CATALOG_VERSION:
                _info.update({k: v for k, v in data.items() if k != "_v"})
                for v in _info.values():
                    if "rarity" in v:
                        v["color"] = RARITY_TEXT.get(rarity_class(int(v["rarity"])), PLAIN)
                return
        except Exception:  # noqa: BLE001
            pass


def save_catalog() -> None:
    global _dirty
    if _dirty:
        data = {"_v": CATALOG_VERSION, **{k: v for k, v in _info.items() if v.get("ok")}}
        CATALOG.write_text(json.dumps(data, indent=0), encoding="utf-8")
        _dirty = False


def _part_name(dd, field: str) -> str | None:
    try:
        part = getattr(dd, field)
        return str(part.PartName) if part is not None and str(part.PartName) else None
    except Exception:  # noqa: BLE001
        return None


def _icon_for(cls_name: str, kind: str) -> str:
    if cls_name in ICON_BY_CLASS:
        return ICON_BY_CLASS[cls_name]
    return next((icon for key, icon in ICON_BY_WEAPON if key in kind), "")


def _read_item(inv, path: str) -> dict:
    info = {"title": path.split(".")[-1], "type": "", "mfr": "", "color": _color(path), "icon": "", "rarity": 0, "ok": True}
    dd = inv.DefinitionData
    cls_name = inv.Class.Name
    title = _part_name(dd, "TitlePartDefinition") or _part_name(dd, "TitleItemNamePartDefinition")
    kind = ""
    try:
        kind = str(dd.WeaponTypeDefinition.TypeName)
    except Exception:  # noqa: BLE001
        try:
            kind = str(dd.ItemDefinition.ItemName)
        except Exception:  # noqa: BLE001
            kind = ""
    if cls_name in ("WillowArtifact", "WillowClassMod"):
        kind = TYPE_BY_CLASS[cls_name]  # the definition's own name is the item's name, not its kind
    for pfield in ("PrefixPartDefinition", "PrefixItemNamePartDefinition"):
        try:  # a fixed legendary/unique prefix is part of the real name ("Sledge's Shotgun"); random prefixes are not
            part = getattr(dd, pfield)
            if part is not None and title and re.search(r"Legendary|Unique|Pearl|Seraph", part._path_name().split(".")[-1]):
                title = f"{part.PartName} {title}"
        except Exception:  # noqa: BLE001
            pass
    if not title:  # no separate title part: the card name (prefix + title) is the best name we have
        try:
            title = str(inv.GetInventoryCardString(False, False, True)).strip() or None
        except Exception:  # noqa: BLE001
            title = None
    if not title:
        title, kind = kind or info["title"], ""
    info["title"], info["type"] = title, kind or TYPE_BY_CLASS.get(cls_name, "")
    info["icon"] = _icon_for(cls_name, kind)
    try:
        card = str(inv.GetInventoryCardString(True, False, False))
        info["mfr"] = card.split(" INVALID")[0].strip() if " INVALID" in card else ""  # artifacts have no manufacturer line
    except Exception:  # noqa: BLE001
        pass
    try:
        info["rarity"] = int(inv.GetRarityLevel())
        info["color"] = RARITY_TEXT.get(rarity_class(info["rarity"]), PLAIN)
    except Exception:  # noqa: BLE001
        pass
    return info


def _stats_text(inv) -> str:
    try:
        return str(inv.GenerateFunStatsText()).replace(chr(13), "").replace(chr(10), "<br>")
    except Exception:  # noqa: BLE001
        return ""


def item_info(drop: dict) -> dict:
    """Name, type, manufacturer, icon and colour of a drop: from the catalog, generated once if it is missing."""
    global _dirty
    path = drop["path"]
    if path in _info:
        return _info[path]
    info = {"title": path.split(".")[-1], "type": "", "mfr": "", "color": _color(path), "icon": "", "rarity": 0, "ok": False}
    inv = None
    try:
        inv = make_item(drop)
        info = _read_item(inv, path)
        _stats[path] = _stats_text(inv)
        _dirty = True
    except Exception:
        log(f"item_info {path}: " + traceback.format_exc()[-200:])
    finally:
        _destroy(inv)
    _info[path] = info
    return info


def item_stats(drop: dict) -> str:
    """The item's special-effects text at the player's level (generated on first hover)."""
    path = drop["path"]
    if path not in _stats:
        inv = None
        try:
            inv = make_item(drop)
            _stats[path] = _stats_text(inv)
        except Exception:
            log(f"item_stats {path}: " + traceback.format_exc()[-200:])
            _stats[path] = ""
        finally:
            _destroy(inv)
    return _stats[path]


# ---- the list ------------------------------------------------------------------------------------------------------------------


def _movie():
    ms = [m for m in unrealsdk.find_all("StatusMenuExGFxMovie", exact=False) if "Default__" not in m.Name]
    return ms[-1] if ms else None


def _rgb(hexcolor: str):
    h = hexcolor.lstrip("#")
    return unrealsdk.make_struct("Color", R=int(h[0:2], 16), G=int(h[2:4], 16), B=int(h[4:6], 16), A=255)


def _anchor_mission():
    """Any real mission: the list wants a MissionDefinition per entry, the guide only uses its own row table."""
    return unrealsdk.find_object("MissionDefinition", TEMPLATE_MISSION)


def _row_key(row: tuple):
    kind, rec, drop = row
    if kind in ("dlc", "zone", "none"):
        return (kind, rec, None)
    return (kind, _boss_key(rec), drop["path"] if drop else None)


DEBUG_STEPS = False


def _step(msg: str) -> None:
    if DEBUG_STEPS:
        log("step: " + msg)


def title_text() -> str:
    if typing or query:
        return f'ADVENTURE GUIDE <font size="22">({found} found)</font>' if query else 'ADVENTURE GUIDE <font size="22">(Search)</font>'
    return f'ADVENTURE GUIDE <font size="22">({SORT_MODES[sort_mode]})</font>'


def tips_text() -> str:
    return TIPS_TYPING if typing else TIPS_RESULTS if query else TIPS_BROWSE


def _all_bosses() -> list[dict]:
    """Every source record, dedicated sources first (so a drop is attributed to its dedicated source before a generic pool)."""
    out = [b for _, zones in GUIDE for _, bosses in zones for b in bosses]
    return sorted(out, key=lambda b: b["group"] != "dedicated")


def _is_boss(rec: dict) -> bool:
    """True for a named boss of a real map (not enemy types, mission rewards, vendors, pools)."""
    return rec["zone"] not in EXTRA_ZONES


def _all_drops() -> list[tuple[dict, dict]]:
    """(boss, drop) for every distinct drop, first boss wins."""
    seen, out = set(), []
    for b in _all_bosses():
        for d in b.get("drops", []):
            if d["path"] not in seen:
                seen.add(d["path"])
                out.append((b, d))
    return out


def _haystack(drop: dict) -> str:
    h = _hay.get(drop["path"])
    if h is None:
        i = item_info(drop)
        parts = [i["title"], i["type"], i["mfr"], RARITY_LABEL.get(rarity_class(int(i.get("rarity", 0))), "")]
        for b in sources.get(drop["path"], []):
            parts += [b["name"], b["zone"], b["dlc"]]
        h = _hay[drop["path"]] = re.sub(r"<[^>]*>", "", " ".join(parts)).lower()
    return h


def search_results() -> list[tuple[dict, dict]]:
    tokens = query.lower().split()
    return [(b, d) for b, d in _all_drops() if all(t in _haystack(d) for t in tokens)]


def fill(m, select: tuple | None = None) -> None:
    """Rebuild the list for the current sort mode (see SORT_MODES)."""
    global found
    tl = m.MissionLogTextList
    anchor = _anchor_mission()
    prev_top = int(tl.IndexOfTopEntry)
    tl.EmptyData()
    cats: list[str] = [""]  # category 0 is a placeholder for rows that precede every header
    te: list = []
    rows.clear()
    first = select_idx = None

    def header(label: str) -> int:
        cats.append(label)
        te.append(unrealsdk.make_struct("GFxTextEntry", ArrayIdx=len(cats) - 1, Kind=1))
        return len(cats) - 1

    def add(ci: int, text: str, color: str, icon: str, row: tuple) -> int:
        nonlocal first, select_idx
        tl.AddDataEntry(anchor, text, _rgb(color), icon)
        n = len(tl.SortableArray) - 1
        tl.SortableArray[n].CategoryIdx = ci
        idx = len(te)
        rows[idx] = row
        te.append(unrealsdk.make_struct("GFxTextEntry", ArrayIdx=n, Kind=0))
        first = idx if first is None else first
        if select == _row_key(row):
            select_idx = idx
        return idx

    def boss_rows(ci: int, rec: dict, label: str | None = None, level: int = 0) -> None:
        """A boss row at `level` steps from the left edge (text and icon both moved right), and its drops one step further when it is open."""
        add(ci, INDENT * level + (label or rec["name"]), PLAIN, ICON_BOSS if level == 0 else f"n{level}_{ICON_BOSS}", ("boss", rec, None))
        if _boss_key(rec) in expanded:
            for d in rec.get("drops", []):
                info = item_info(d)
                add(ci, INDENT * (level + 1) + info["title"], info["color"], ITEM_PREFIX[level + 1] + (_icon_frame(info) or "none"), ("drop", rec, d))

    def drop_row(ci: int, boss: dict, d: dict) -> None:
        info = item_info(d)
        add(ci, info["title"], info["color"], _icon_frame(info) or "none", ("drop", boss, d))

    def rarity_groups(pairs: list) -> None:
        groups: dict[int, list] = {}
        for boss, d in pairs:
            groups.setdefault(rarity_class(int(item_info(d).get("rarity", 0))), []).append((boss, d))
        for cls in RARITY_LABEL:
            if cls in groups:
                color = RARITY_TEXT.get(cls, PLAIN)
                ci = header(rarity_title(cls, color))
                for boss, d in sorted(groups[cls], key=lambda x: item_info(x[1])["title"].lower()):
                    drop_row(ci, boss, d)

    mode = SORT_MODES[sort_mode]
    if query:
        results = search_results()
        found = len(results)
        if results:
            rarity_groups(results)
        else:
            add(0, "No item matches", "#BBBBBB", "none", ("none", "", None))
    elif mode == "Map":
        for group, label in SECTIONS:
            ci = None
            for dlc, zones in GUIDE:
                zs = [(z, bs) for z, bs in zones if bs and bs[0]["group"] == group]
                if not zs:
                    continue
                if ci is None:
                    ci = header(label)
                folded = (group, dlc) in collapsed
                add(ci, f"{'+' if folded else '-'} {dlc.upper()}", DLC_COLOR, ICON_DLC, ("dlc", (group, dlc), None))
                if folded:
                    continue
                for zone, bosses in zs:
                    zkey = (group, dlc, zone)
                    shown = zkey in open_zones
                    add(ci, f"{INDENT}{'-' if shown else '+'} {zone}", ZONE_COLOR, ICON_ZONE, ("zone", zkey, None))
                    if shown:
                        for rec in bosses:
                            boss_rows(ci, rec, level=2)
    elif mode == "Boss Name":
        letter, ci = None, 0
        for rec in sorted((b for b in _all_bosses() if _is_boss(b)), key=lambda b: b["name"].lower().removeprefix("the ")):
            ch = rec["name"].lower().removeprefix("the ")[:1].upper()
            if ch != letter:
                letter, ci = ch, header(ch)
            boss_rows(ci, rec)
    elif mode == "Source Type":
        kinds: dict[str, list] = {}
        for rec in _all_bosses():
            kinds.setdefault("Boss" if _is_boss(rec) else rec["zone"], []).append(rec)
        for kind in SOURCE_ORDER:
            recs = [r for r in kinds.get(kind, []) if r.get("drops")]
            if not recs:
                continue
            ci = header("Bosses" if kind == "Boss" else kind)
            names = [r["name"] for r in recs]
            for rec in sorted(recs, key=lambda r: r["name"].lower().removeprefix("the ")):
                label = rec["name"] if names.count(rec["name"]) == 1 else f"{rec['name']} ({rec['dlc']})"
                boss_rows(ci, rec, label)
    elif mode == "Item Type":
        groups: dict[str, list] = {}
        for boss, d in _all_drops():
            groups.setdefault(item_info(d).get("icon") or "", []).append((boss, d))
        for icon in list(TYPE_LABEL) + [""]:
            if icon in groups:
                ci = header(TYPE_LABEL.get(icon, "Other"))
                for boss, d in sorted(groups[icon], key=lambda x: item_info(x[1])["title"].lower()):
                    drop_row(ci, boss, d)
    else:  # Rarity
        rarity_groups(_all_drops())

    tl.CategoryLabelsArray = cats
    m.MissionCategories = cats
    tl.TextEntries = te
    target = select_idx if select_idx is not None else first
    if target is not None:  # the old highlight may point at a header row of the new list, which crashes the update
        per = max(1, int(tl.NumberEntriesPerPage))
        top = prev_top if select is not None else 0  # a toggle keeps the scroll position unless the open drops would not fit
        extent = target
        if select is not None and rows[target][0] == "boss" and _boss_key(rows[target][1]) in expanded:
            extent += len(rows[target][1].get("drops", []))
        elif select is not None and rows[target][0] == "zone" and rows[target][1] in open_zones:
            extent += len(_zone_bosses(rows[target][1]))
        top = min(top, target)
        if extent >= top + per:
            top = max(0, min(target - 1, extent - per + 1))
        tl.HighlightedEntry = target
        tl.IndexOfTopEntry = max(0, min(top, len(te) - per))
    tl.UpdateTextEntries()
    try:
        tl.PositionHighlightBar()  # the bar is not moved by the update itself
    except Exception:  # noqa: BLE001
        log("PositionHighlightBar failed: " + traceback.format_exc()[-200:])
    save_catalog()


def _icon_frame(info: dict) -> str | None:
    """Frame label of the patched movie's item icons (see bl2_guide/patch_status_swf_v2.py): "<type>" or "<type>_<rarity>"."""
    icon = info.get("icon")
    if not icon:
        return None
    cls = rarity_class(int(info.get("rarity", 0)))
    return f"{icon}_{cls}" if cls >= 2 else icon


# ---- the summary / rewards panels ---------------------------------------------------------------------------------------------


def _set_text(m, path: str, value: str) -> None:
    m.SetVariableString(path, value)


def _presentation(info: dict):
    """The reward-panel icon (item type, tinted by rarity) for a drop, or None."""
    path = PRESENTATION.get(info.get("icon", ""))
    if path is None:
        return None
    return unrealsdk.make_struct(
        "MissionRewardPresentationData",
        PresentationDefinition=unrealsdk.find_object("InventoryCardPresentationDefinition", path),
        Rarity=REWARD_RARITY[rarity_class(int(info.get("rarity", 0)))],
    )


def level_text(lv) -> str:
    """The level a drop starts spawning at: "DROPS FROM LEVEL 50" (or "DROPS AT LEVEL 12-40" when it also stops); empty when nothing
    restricts it (item_levels.json lists only the restricted drops: the generator's own floor of 7, which comes from the reward mission
    and not from the item, is left out)."""
    if not lv:
        return ""
    lo, hi = lv
    return f"DROPS FROM LEVEL {lo}" if hi >= 100 else f"DROPS AT LEVEL {lo}-{hi}"


def _zone_bosses(zkey: tuple) -> list[dict]:
    """The sources of one map (or of one of the extra zones) in one section of one DLC."""
    group, dlc, zone = zkey
    return next((bs for d, zs in GUIDE if d == dlc for z, bs in zs if z == zone and bs and bs[0]["group"] == group), [])


def _details(row: tuple) -> dict:
    kind, rec, drop = row
    if kind == "none":
        return {"title": "No match", "frame": "new", "body": "Nothing in the guide matches the search text.<br><br><font color='#BBBBBB'>Try a boss, a map, an item type or part of a name.</font>",
                "level": "0 ITEMS", "diff": "", "xp": "", "head": "SEARCH", "tag": "", "icons": []}
    if kind == "zone":
        group, dlc, zone = rec
        bosses = _zone_bosses(rec)
        lines = [f"• {b['name']} <font color='{ZONE_COLOR}'>{len(b.get('drops', []))} drop{'s' if len(b.get('drops', [])) != 1 else ''}</font>" for b in bosses]
        drops = len({d["path"] for b in bosses for d in b.get("drops", [])})
        extra = zone in EXTRA_ZONES
        what = ("SOURCE", "SOURCES") if extra else ("BOSS", "BOSSES")
        return {"title": zone, "frame": HEADER_BOSS, "body": "<br>".join(lines) + "<br><br><font color='#BBBBBB'>Enter opens / closes this map.</font>",
                "level": f"{len(bosses)} {what[len(bosses) != 1]}, {drops} DROP{'S' if drops != 1 else ''}", "diff": "", "xp": "",
                "head": "SOURCES" if extra else "MAP", "tag": dlc.upper(), "icons": []}
    if kind == "dlc":
        group, dlc = rec
        zones = [(z, bs) for z, bs in next(z for d, z in GUIDE if d == dlc) if bs and bs[0]["group"] == group]
        maps = [(z, bs) for z, bs in zones if z not in EXTRA_ZONES]
        extra = [(z, bs) for z, bs in zones if z in EXTRA_ZONES]
        lines = [f"• {zone} <font color='{ZONE_COLOR}'>{len(bs)} boss{'es' if len(bs) != 1 else ''}</font>" for zone, bs in maps]
        if extra:
            lines.append(("<br>" if maps else "") + "<font color='#BBBBBB'>Sources:</font> " + ", ".join(z for z, _ in extra))
        n = sum(len(bs) for _, bs in maps)
        drops = len({d["path"] for _, bs in zones for b in bs for d in b["drops"]})
        level = f"{len(maps)} MAPS, {n} BOSSES, {drops} DROPS" if maps else f"{drops} DROPS"
        return {"title": dlc, "frame": "new", "body": "<br>".join(lines) + "<br><br><font color='#BBBBBB'>Enter folds / unfolds this section.</font>",
                "level": level, "diff": "", "xp": "", "head": "GENERIC DROPS" if group == "generic" else "DLC", "tag": "", "icons": []}
    if kind == "drop":
        i = item_info(drop)
        head = " - ".join(x for x in (i["type"], i["mfr"]) if x)
        bosses = sources.get(drop["path"], [rec])
        where = "<br>".join(
            f"{b['name']} <font color='{ZONE_COLOR}'>{b['zone'] if b['zone'] == b['dlc'] else b['zone'] + ' (' + b['dlc'] + ')'}</font>" for b in bosses
        )
        body = f"<font color='{ZONE_COLOR}'>{head}</font><br><font color='#BBBBBB'>Dropped by</font><br>{where}<br><br>{item_stats(drop)}"
        lv = LEVELS.get(drop["path"])
        return {"title": i["title"], "frame": _icon_frame(i) or "new", "body": body, "level": i["type"].upper(), "diff": level_text(lv), "xp": "",
                "head": "ITEM", "tag": rec["dlc"].upper(), "icons": [i]}
    drops = rec.get("drops", [])
    infos = [item_info(d) for d in drops]
    lines = []
    for i in infos:
        lines.append(f"<font color='{i['color']}'>\u2022 {i['title']}</font> <font color='{ZONE_COLOR}'>{i['type']}</font>")
    where = rec["zone"] if rec["zone"] == rec["dlc"] else f"{rec['zone']}<br>{rec['dlc']}"
    body = f"<font color='{ZONE_COLOR}'>{where}</font><br><br>" + ("<br>".join(lines) or "No dedicated drops listed")
    best = sorted(infos, key=lambda i: -rarity_class(i.get("rarity", 0)))
    n = len(drops)
    pseudo = rec["zone"] in EXTRA_ZONES and rec["zone"] != "Special Enemies"  # "World Drops", "Vendors", ...: a source, not a boss
    return {"title": rec["name"], "frame": HEADER_BOSS, "body": body,
            "level": f"{n} ITEM{'S' if n != 1 else ''}" if pseudo else f"{n} DEDICATED DROP{'S' if n != 1 else ''}", "diff": "",
            "xp": "", "head": "LOOT" if pseudo else "BOSS LOOT", "tag": rec["dlc"].upper(), "icons": best[:2]}


def apply_details(m) -> None:
    """Fill the summary box and the rewards panel for the highlighted row."""
    tl = m.MissionLogTextList
    row = rows.get(int(tl.HighlightedEntry))
    if row is None:
        return
    d = _details(row)
    try:
        m.GetVariableObject(MS + ".header").GotoAndStop(d["frame"])
        m.GetVariableObject(RP + ".OptionalRequired").GotoAndStop("optional")  # not the "story" badge of the placeholder mission
        for clip in ("rewards_credits", "reward_other"):
            m.GetVariableObject(f"{RP}.{clip}").SetBool("_visible", False)
    except Exception:
        log("details frames failed: " + traceback.format_exc()[-200:])
    _set_text(m, MS + ".header.description.text", d["title"])
    _set_text(m, MS + ".description.htmlText", d["body"])
    _set_text(m, MS + ".difficultybar.level.text", d["level"])
    _set_text(m, MS + ".difficultybar.difficulty.text", d["diff"])
    _set_text(m, RP + ".rewards.text", d["head"])
    _set_text(m, RP + ".rewards_xp.text", d["xp"])
    _set_text(m, RP + ".OptionalRequired.textField.htmlText", d["tag"])
    _set_reward_icons(m, d["icons"])
    try:
        m.ActionScriptVoid("SetScrollArrows")
    except Exception:  # noqa: BLE001
        pass


def _set_reward_icons(m, infos: list) -> None:
    pres = [p for p in (_presentation(i) for i in infos) if p is not None]
    rw = m.GetVariableObject(RP + ".reward_weap")
    rw.SetBool("_visible", bool(pres))
    if not pres:
        return
    want = 1 if len(pres) == 1 else 2  # frames "oneReward" / "twoRewards"; going to the current frame again would recreate the icons
    if int(float(m.GetVariableString(RP + ".reward_weap._currentframe") or 0)) != want:
        rw.GotoAndStop("oneReward" if want == 1 else "twoRewards")
    names = ["innerReward"] if len(pres) == 1 else ["innerReward1", "innerReward2"]
    for name, p in zip(names, pres):
        m.SetInnerReward(m.GetVariableObject(f"{RP}.reward_weap.{name}"), p)


def _decorate(m) -> None:
    """Guide-tab look: our list heading and hint line (the player's level / XP panel stays as it is)."""
    try:
        _set_text(m, "missions.selections.missionHeaderText.htmlText", title_text())
        _set_text(m, "tooltips.tooltips.htmlText", tips_text())
    except Exception:
        log("decorate failed: " + traceback.format_exc()[-300:])


SEARCH_PANEL = "missions.searchPanel"
LEVEL_PANEL = "missions.levelPanel"
BOX_CHARS = 20  # the level box's right-hand text field holds about this much


def sync_search_box(m) -> None:
    """The search box: a copy of the Level / XP box (same plate, same fonts) that takes its place while a search is open.
    The copy is made in the running movie (MovieClip.duplicateMovieClip through GFxObject.Invoke), so it needs no SWF change."""
    show = typing or bool(query)
    lp = m.GetVariableObject(LEVEL_PANEL)
    if lp is None:
        return
    sp = m.GetVariableObject(SEARCH_PANEL)
    if sp is None:
        if not show:
            return
        name = unrealsdk.make_struct("ASValue", Type=3, S="searchPanel")  # 3 = string
        depth = unrealsdk.make_struct("ASValue", Type=2, N=4000.0)  # 2 = number
        lp.Invoke("duplicateMovieClip", [name, depth])
        sp = m.GetVariableObject(SEARCH_PANEL)
        if sp is None:
            return
        m.GetVariableObject(SEARCH_PANEL + ".xpbar").SetBool("_visible", False)
    for axis in ("_x", "_y"):
        sp.SetFloat(axis, lp.GetFloat(axis))  # a copy starts at the origin, not where the original is
    sp.SetBool("_visible", show)
    lp.SetBool("_visible", not show)
    if show:
        shown = query if len(query) <= BOX_CHARS else "…" + query[-(BOX_CHARS - 1):]
        _set_text(m, SEARCH_PANEL + ".level.text", "SEARCH")
        _set_text(m, SEARCH_PANEL + ".xp.text", shown + ("_" if typing else ""))


def _undecorate(m) -> None:
    """Undo what the guide changed that the Mission Log's own refresh does not redo."""
    global query, typing
    query, typing = "", False
    try:
        sync_search_box(m)
    except Exception:  # noqa: BLE001
        pass
    try:
        _set_text(m, RP + ".rewards.text", "$WillowMenu.missions.RewardsTitle")  # the movie's own localized "REWARDS"
        m.GetVariableObject(RP + ".reward_weap").SetBool("_visible", True)
    except Exception:  # noqa: BLE001
        pass


# ---- hooks: list / details -----------------------------------------------------------------------------------------------------


def _on_fill(obj, args, ret, func) -> None:
    """The Mission Log just rebuilt its list: put the guide's rows in it (the game's hover sequence that follows then shows
    the guide's details for the highlighted row)."""
    if not guide_mode:
        return None
    try:
        fill(obj)
        _set_header_frame(obj, "guide")
    except Exception:
        log("fill error " + traceback.format_exc()[-500:])


def _on_tips_post(obj, args, ret, func) -> None:
    """The last call of the game's own hover sequence: put our summary / rewards / hint line over what it just wrote."""
    if guide_mode and int(obj.CurrentScreen) == MISSION_SCREEN and rows:
        try:
            _set_text(obj, "tooltips.tooltips.htmlText", tips_text())
            _set_text(obj, "missions.selections.missionHeaderText.htmlText", title_text())
            sync_search_box(obj)
            apply_details(obj)
        except Exception:
            log("details error " + traceback.format_exc()[-500:])


def _on_menu_edge(obj, args, ret, func) -> None:
    """A status menu opens or closes: every session starts on a real tab."""
    global guide_mode, _entering, query, typing
    guide_mode = _entering = typing = False
    _eaten.clear()
    query = ""
    _pending.clear()


def _on_active(obj, args, ret, func) -> None:
    if guide_mode:
        args.Index = 0  # no "tracked mission" marker on any guide row


def _on_credits(obj, args, ret, func) -> None:
    if guide_mode:
        args.Credits = 0  # the placeholder mission's money reward is not ours to show


def _on_other(obj, args, ret, func) -> None:
    if guide_mode:
        args.Amount = 0


def _row_drop(row: tuple):
    """The drop to inspect for a highlighted row: the drop itself, or a boss's best (legendary) drop."""
    kind, rec, drop = row
    if kind == "drop":
        return drop
    if kind in ("dlc", "zone", "none"):
        return None
    drops = rec.get("drops", [])
    return max(drops, key=lambda d: rarity_class(item_info(d).get("rarity", 0)), default=None)  # the rarest drop


def activate(m) -> bool:
    """Enter / click on the highlighted row: fold a DLC, open or close a boss's drop list, or inspect a drop."""
    tl = m.MissionLogTextList
    row = rows.get(int(tl.HighlightedEntry))
    if row is None:
        return False
    if row[0] == "dlc":
        collapsed.symmetric_difference_update({row[1]})
        fill(m, select=_row_key(row))
        apply_details(m)
        return True
    if row[0] == "zone":
        open_zones.symmetric_difference_update({row[1]})
        fill(m, select=_row_key(row))
        apply_details(m)
        return True
    if row[0] == "none":
        return True
    if row[0] == "boss":
        if not row[1].get("drops"):
            return True
        expanded.symmetric_difference_update({_boss_key(row[1])})
        fill(m, select=_row_key(row))
        apply_details(m)
        return True
    inspect_drop(row[2])
    return True


def _on_enter(obj, args, ret, func):
    if not guide_mode:
        return None
    if typing:  # Space (and other keys the menu reads as "accept") reach here by a path the key hook cannot block
        return Block
    try:
        activate(obj)
    except Exception:
        log("enter error " + traceback.format_exc()[-500:])
    return Block


def _on_track(obj, args, ret, func):
    """The Mission Log's click action: use it as activate; never let it track a mission."""
    if not guide_mode:
        return None
    if typing:
        return Block
    try:
        activate(obj)
    except Exception:
        log("track error " + traceback.format_exc()[-500:])
    return Block


def _on_ignore(obj, args, ret, func):
    """F (ignore a mission) inspects the highlighted row's item instead."""
    if not guide_mode:
        return None
    if typing:  # the F of a search text
        return Block
    try:
        row = rows.get(int(obj.MissionLogTextList.HighlightedEntry))
        drop = _row_drop(row) if row else None
        if drop is not None:
            inspect_drop(drop)
    except Exception:
        log("inspect error " + traceback.format_exc()[-500:])
    return Block


def _on_sort(obj, args, ret, func):
    """Q (change sorting) cycles the sort mode."""
    global sort_mode, query, typing
    if not guide_mode:
        return None
    try:
        if not typing:  # (while typing, the key is part of the search text and never gets here)
            query = ""
            sort_mode = (sort_mode + 1) % len(SORT_MODES)
            refresh(obj)
    except Exception:
        log("sort error " + traceback.format_exc()[-500:])
    return Block


def refresh(m) -> None:
    """Rebuild the list and redo heading, hint line and details after the view changed."""
    fill(m)
    _set_text(m, "missions.selections.missionHeaderText.htmlText", title_text())
    _set_text(m, "tooltips.tooltips.htmlText", tips_text())
    sync_search_box(m)
    apply_details(m)


def _typed_char(key: str) -> str | None:
    """The character a key press adds to the search text, or None if it is not a text key."""
    if len(key) == 1 and key.isalpha():
        return key.lower()
    if key in DIGITS:
        return str(DIGITS.index(key))
    return SEARCH_KEYS.get(key) or None


def _on_key(obj, args, ret, func):
    """Search: [R] starts it, text keys go into the query, Backspace deletes, Enter keeps the results, Escape clears them."""
    global query, typing
    if not guide_mode or int(obj.CurrentScreen) != MISSION_SCREEN:
        return None
    try:
        key, event = str(args.ukey), int(args.uevent)
        press = event in (0, 2)  # pressed / repeated; 1 is the release
        if not press and key in _eaten:  # the press was ours, so the release must not reach the menu either
            _eaten.discard(key)
            return Block
        if _search_key(key, press, obj):
            _eaten.add(key)
            return Block
    except Exception:
        log("key error " + traceback.format_exc()[-500:])
    return None


def _search_key(key: str, press: bool, obj) -> bool:
    """Handle one key of the search interface; True if it was used (and must not reach the menu)."""
    global query, typing
    if key in SEARCH_OPEN and not typing:
        if press:
            typing = True
            refresh(obj)
        return True
    if key == "Escape" and (typing or query):
        if press:
            query, typing = "", False
            refresh(obj)
        return True
    if not typing:
        return False
    if key in ("Enter", "Return"):
        if press:
            typing = False
            refresh(obj)
        return True
    if key == "BackSpace":
        if press and query:
            query = query[:-1]
            refresh(obj)
        return True
    ch = _typed_char(key)
    if ch is None:
        return False  # arrows, page keys, ... still move through the results
    if press and len(query) < MAX_QUERY and not (ch == " " and (not query or query.endswith(" "))):
        query += ch
        refresh(obj)
    return True


# ---- tab logic ----------------------------------------------------------------------------------------------------------------


def _flag(m, on: bool) -> None:
    """The patched movie's Mission Log setup shows the "guide" header instead of "logs" while _root.guideMode is set."""
    global guide_mode
    guide_mode = on
    try:
        m.SetVariableBool("guideMode", on)
    except Exception:  # noqa: BLE001
        pass


def _later(delay: float, fn) -> None:
    _pending.append((time.monotonic() + delay, fn))


def _set_header_frame(m, label: str) -> None:
    try:
        m.GetVariableObject("header").GotoAndStop(label)
    except Exception:
        log("header frame failed: " + traceback.format_exc()[-300:])


def enter_guide(m) -> None:
    global guide_mode, _entering, query, typing
    query, typing = "", False
    _flag(m, True)
    guide_mode = True
    if int(m.CurrentScreen) == MISSION_SCREEN:
        m.extSetupMissionLogScreen()  # the Mission Log's own refresh; our fill hook runs inside it because guide_mode is on
    else:
        _entering = True
        try:
            m.extGenericButtonClicked("missionlog")
        finally:
            _entering = False
    _later(0.2, lambda: _set_header_frame(m, "guide") if guide_mode else None)
    _later(0.35, lambda: _after_enter(m))


def _after_enter(m) -> None:
    if not guide_mode:
        return
    _step("after_enter: decorate")
    _decorate(m)
    _step("after_enter: details")
    apply_details(m)
    _step("after_enter done")


def leave_guide(m, target: str) -> None:
    """Back to a real tab. target is the tab's action name ("missionlog", "challenges", ...)."""
    global guide_mode, query, typing
    guide_mode = typing = False
    query = ""
    _flag(m, False)
    _undecorate(m)
    if target == "missionlog":  # same screen: the Mission Log's own refresh puts the real list back
        m.extSetupMissionLogScreen()
        _set_header_frame(m, "logs")
    else:
        _entering_target(m, target)


def _entering_target(m, target: str) -> None:
    global _entering
    _entering = True  # the nav hook must not treat this as a guide click
    try:
        m.extGenericButtonClicked(target)
    finally:
        _entering = False


def _on_nav(obj, args, ret, func):
    global guide_mode
    if not TAB_MODE or _entering:
        return None
    try:
        name = str(args.ActionName)
        if name == "guide":
            enter_guide(obj)
            return Block
        if guide_mode and name == "missionlog":
            leave_guide(obj, "missionlog")
            return Block
        if guide_mode and name in ("map", "inventory", "skills", "challenges"):
            guide_mode = False
            _flag(obj, False)
            _undecorate(obj)
    except Exception:
        log("nav error " + traceback.format_exc()[-400:])
    return None


def _on_screen(obj, args, ret, func):
    global guide_mode
    if TAB_MODE and guide_mode and not _entering:
        try:
            if int(args.DesiredScreen) != MISSION_SCREEN:
                guide_mode = False
                _flag(obj, False)
                _undecorate(obj)
        except Exception:  # noqa: BLE001
            pass
    return None


def _on_next(obj, args, ret, func):
    """Tab key / shoulder: Badass Rank -> guide -> Mission Log."""
    if not TAB_MODE:
        return None
    try:
        if guide_mode:
            leave_guide(obj, "missionlog")
            return Block
        if int(obj.CurrentScreen) == CHALLENGES_SCREEN:
            enter_guide(obj)
            return Block
    except Exception:
        log("next error " + traceback.format_exc()[-400:])
    return None


def _on_prev(obj, args, ret, func):
    """Previous tab: guide -> Badass Rank; Mission Log (first tab) -> guide, so the six tabs form one loop."""
    if not TAB_MODE:
        return None
    try:
        if guide_mode:
            leave_guide(obj, "challenges")
            return Block
        if int(obj.CurrentScreen) == FIRST_SCREEN:
            enter_guide(obj)
            return Block
    except Exception:
        log("prev error " + traceback.format_exc()[-400:])
    return None


def _on_tick(obj, args, ret, func):
    if not _pending:
        return None
    now = time.monotonic()
    due = [x for x in _pending if x[0] <= now]
    _pending[:] = [x for x in _pending if x[0] > now]
    for _, fn in due:
        try:
            fn()
        except Exception:
            log("pending call failed: " + traceback.format_exc()[-300:])
    return None


FUNCS = {
    "ag_fill": _on_fill, "ag_tips_post": _on_tips_post, "ag_enter": _on_enter, "ag_track": _on_track,
    "ag_close": _on_menu_edge, "ag_start": _on_menu_edge, "ag_active": _on_active, "ag_credits": _on_credits, "ag_other": _on_other, "ag_sort": _on_sort, "ag_ignore": _on_ignore, "ag_nav": _on_nav, "ag_screen": _on_screen,
    "ag_next": _on_next, "ag_prev": _on_prev, "ag_key": _on_key, "ag_tick": _on_tick,
}

_tab_tries = 0


def _load_patcher():
    """swf_patch.py next to this file (loaded by path: this module may be running as a hot-reloaded copy without a package)."""
    if __package__:
        from . import swf_patch

        return swf_patch
    import importlib.util

    spec = importlib.util.spec_from_file_location("ag_swf_patch", HERE / "swf_patch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def enable_tab() -> bool:
    """Give the status menu movie a sixth, "guide" tab: the game's own movie data (SwfMovie.RawData) is patched in memory and
    written back (swf_patch.py). The movie stays in its own package, so its bitmaps still resolve; a separate package would not
    find them. Must run before the status menu is first opened in a session. On any problem the menu is left as it is."""
    global TAB_MODE, _tab_tries
    if TAB_MODE:
        return True
    if (HERE / "no_tab.flag").exists():  # dev switch: run the game's original status menu movie
        log("no_tab.flag present: original status menu movie kept")
        return False
    _tab_tries += 1
    try:
        swf = unrealsdk.find_object("SwfMovie", "UI_StatusMenu.StatusMenu")
        orig = bytes(swf.RawData)
        patcher = _load_patcher()
        if patcher.is_patched(orig):
            TAB_MODE = True
            return True
        try:
            logo = _read("tab_logo.bin")
        except OSError:
            logo = None
        swf.RawData = list(patcher.patch(orig, logo))
        TAB_MODE = True
        log(f"guide tab enabled (status menu movie patched in memory: {len(orig):,} -> {len(swf.RawData):,} bytes, attempt {_tab_tries})")
        return True
    except Exception:
        log("enable_tab failed, the status menu keeps its original tabs: " + traceback.format_exc()[-500:])
        return False


def uninstall() -> None:
    for path, typ, hid in HOOKS:
        try:
            remove_hook(path, typ, hid)
        except Exception:  # noqa: BLE001
            pass


def install(logger=print) -> None:
    global log
    log = logger
    uninstall()
    load_data()
    load_catalog()
    for path, typ, hid in HOOKS:
        add_hook(path, typ, hid, FUNCS[hid])
    log(f"guide installed; {sum(len(b) for _, z in GUIDE for _, b in z)} bosses in {sum(len(z) for _, z in GUIDE)} maps")
