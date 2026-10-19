"""S3 item-save spike (dev): her items must survive save and reload (RESEARCH.md §6.5, §8 S3).

BL2 saves an item as asset-library indices: per asset a SKU (DLC id), a sublibrary and an index into it
(`AssetLibraryManager`, 6 libraries per SKU: weapon types, weapon parts, item types, item parts,
manufacturers, balances). An object that is in no library can't be saved.

Measured 2026-09-30 with a runtime-built class mod (definition + balance):
- A runtime SKU-31 library set appended to `RuntimeAssetLibraries`, with her `DownloadableItemSetDefinition`
  on the balance: `PackSerialNumber`/`UnpackSerialNumber` round-trip it in memory, but the native save
  writer nulls the item's definition and balance, even with PackageId 31's bit set in the DLC manager's
  `CachedSimplePackageLicenseInfo`. The native DLC state behind that isn't reachable from script.
- Her sublibraries appended to the base game's libraries (SKU 0, Athena's route): the item survives
  Save & Quit with its definition and balance.

The saved index is the sublibrary's position in the base library, and every item mod appends there,
so the positions depend on load order. Hers are appended like the others' (at registration, before any
save is read), and the layout keeper (`layout.py`) puts each save's recorded order back before it is read.
Rejected (2026-09-30/10-01): reserved high slots padded with empty sublibraries (padding shifts every
mod that appends later); her own DLC id (the native writer drops it).
"""

from __future__ import annotations

import unrealsdk
from mods_base import get_ordered_mod_list
from unrealsdk.unreal import UObject

ITEM_PACKAGE = "GD_AureliaItems"
# Mods that read saved items in the game's place, by the name the MODS menu shows (`check_taken_over`).
CHECK_TAKERS = ("Sanity Saver",)
_RF_ROOTSET = 0x4000

# Library indices in `AssetLibraryManager.LibraryConfigs` / a set's `Libraries` (0 is the weapon types': nothing of
# the mod's goes there).
LIB_WEAPON_PARTS = 1
LIB_ITEM_TYPES = 2
LIB_ITEM_PARTS = 3
LIB_BALANCE = 5

# Spike content: a class mod cloned from Maya's, standing in for her converted ones.
SPIKE_DEF = "ClassMod_Aurelia_Spike"
SPIKE_BAL = "BalDef_ClassMod_Aurelia_Spike"
SPIKE_TEMPLATES = {
    SPIKE_DEF: ("ClassModDefinition", "GD_ClassMods.A_Item_Siren.ClassMod_Siren_Binder"),
    SPIKE_BAL: ("ClassModBalanceDefinition", "GD_ItemGrades.ClassMods.BalDef_ClassMod_Siren_03_Rare"),
}


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] items: {msg}")


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _root(obj: UObject) -> UObject:
    obj.ObjectFlags = int(obj.ObjectFlags) | _RF_ROOTSET
    return obj


def _get(cls: str, outer: UObject, name: str, template: UObject | None = None) -> UObject:
    obj = _find(cls, f"{outer._path_name()}.{name}")
    if obj is None:
        obj = (unrealsdk.construct_object(cls, outer, name, 0, template) if template is not None
               else unrealsdk.construct_object(cls, outer, name))
    return _root(obj)


def _manager() -> UObject | None:
    """The game's asset library manager. WillowGlobals holds it; looking for it among every object (the fallback)
    takes 4 to 7 ms, which every save paid until 0.4.5 (the layout keeper asks at each one)."""
    try:
        held = unrealsdk.find_class("WillowGlobals").ClassDefaultObject.GetWillowGlobals().AssLibMan
    except Exception:  # noqa: BLE001 - no globals yet: look for it
        held = None
    if held is not None:
        return held
    return next((o for o in unrealsdk.find_all("AssetLibraryManager") if not o.Name.startswith("Default__")), None)


def package(name: str = ITEM_PACKAGE) -> UObject:
    """A runtime package for saveable assets. The asset encoder finds a sublibrary by package name, so its
    assets must sit directly in it."""
    pkg = _find("Package", name) or unrealsdk.construct_object("Package", None, name)
    return _root(pkg)


_package = package


def _sublibrary(pkg: UObject, name: str, assets: list[UObject]) -> UObject:
    sub = _get("PackageAssetSublibrary", pkg, name)
    sub.CachedPackageName = pkg.Name
    sub.AssetPaths = [unrealsdk.make_struct("NameBasedObjectPath", PathComponentNames=["None"] * 5 + [a.Name],
                                            IsSubobjectMask=0) for a in assets]
    return sub


def _append(library: UObject, sub: UObject) -> bool:
    """Append `sub` to a base library once. True if it was added now."""
    path = sub._path_name()
    if any(x is not None and x._path_name() == path for x in library.SublibraryLinks):
        return False
    library.SublibraryLinks.append(sub)
    library.Sublibraries.append(path)
    return True


def append_to_base(lib_index: int, pkg: UObject, assets: list[UObject]) -> int | None:
    """Append (once) a sublibrary of `assets` from `pkg` to the base game's library `lib_index`, the route
    that survives saving (see the module docstring). Returns its position there, or None if the asset
    manager isn't up yet. The layout keeper records and restores the position per save."""
    manager = _manager()
    base = next((s for s in manager.RuntimeAssetLibraries if s.Id == 0), None) if manager is not None else None
    if base is None:
        return None
    library = base.Libraries[lib_index]
    sub = _sublibrary(pkg, f"Sublib_{pkg.Name}_{lib_index}", assets)
    if _append(library, sub):
        _log(f"{pkg.Name}: appended to {manager.LibraryConfigs[lib_index].Desc} at {len(library.SublibraryLinks) - 1}")
    path = sub._path_name()
    return next(i for i, x in enumerate(library.SublibraryLinks) if x is not None and x._path_name() == path)


def check_taken_over() -> bool:
    """Is a mod switched on that reads saved items in the game's place? Then the game's check of a saved item (its
    "sanity check", `ValidateWeaponDefinition` and `ValidateItemDefinition`) is that mod's to answer, not ours.

    Sanity Saver reads a save's guns by letting the game's own reader run once with the check blocked, so that it
    makes nothing, and then makes every gun itself; it opens the bank and the stash the same way. A hook of ours that
    said yes for a cryo gun while that reader ran undid the block for that one gun: the game made it, Sanity Saver
    made it again, and every cryo gun a character carried was there twice after each load (0.4.6 to 0.4.12; the
    reports of 2026-10-05, RESEARCH.md §6.59). With such a mod on nothing is thrown away, so there is nothing for us
    to answer."""
    try:
        return any(mod.is_enabled and mod.name in CHECK_TAKERS for mod in get_ordered_mod_list())
    except Exception:  # noqa: BLE001 - a mod list that can't be read is no reason to lose the answer
        return False


def _spike_objects(pkg: UObject) -> dict[str, UObject] | None:
    objs = {}
    for name, (cls, template_path) in SPIKE_TEMPLATES.items():
        template = _find(cls, template_path)
        if template is None:
            return None
        objs[name] = _get(cls, pkg, name, template)
    objs[SPIKE_BAL].InventoryDefinition = objs[SPIKE_DEF]
    objs[SPIKE_BAL].DlcItemSet = None
    return objs


def register() -> bool:
    """Idempotent: her item objects exist and are appended to the base libraries. Must run before
    a save is read (the setup's first item step, setup.py)."""
    manager = _manager()
    if manager is None:
        return False
    base = next((s for s in manager.RuntimeAssetLibraries if s.Id == 0), None)
    if base is None:
        return False
    pkg = _package()
    objs = _spike_objects(pkg)
    if objs is None:
        return False
    configs = manager.LibraryConfigs
    added = {}
    for lib_index, assets in ((LIB_ITEM_TYPES, [objs[SPIKE_DEF]]), (LIB_BALANCE, [objs[SPIKE_BAL]])):
        library = base.Libraries[lib_index]
        sub = _sublibrary(pkg, f"Sublib_Aurelia_{lib_index}", assets)
        if _append(library, sub):
            added[configs[lib_index].Desc] = len(library.SublibraryLinks) - 1
    if added:
        _log(f"appended to the base libraries at {added}")
    return True
