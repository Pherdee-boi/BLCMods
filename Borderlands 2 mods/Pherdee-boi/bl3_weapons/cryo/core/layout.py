"""Layout keeper: saved items of every mod load back, whatever order the mods append in.

BL2 saves an item as positions in the base game's asset libraries (RESEARCH.md §6.5, §7 "S3 status").
Mods that add items (Athena, Doppelganger, this one) append their own sublibraries to those libraries,
so a sublibrary's position depends on which mods appended first; when that changes, saved items read back
as other items or not at all. The keeper records, for each save, the order of all appended sublibraries
when the save was written, and puts them back in that order right before the save is read. A placeholder
keeps the place of a sublibrary whose mod is missing. Only the appended part of each library moves; the
base game's entries never do.

A record is used only while the save file is exactly as it was written under it (SHA-1 of the file), so a
save written elsewhere (the Steam copy, a session without this mod) loads in the order of the day, as it
would without the keeper. Records live in the mod settings folder, never next to the saves.

"The order of the day" is the order the mods appended in, taken before the first record moves anything
(`_natural`). Up to 0.4.7 a save without a record was read in whatever order the last save's record had
left: after a character whose record is of an older order (her sublibraries have changed places between
releases), a save from elsewhere came up with its modded items read as others.

Claptrap's stash lives in the profile, not the save (four slots, each an item's serial number), written and
read by `WillowPlayerController.WriteStashToProfile` / `ReadStashFromProfile`. So the stash gets a record of
its own, validated by a hash of the four slots, and is read in its own order, after which the save's order is
restored.

Not covered yet: co-op, where clients would take the host's order (M10).
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base import hook
from mods_base.settings import SETTINGS_DIR
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

from . import items

def _migrated(new: Path, old_name: str) -> Path:
    """`new` (a file of this mod in the settings folder), copied once from the Aurelia mod's file of that name when it is not there yet: what
    Aurelia recorded (the saves' layouts, the compiled shaders) is what this mod starts from."""
    if not new.exists():
        old = new.with_name(old_name)
        if old.is_file():
            try:
                import shutil  # noqa: PLC0415

                shutil.copyfile(old, new)
            except OSError:
                pass
    return new


RECORDS_FILE = _migrated(SETTINGS_DIR / "cryo_layouts.json", "aurelia_layouts.json")
PLACEHOLDER_PACKAGE = "AureliaLayoutKeeper"
_RF_ROOTSET = 0x4000

# The base game's sublibrary counts per library (weapon types, weapon parts, item types, item parts,
# manufacturers, balances), measured 2026-09-30. Everything past them was appended by mods.
BASE_SIZES = (39, 14, 103, 6, 2, 141)

_placeholders: dict[tuple[int, str], UObject] = {}   # (library, stood-in name) -> placeholder
_stands_for: dict[str, str] = {}                     # placeholder path -> stood-in name
_pending: dict[str, dict[str, Any]] = {}            # save file name -> {layout, before, seen, since}
_loading: dict[str, list[str]] | None = None         # layout applied for the save being read
_natural: dict[str, list[str]] | None = None         # the order the mods appended in: "the order of the day"
_next_poll = 0.0
POLL_SECONDS = 0.5
PENDING_TIMEOUT = 120.0                              # give up on a save that never shows up on disk


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] layout: {msg}")


# --- Where the saves are

def _documents() -> list[Path]:
    """Candidates for the Documents folder the game uses: the known folder (OneDrive may redirect it),
    then the usual places."""
    roots = []
    try:
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:   # CSIDL_PERSONAL
            roots.append(Path(buf.value))
    except (AttributeError, OSError):
        pass
    roots += [Path.home() / "OneDrive" / "Documents", Path.home() / "Documents"]
    return roots


def _save_file(filename: str) -> Path | None:
    """The newest `SaveData/<steam id>/<filename>`; None if there is none."""
    found = []
    for docs in _documents():
        root = docs / "My Games" / "Borderlands 2" / "WillowGame" / "SaveData"
        found += [p for p in root.glob(f"*/{filename}") if p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


# --- Records

def _load_records() -> dict[str, Any]:
    try:
        data = json.loads(RECORDS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("saves"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "saves": {}}


def _write_records(data: dict[str, Any]) -> None:
    RECORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RECORDS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _store_record(path: Path, layout: dict[str, list[str]]) -> None:
    data = _load_records()
    data["saves"][str(path).lower()] = {
        "sha1": _sha1(path),
        "written": time.strftime("%Y-%m-%d %H:%M:%S"),
        "layout": layout,
    }
    _write_records(data)


def _record_for(path: Path) -> dict[str, list[str]] | None:
    """The layout recorded for this save, if the file is still exactly as written under it."""
    record = _load_records()["saves"].get(str(path).lower())
    if record is None:
        return None
    if record.get("sha1") != _sha1(path):
        _log(f"{path.name} changed since its layout was recorded; loading it in today's order")
        return None
    return record.get("layout")


# --- Libraries

def _base_libraries() -> list[UObject]:
    manager = items._manager()
    if manager is None:
        return []
    base = next((s for s in manager.RuntimeAssetLibraries if s.Id == 0), None)
    return list(base.Libraries) if base is not None else []


def _entry_name(link: UObject | None, listed: str) -> str:
    """The sublibrary an appended entry stands for: a placeholder stands for the missing one."""
    if link is not None:
        path = link._path_name()
        return _stands_for.get(path, path)
    return listed


def current_layout() -> dict[str, list[str]]:
    """{library index: the appended sublibraries in order}. Placeholders count as what they stand for."""
    layout = {}
    for index, library in enumerate(_base_libraries()):
        links, listed = list(library.SublibraryLinks), [str(x) for x in library.Sublibraries]
        base = BASE_SIZES[index]
        names = [_entry_name(links[i] if i < len(links) else None, listed[i] if i < len(listed) else "")
                 for i in range(base, max(len(links), len(listed)))]
        if names:
            layout[str(index)] = names
    return layout


def _placeholder(index: int, name: str) -> UObject:
    key = (index, name)
    obj = _placeholders.get(key)
    if obj is None:
        try:
            pkg = unrealsdk.find_object("Package", PLACEHOLDER_PACKAGE)
        except ValueError:
            pkg = unrealsdk.construct_object("Package", None, PLACEHOLDER_PACKAGE)
        pkg.ObjectFlags = int(pkg.ObjectFlags) | _RF_ROOTSET
        obj = unrealsdk.construct_object("PackageAssetSublibrary", pkg, f"Missing_{index}_{len(_placeholders)}")
        obj.ObjectFlags = int(obj.ObjectFlags) | _RF_ROOTSET
        # Each placeholder needs a package name of its own: with one shared object (and name) standing
        # in several slots, encoding and decoding broke for the whole library (S3, 2026-09-30).
        obj.CachedPackageName = obj.Name
        _placeholders[key] = obj
        _stands_for[obj._path_name()] = name
    return obj


def arrange(layout: dict[str, list[str]]) -> int:
    """Put each library's appended sublibraries in the recorded order: recorded ones first (placeholders
    for missing ones), then any not in the record. Returns how many entries changed position."""
    moved = 0
    for index, library in enumerate(_base_libraries()):
        wanted = layout.get(str(index), [])
        base = BASE_SIZES[index]
        links, listed = library.SublibraryLinks, library.Sublibraries
        if len(links) != len(listed) or len(links) < base:
            _log(f"{library.Name}: {len(links)} links for {len(listed)} names (base {base}); left as it is")
            continue
        present: dict[str, tuple[UObject | None, str]] = {}
        before = []
        for i in range(base, len(links)):
            link, name = links[i], str(listed[i])
            stood = _entry_name(link, name)
            before.append(stood)
            if link is not None and link._path_name() in _stands_for:
                continue                                   # an old placeholder: rebuilt below if still needed
            present.setdefault(stood, (link, name))
        order: list[tuple[UObject | None, str]] = []
        for name in wanted:
            if name in present:
                order.append(present.pop(name))
            else:
                ph = _placeholder(index, name)
                order.append((ph, ph._path_name()))
        order.extend(present.values())                     # appended by mods the record doesn't know
        after = [_entry_name(link, name) for link, name in order]
        if after == before:
            continue
        while len(links) > base:
            links.pop()
            listed.pop()
        for link, name in order:
            links.append(link)
            listed.append(name)
        moved += sum(1 for a, b in zip(before, after) if a != b) + abs(len(after) - len(before))
    return moved


# --- Hooks

def _stat(filename: str) -> tuple[Path, float, int] | None:
    path = _save_file(filename)
    if path is None:
        return None
    st = path.stat()
    return path, st.st_mtime, st.st_size


def _remember(filename: str) -> None:
    """A save is being written: keep the layout it is encoded with until the file is on disk."""
    if filename:
        stat = _stat(filename)
        _pending[filename] = {"layout": current_layout(), "before": stat[1:] if stat else None,
                              "seen": None, "since": time.monotonic()}
        _save_tick.enable()                           # until its record is written (it turns itself off)


def stop() -> int:
    """The mod is switched off in the MODS menu: the tick that waits for a save's file is turned off (it is turned on
    by hand, so the SDK doesn't turn it off with the mod's listed hooks). A save still waiting gets no record: it is
    read in the order of the day, as a save from before the mod is. Returns how many were waiting."""
    waiting = len(_pending)
    _pending.clear()
    _save_tick.disable()
    return waiting


def _finish_pending() -> None:
    """Record each pending save once its file has changed and then stayed the same for one poll.
    (The save-complete event is not dispatched through script for Save & Quit, 2026-10-01.)"""
    for filename, entry in list(_pending.items()):
        stat = _stat(filename)
        if stat is None or stat[1:] == entry["before"]:
            if time.monotonic() - entry["since"] > PENDING_TIMEOUT:
                del _pending[filename]
                _log(f"{filename} did not change on disk after saving; no layout recorded")
            continue
        if stat[1:] != entry["seen"]:
            entry["seen"] = stat[1:]                  # changed: wait one poll for the write to settle
            continue
        path = stat[0]
        _store_record(path, entry["layout"])
        del _pending[filename]
        _log(f"recorded the layout {path.name} was written with "
             f"({sum(len(v) for v in entry['layout'].values())} appended sublibraries)")


@hook("WillowGame.WillowSaveGameManager:SaveGame")
def _on_manager_save(_obj: UObject, args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    _remember(str(args.Filename))


@hook("WillowGame.WillowPlayerController:SaveGame")
def _on_controller_save(_obj: UObject, args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    _remember(str(args.Filename))


# Per-frame candidates (the ones the dev bridge polls from). On only while a save's record is pending: a hook that
# has nothing to do still costs the game its way into Python and back, three times a frame here (0.4.5).
@hook("WillowGame.WillowGameViewportClient:Tick", Type.POST)
@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
@hook("WillowGame.WillowPlayerInput:PlayerInput", Type.POST)
def _save_tick(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    global _next_poll  # noqa: PLW0603
    if not _pending:
        _save_tick.disable()
        return
    now = time.monotonic()
    if now < _next_poll:
        return
    _next_poll = now + POLL_SECONDS
    _finish_pending()


# The stash's four slots are profile settings (WillowPlayerController.WPS_StashSlot0..3) holding one item's
# serial number each. `ReadStashFromProfile`/`WriteStashToProfile` fill their Buffer argument themselves
# (it arrives empty), so the slots are read from the profile directly.
STASH_SLOTS = (130, 131, 132, 133)


def _stash_sha1() -> str | None:
    profile = next((o for o in unrealsdk.find_all("WillowProfileSettings") if not o.Name.startswith("Default__")), None)
    if profile is None:
        return None
    digest = hashlib.sha1()
    for slot in STASH_SLOTS:
        result = profile.GetProfileSettingValueBlob(slot, [])
        blob = bytes(list(result[1])) if isinstance(result, tuple) and len(result) > 1 else b""
        digest.update(len(blob).to_bytes(4, "little") + blob)
    return digest.hexdigest()


_stash_restore: dict[str, list[str]] | None = None   # the order to go back to after reading the stash


@hook("WillowGame.WillowPlayerController:WriteStashToProfile", Type.POST)
def _on_stash_write(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    """The stash's slots were just encoded with the current order: record it with their hash."""
    digest = _stash_sha1()
    if digest is None:
        return
    data = _load_records()
    data["stash"] = {"sha1": digest, "written": time.strftime("%Y-%m-%d %H:%M:%S"), "layout": current_layout()}
    _write_records(data)
    _log("recorded the layout the stash was written with")


@hook("WillowGame.WillowPlayerController:ReadStashFromProfile")
def _on_stash_read(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    global _stash_restore  # noqa: PLW0603
    record = _load_records().get("stash")
    if record is None:
        _log("reading the stash; no recorded layout")
        return
    if record.get("sha1") != _stash_sha1():
        _log("the stash changed since its layout was recorded; reading it in today's order")
        return
    _stash_restore = current_layout()
    moved = arrange(record["layout"])
    _log(f"reading the stash in the order it was written with ({moved} entries moved)")


# `ReadStashFromProfile` only fetches the slots; `WillowInventoryStorage.Open` builds the items from them
# after it returns (switching back at its end lost a stash item, 2026-10-01). So the save's order returns
# once Open is done. "Done" also when another mod's hook ran Open itself and blocked the game's own call, as Sanity
# Saver's does: a hook that only runs after an unblocked call (until 0.4.12) never ran with that mod on, and the
# stash's order stayed in place for whatever was read or written next (RESEARCH.md §6.59).
@hook("WillowGame.WillowInventoryStorage:Open", Type.POST_UNCONDITIONAL)
def _after_stash_read(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    global _stash_restore  # noqa: PLW0603
    if _stash_restore is not None:
        moved = arrange(_stash_restore)
        _stash_restore = None
        if moved:
            _log(f"back to the save's order after the stash ({moved} entries moved)")


def order_of_the_day() -> dict[str, list[str]]:
    """The order the mods appended in, as it was before any record was applied. What a mod appends later is not in
    it, and stays where it was appended: after."""
    global _natural  # noqa: PLW0603
    if _natural is None:
        _natural = current_layout()
    return _natural


def _before_read(filename: str) -> None:
    global _loading  # noqa: PLW0603
    try:  # CRYO_EARLY_SETUP: the mod's whole setup (cryo's objects, its item libraries) is done before the save is read: nobody else calls it
        from . import setup as _setup  # noqa: PLC0415

        _setup.run()
    except Exception as ex:  # noqa: BLE001 - the read goes on; the item libraries are registered just below
        _log(f"setup before the read failed: {type(ex).__name__}: {ex}")
    items.register()                                   # the mod's own sublibraries must be in place before arranging
    natural = order_of_the_day()                       # before anything is moved
    if filename in _pending:
        _finish_pending()                              # saved and read again before the tick caught up
    path = _save_file(filename)
    layout = _record_for(path) if path is not None else None
    _loading = layout
    _log(f"reading {filename} ({path}); recorded layout: {'yes' if layout is not None else 'no'}")
    if layout is not None:
        moved = arrange(layout)
        _log(f"{filename}: restored the layout it was written with ({moved} entries moved)")
    else:
        moved = arrange(natural)                       # not the order the last save's record left
        if moved:
            _log(f"{filename}: no record, so the order of the day ({moved} entries moved back)")


@hook("WillowGame.WillowSaveGameManager:BeginLoadGame")
def _on_begin_load(_obj: UObject, args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    _before_read(str(args.Filename))


@hook("WillowGame.WillowSaveGameManager:EndLoadGame")
def _on_end_load(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    # Again at the last moment, for mods that appended between the two calls.
    if _loading is not None:
        arrange(_loading)
    elif _natural is not None:
        arrange(_natural)


# _save_tick isn't here: a save turns it on, and it turns itself off once the save's record is written
HOOKS = (_on_manager_save, _on_controller_save, _on_begin_load, _on_end_load,
         _on_stash_write, _on_stash_read, _after_stash_read)
