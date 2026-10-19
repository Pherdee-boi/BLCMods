"""Data for the save editor (`bl2-saveedit`, our fork of Gibbed's Borderlands 2 Save Editor): what the running game
knows about modded items, in the editor's own dump format, so it can read, show and write them.

Written to `sdk_mods/settings/aurelia_saveedit.json` once her items are registered (at the main menu), and on
`aurelia saveedit`; only when the content changed. The editor finds it in each Steam install's settings folder and
in the folders listed in its `overlay-folders.txt`.

- `sublibraries`: every sublibrary appended to the base game's asset libraries, by any mod (Athena and Doppelganger
  use the same route), with its package and asset paths. The editor appends them in each save's recorded order (the
  layout keeper's `aurelia_layouts.json`), as the keeper does in game, and in `current_layout` for a save without a
  record.
- `dumps`: entries in the editor's dump format (Gibbed.Borderlands2.GameInfo/Resources/Dumps), for everything the
  appended sublibraries hold and what it refers to: item definitions and their part lists, balances and their part
  collections, name parts; the weapon part lists cryo was added to, the cryo parts and their prefixes; her class,
  its DLC entries, and her customizations.

The sublibraries and what they hold are read here, from the game's libraries, whichever mod appended them. What only a
feature knows (BL2's own lists it added parts to, the character's class) it adds itself: `CONTRIBUTORS`.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

import unrealsdk
from mods_base.settings import SETTINGS_DIR
from unrealsdk.unreal import UObject

from . import items, layout

OUT_FILE = SETTINGS_DIR / "cryo_saveedit.json"
FORMAT = "bl2-saveedit-overlay"
VERSION = 1
GROUPS = ("WeaponTypes", "WeaponParts", "ItemTypes", "ItemParts", "Manufacturers", "BalanceDefs")
WEAPON_SLOTS = ("Body", "Grip", "Barrel", "Sight", "Stock", "Elemental", "Accessory1", "Accessory2", "Material")
ITEM_SLOTS = ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Material")
ITEM_TYPES = {"ClassModDefinition": "ClassMod", "CrossDLCClassModDefinition": "CrossDLCClassMod",
              "ArtifactDefinition": "Artifact", "GrenadeModDefinition": "GrenadeMod", "ShieldDefinition": "Shield",
              "UsableCustomizationItemDefinition": "UsableCustomizationItem", "UsableItemDefinition": "UsableItem",
              "MissionItemDefinition": "MissionItem"}

_last = {"digest": ""}


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] save editor data: {msg}")


def _path(obj: UObject | None) -> str | None:
    return obj._path_name() if obj is not None else None


def _find(path: str) -> UObject | None:
    try:
        return unrealsdk.find_object("Object", path)
    except ValueError:
        return None


def _enum_name(value: Any, prefix: str) -> str:
    return str(getattr(value, "name", value)).removeprefix(prefix)


def _asset_path(entry: Any) -> str:
    """A sublibrary asset path within its package ("A_Item_Baroness.ClassMod_Baroness_Lady"), from the engine's
    NameBasedObjectPath (component names, "None" for unused leading ones)."""
    return ".".join(str(n) for n in entry.PathComponentNames if str(n) != "None")


# ---------------------------------------------------------------- the appended sublibraries
def sublibraries() -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Every sublibrary past the base game's in the base set's libraries: path -> {library, package, assets};
    and this session's order per library."""
    manager = items._manager()
    base = next((s for s in manager.RuntimeAssetLibraries if s.Id == 0), None) if manager is not None else None
    table: dict[str, dict[str, Any]] = {}
    order: dict[str, list[str]] = {}
    if base is None:
        return table, order
    for index, library in enumerate(base.Libraries):
        group = GROUPS[index]
        names = []
        for sub in list(library.SublibraryLinks)[layout.BASE_SIZES[index]:]:
            if sub is None:
                continue
            path = sub._path_name()
            names.append(path)
            table[path] = {"library": group, "package": str(sub.CachedPackageName),
                           "assets": [_asset_path(a) for a in sub.AssetPaths]}
        if names:
            order[group] = names
    return table, order


# ---------------------------------------------------------------- dump entries
class Dumps:
    """Dump entries by dump name ("Items", "Item Balance", ...), following references from the starting objects."""

    def __init__(self) -> None:
        self.out: dict[str, dict[str, Any]] = {}

    def has(self, dump: str, key: str) -> bool:
        return key in self.out.get(dump, {})

    def put(self, dump: str, key: str, value: dict[str, Any] | list[Any]) -> None:
        self.out.setdefault(dump, {})[key] = value

    # items
    def item_definition(self, definition: UObject | None) -> None:
        key = _path(definition)
        if key is None or self.has("Items", key):
            return
        kind = ITEM_TYPES.get(definition.Class.Name)
        if kind is None:
            return
        entry: dict[str, Any] = {"type": kind}
        name = str(getattr(definition, "ItemName", "") or "")
        if name:
            entry["name"] = name
        if getattr(definition, "bItemNameIsFullName", False):
            entry["has_full_name"] = True
        self.put("Items", key, entry)                         # before following references: cycles end here
        for field, dump_key in (("TitleList", "titles"), ("PrefixList", "prefixes")):
            parts = [p for p in getattr(definition, field, []) if p is not None]
            if parts:
                entry[dump_key] = [_path(p) for p in parts]
                for part in parts:
                    self.item_name_part(part)
        for slot in ITEM_SLOTS:
            plist = getattr(definition, f"{slot}Parts", None)
            if plist is not None:
                entry[f"{slot.lower()}_parts"] = _path(plist)
                self.item_part_list(plist)

    def item_part_list(self, plist: UObject) -> None:
        key = _path(plist)
        if self.has("Item Part Lists", key):
            return
        self.put("Item Part Lists", key, [_path(e.Part) for e in plist.WeightedParts if e.Part is not None])

    def item_name_part(self, part: UObject) -> None:
        key = _path(part)
        if self.has("Item Name Parts", key):
            return
        entry: dict[str, Any] = {}
        name = str(getattr(part, "PartName", "") or "")
        if name:
            entry["name"] = name
        if getattr(part, "bNameIsUnique", False):
            entry["unique"] = True
        self.put("Item Name Parts", key, entry)

    def item_balance(self, balance: UObject | None) -> None:
        key = _path(balance)
        if key is None or self.has("Item Balance", key) or balance.Class.Name == "WeaponBalanceDefinition":
            return
        entry: dict[str, Any] = {}
        self.put("Item Balance", key, entry)
        definitions = [d for d in getattr(balance, "ClassModDefinitions", []) if d is not None]
        if definitions:
            entry["items"] = [_path(d) for d in definitions]
        elif getattr(balance, "InventoryDefinition", None) is not None:
            entry["item"] = _path(balance.InventoryDefinition)
        for definition in definitions or [balance.InventoryDefinition]:
            self.item_definition(definition)
        if balance.BaseDefinition is not None:
            entry["base"] = _path(balance.BaseDefinition)
            self.item_balance(balance.BaseDefinition)
        makers = [_path(m.Manufacturer) for m in balance.Manufacturers if m.Manufacturer is not None]
        if makers:
            entry["manufacturers"] = makers
        # class mods' balances name their parts ItemPartListCollection; other item balances PartListCollection
        coll = getattr(balance, "ItemPartListCollection", None) or getattr(balance, "PartListCollection", None)
        if coll is not None:
            entry["parts"] = _path(coll)
            self.item_part_collection(coll)

    def item_part_collection(self, coll: UObject) -> None:
        key = _path(coll)
        if self.has("Item Balance Part Lists", key):
            return
        entry: dict[str, Any] = {"mode": _enum_name(coll.PartReplacementMode, "EPRM_")}
        if getattr(coll, "AssociatedItem", None) is not None:
            entry["item"] = _path(coll.AssociatedItem)
        for slot in ITEM_SLOTS:
            data = getattr(coll, f"{slot}PartData")
            if data.bEnabled:
                entry[slot.lower()] = [_path(e.Part) for e in data.WeightedParts if e.Part is not None]
        self.put("Item Balance Part Lists", key, entry)

    # weapons
    def weapon_part_collection(self, coll: UObject) -> None:
        entry: dict[str, Any] = {"mode": _enum_name(coll.PartReplacementMode, "EPRM_")}
        if getattr(coll, "AssociatedWeaponType", None) is not None:
            entry["weapon_type"] = _path(coll.AssociatedWeaponType)
        for slot in WEAPON_SLOTS:
            data = getattr(coll, f"{slot}PartData")
            if data.bEnabled:
                entry[slot.lower()] = [_path(e.Part) for e in data.WeightedParts if e.Part is not None]
        self.put("Weapon Balance Part Lists", _path(coll), entry)

    def weapon_part(self, part: UObject) -> None:
        entry: dict[str, Any] = {"type": _enum_name(part.PartType, "WP_")}
        for field, dump_key in (("TitleList", "titles"), ("PrefixList", "prefixes")):
            names = [p for p in getattr(part, field, []) if p is not None]
            if names:
                entry[dump_key] = [_path(p) for p in names]
                for name in names:
                    self.put("Weapon Name Parts", _path(name), {"name": str(name.PartName)})
        self.put("Weapon Parts", _path(part), entry)


# What each feature adds to the dumps, by its name: a callable taking the `Dumps` being filled.
CONTRIBUTORS: dict[str, Callable[[Dumps], None]] = {}


def build() -> dict[str, Any]:
    table, order = sublibraries()
    dumps = Dumps()
    for info in table.values():
        for asset in info["assets"]:
            obj = _find(f"{info['package']}.{asset}")
            if obj is None:
                continue
            if info["library"] == "ItemTypes":
                dumps.item_definition(obj)
            elif info["library"] == "BalanceDefs":
                dumps.item_balance(obj)
            elif info["library"] == "ItemParts" and "NamePart" in obj.Class.Name:
                dumps.item_name_part(obj)
            elif info["library"] == "WeaponParts" and obj.Class.Name == "WeaponPartDefinition":
                dumps.weapon_part(obj)
    for contribute in CONTRIBUTORS.values():
        contribute(dumps)
    return {"format": FORMAT, "version": VERSION, "source": "Aurelia (bl2-aurelia)", "sublibraries": table,
            "current_layout": order, "dumps": dumps.out}


def export(why: str = "") -> str:
    """Write the file when its content changed. Returns what happened."""
    data = build()
    text = json.dumps(data, indent=1, sort_keys=True)
    digest = hashlib.sha1(text.encode()).hexdigest()  # noqa: S324 - a change check
    if digest == _last["digest"] and OUT_FILE.is_file():
        return "unchanged"
    data["written"] = time.strftime("%Y-%m-%d %H:%M:%S")
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(OUT_FILE)
    _last["digest"] = digest
    counts = {k: len(v) for k, v in data["dumps"].items()}
    line = f"{len(data['sublibraries'])} sublibraries, {counts}" + (f" ({why})" if why else "")
    _log(line)
    return line
