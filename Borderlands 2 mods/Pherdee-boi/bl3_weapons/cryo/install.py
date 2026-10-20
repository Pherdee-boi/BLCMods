"""Cryo's files on the player's machine: built from their own TPS install at the first launch, rebuilt when a mod update changes the converter,
removed on request (CRYO_INSTALL: Aurelia's install.py, without the character). builder.py does the work; this runs it at the right moments and
tells the player what came of it.

- At load, before the game opens any of the files: the rest of an uninstall started in game, then the files that waited for a restart
  (`builder.finish_uninstall`, `builder.apply_pending`).
- Then, on a background thread: `builder.decide()`, and a build when something is missing or outdated.
- A notice (a training box, shown on the game thread at the next frame) says what came of it: restart, TPS not found, files in the way, a failure.
- The engine finds files by name only if they existed at startup, so a first build always needs one restart.
- **The packages' names, for other players' games** (Aurelia 0.4.20): a player who joins a game is sent the host's list of loaded packages and
  looks each up, by name, in the GUID caches its own game has loaded. A name none of them knows is a package to download, and the connection
  ends there. So the mod has a cache of its own, as a DLC has: `load_guid_cache`, one of the setup's steps, makes `GuidCache_cryo.upk` in the
  mod's settings folder from the packages inside the built files and loads it, kept for the session. Every player needs the mod.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base import SETTINGS_DIR, ButtonOption, get_pc, hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

from . import builder
from .core import maps, netobjects, placelist, setup
from .core.setup import Step

TITLE = "Cryo"
NEEDS = "Cryo is built from your own copy of Borderlands: The Pre-Sequel."
RETRY_AFTER = (10.0, 30.0)       # seconds: what failed to build is tried again, twice, before the player is told
WHY_LENGTH = 300                 # of a failure's own words, as much as a notice box holds beside the rest

netobjects.tool = builder.blank_tool      # the converter's writer of blank objects: known here, used there

_notices: list[tuple[str, str]] = []
_lock = threading.Lock()
_thread: threading.Thread | None = None


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Cryo] {msg}")


def game_root() -> Path:
    return Path.cwd().parents[1]          # the game runs from Binaries/Win32


def notify(message: str, title: str = TITLE) -> None:
    """Shown on the game thread at the next frame with a player controller. Any thread may call this."""
    with _lock:
        _notices.append((title, message))


# ---------------------------------------------------------------- at load
def at_load(check: bool = True) -> None:
    """Before the game opens the mod's files: finish an uninstall, swap in waiting files, then decide in the background.
    `check` False (the mod is switched off in the MODS menu): its files are neither checked nor built."""
    root = game_root()
    removed = _run_step("uninstall", builder.finish_uninstall, root)
    if removed:
        notify("Cryo's built files are removed. You can now remove the mod's folder (sdk_mods/bl3_weapons) as well.")
    _run_step("swapped in", builder.apply_pending, root)
    if check:
        start(launch=True)
    else:
        _log("build: the mod is switched off: its files aren't checked")


def _run_step(label: str, step: Any, root: Path) -> list[str]:
    try:
        lines = step(root)
    except Exception as ex:  # noqa: BLE001 - never block the mod's load
        _log(f"build: {label} step failed: {type(ex).__name__}: {ex}")
        return []
    for line in lines:
        _log(f"build: {label}: {line}")
    return lines


# ---------------------------------------------------------------- building
def running() -> bool:
    return _thread is not None and _thread.is_alive()


def start(launch: bool = False, adopt: bool = False, tps: Path | None = None) -> str:
    """Build on a background thread (~10 s of converter work). At launch only what `decide()` asks for, and quietly
    when there's nothing to do."""
    global _thread  # noqa: PLW0603
    if running():
        return "a build is already running"
    _thread = threading.Thread(target=_work, args=(launch, adopt, tps), name="cryo-build", daemon=True)
    _thread.start()
    return "checking cryo's files" if launch else "build started (see the console log for progress)"


def _work(launch: bool, adopt: bool, tps: Path | None) -> None:
    root = game_root()
    try:
        if launch:
            verdict, why = builder.decide(root, tps)
            _log(f"build: at launch: {verdict}{f' ({why})' if why else ''}")
            if verdict == "no tps":
                notify(f"{NEEDS}\n\n{why}.\n\nIf it's installed somewhere else, type in the console:\n"
                       "cryo build run \"<its folder>\"")
                return
            # "blocked" goes on to the build: it takes over a file in the way that is byte for byte its own, and the notice names the files that really are in the way
            if verdict == "restart":
                notify("Cryo's files are built. Restart Borderlands 2 to use cryo.")
                return
            if verdict not in ("build", "blocked"):
                return                                  # ready, or automatic builds are off
        report = builder.build(root, tps, adopt=adopt, log=lambda line: _log(f"build: {line}"))
        _log(f"build: {report.lines[-1] if report.lines else 'nothing done'}")
        # What failed is tried again a little later: a file that another program held for a moment (a scan, a tool)
        # is free by then. A build only makes what is missing or outdated, so the rest isn't touched again.
        built, pending, texts = report.built, report.pending, report.texts
        for pause in RETRY_AFTER:
            if not report.failed or report.blocked:
                break
            _log(f"build: {len(report.failed)} file(s) failed; trying again in {pause:.0f} s")
            time.sleep(pause)
            report = builder.build(root, tps, adopt=adopt, log=lambda line: _log(f"build: {line}"))
            _log(f"build: {report.lines[-1] if report.lines else 'nothing done'}")
            built, pending, texts = built + report.built, pending + report.pending, texts + report.texts
        report.built, report.pending, report.texts = built, pending, texts
        _tell(report)
    except builder.ConverterMissing as ex:
        _log(f"build: {ex}")
        notify("The mod's folder (sdk_mods/bl3_weapons) is incomplete: its \"convert\" folder is missing, so cryo's files "
               "can't be built or updated.\n\nClose the game and put the whole \"cryo\" folder from the release zip "
               "into sdk_mods.")
    except Exception as ex:  # noqa: BLE001 - report, never take the game down
        _log(f"build failed: {type(ex).__name__}: {ex}")
        notify(f"Building cryo's files failed: {type(ex).__name__}: {ex}\n\nThe console log has the details.")


def _failure(report: builder.Report) -> str:
    """What failed and why, in the notice itself: a report that only says "the log has the details" tells its reader
    nothing (the 0.3.4 "permission error" report came without the file Windows refused)."""
    names = sorted({Path(out).name for out in report.failed})
    text = (f"{len(report.failed)} of cryo's files could not be built: {', '.join(names[:3])}"
            f"{' ...' if len(names) > 3 else ''}.")
    why = next(iter(report.errors.values()), "").removeprefix("InitUnavailable: ")
    if why:
        text += f"\n\n{why if len(why) <= WHY_LENGTH else why[:WHY_LENGTH] + ' ...'}"
    refused = any(word in error for error in report.errors.values() for word in ("PermissionError", "InitUnavailable"))
    return (text + "\n\n" + ("Windows refused a file the build needs: another program is using it, or protecting it. "
                             if refused else "")
            + "The mod tries again at every start, and at once from Mods > Cryo > Build cryo's files now. The "
            "console log has every line.")


def _tell(report: builder.Report) -> None:
    if report.blocked:
        notify(f"Cryo's files can't be built: {report.blocked[0]}"
               + ("" if len(report.blocked) == 1 else f" (and {len(report.blocked) - 1} more)")
               + ".\n\nIn the Mods menu (BL3 Weapons, Cryo) press \"Replace files in the way and build\", or type in the console: cryo build adopt\n"
                 "(it moves such files to sdk_mods/settings/cryo_replaced, then builds).")
        return
    parts = []
    # Cryo's texts in the game's other languages need no restart: the game reads them when it loads the packages, and
    # the build has written them by then. Nothing but those written: nothing to tell.
    restart = report.built + report.pending > report.texts + report.radfx
    if report.radfx:
        parts.append("Radiation's effects are built. Restart Borderlands 2 to see them.")
    if report.texts and not restart:
        _log(f"build: cryo's texts in the game's other languages are in place ({report.texts} files); no restart needed")
    if restart:
        parts.append(f"Cryo's files are built from your copy of The Pre-Sequel ({report.built + report.pending - report.radfx} files). "
                     "Restart Borderlands 2 to use cryo.")
    if report.failed:
        parts.append(_failure(report))
    if report.warnings and restart:
        parts.append(f"{len(report.warnings)} of your TPS files differ from the version this mod was tested with. If "
                     "cryo looks or plays wrong, verify The Pre-Sequel's files in its launcher (Steam or Epic).")
    if parts:
        notify("\n\n".join(parts))


# ---------------------------------------------------------------- removing
def uninstall() -> str:
    if running():
        return "a build is running; try again when it's done"
    lines = builder.uninstall(game_root())
    for line in lines:
        _log(f"build: uninstall: {line}")
    held = sum(line.startswith("in use") for line in lines)
    removed = sum(line.startswith("removed ") and not line.startswith("removed folder") for line in lines)
    if held:
        message = (f"{removed} of cryo's files are removed; {held} are in use and go when the game next starts. "
                   "Start Borderlands 2 once more, then remove the mod's folder.")
    else:
        message = f"{removed} of cryo's files are removed. You can now remove the mod's folder (sdk_mods/bl3_weapons)."
    notify(message + "\n\nAutomatic builds are off until you build again.")
    return message


def _confirm_uninstall(_button: ButtonOption) -> None:
    try:
        from ui_utils import OptionBox, OptionBoxButton  # noqa: PLC0415 - the menu's own dependency
    except ImportError:
        _log("build: ui_utils missing; type 'cryo build uninstall' in the console instead")
        return
    remove = OptionBoxButton("Remove cryo's files")

    def chosen(_box: Any, button: Any) -> None:
        if button is remove:
            _log(f"build: {uninstall()}")

    OptionBox(title="Remove cryo's files?",
              message="This removes every file built from your TPS install (cryo's ice effects, icon and texts). Cryo guns and gear "
                      "in your saves are lost the next time a character saves, until cryo is built again.",
              buttons=[remove, OptionBoxButton("Cancel")], on_select=chosen).show()


def _build_now(_button: ButtonOption) -> None:
    _log(f"build: {start()}")


build_button = ButtonOption("Build cryo's files now", on_press=_build_now,
                            description="Builds what's missing or outdated from your Pre-Sequel install, in the "
                                        "background. Restart the game afterwards.")
def _adopt_now(_button: ButtonOption) -> None:
    _log(f"build: {start(adopt=True)}")


adopt_button = ButtonOption("Replace files in the way and build", on_press=_adopt_now,
                            description="When a file cryo needs is already there and isn't the build's own (the notice at start-up names it): moves it to "
                                        "sdk_mods/settings/cryo_replaced, then builds cryo's files. Nothing is deleted. Restart the game afterwards.")
uninstall_button = ButtonOption("Remove cryo's files", on_press=_confirm_uninstall,
                                description="Removes everything built from your Pre-Sequel install (it asks "
                                            "first). Do this before you delete the mod's folder.")


# ---------------------------------------------------------------- the packages' names, for other players' games
GUIDS = "GuidCache_cryo"              # as each of the game's own DLCs names its cache: GuidCache_<name>
PACKAGE_RECIPES = ("icefx",)      # the build's outputs that are packages
_RF_ROOTSET = 0x4000


def guid_cache_file() -> Path:
    return SETTINGS_DIR / f"{GUIDS}.upk"


def _make_guid_cache(root: Path, target: Path) -> str:
    """The packages' names and GUIDs, without the names the game's own caches have, as a GUID cache package."""
    guidcache = builder.guid_tool()
    files = [root / rel for rel, record in builder.load_manifest(root)["files"].items()
             if record.get("recipe") in PACKAGE_RECIPES and (root / rel).is_file()]
    files += netobjects.files()             # and the packages of blank objects made here
    stock = [root / builder.COOKED / "GuidCache.upk", root / builder.COOKED / "GuidCache_maingamerefs.upk",
             *sorted((root / "DLC").glob("*/Compat/Content/GuidCache_*.upk"))]
    known = {key[0].lower() for path in stock if path.is_file() for key in guidcache.read(path)}
    entries: dict[Any, bytes] = {}
    for path in files:
        for key, guid in guidcache.packages_of(path).items():
            if key[0].lower() not in known:
                entries.setdefault(key, guid)
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(guidcache.build(entries, GUIDS))
    tmp.replace(target)
    return f"{len(entries)} names from {len(files)} of the mod's files"


def guid_cache_loaded() -> bool:
    try:
        return unrealsdk.find_object("GuidCache", f"{GUIDS}.GuidCache") is not None
    except ValueError:
        return False


def load_guid_cache() -> bool:
    """A setup step: the GUID cache made (when the build is newer than it, or it isn't there) and loaded. True when
    it is in the game."""
    root, target = game_root(), guid_cache_file()
    manifest = root / builder.MANIFEST
    if not manifest.is_file():
        return False                            # nothing is built: nothing of hers is in the game either
    sources = [manifest, *netobjects.files()]
    if not target.is_file() or any(path.stat().st_mtime_ns > target.stat().st_mtime_ns for path in sources):
        _log(f"co-op: {target.name} made: {_make_guid_cache(root, target)}")
    if not guid_cache_loaded():
        unrealsdk.load_package(str(target))
        cache = unrealsdk.find_object("GuidCache", f"{GUIDS}.GuidCache")
        for obj in (cache, cache.Outer):        # kept: nothing else holds it
            obj.ObjectFlags |= _RF_ROOTSET
        _log(f"co-op: {target.name} loaded: other players' games can be joined, and this mod's by players who have it")
    return True


def place_objects() -> bool:
    """A setup step, and again at each map: the mod's own objects that other players' games are told of get their
    places (core/netobjects.py `place`; the list is core/placelist.py). Before the GUID cache is made: the cache
    names the packages they are in."""
    given, absent = netobjects.place(placelist.PLACES)
    if given:
        _log(f"co-op: {given} of the mod's own objects have their places now ({absent} of the list are not in this game)")
    return True


@hook(maps.ARRIVED, Type.POST)
def _place_later(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    if setup.done():                    # only once the mod's content is in the game
        place_objects()                 # what was made since the setup (a material compiled later) gets its place


STEPS = (Step("records", "places for the mod's own objects", place_objects),
         Step("records", "the packages' names for other players' games", load_guid_cache))

# ---------------------------------------------------------------- notices
@hook("WillowGame.WillowGameViewportClient:Tick", Type.POST)
def _show_notices(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    if not _notices:
        return
    pc = get_pc()
    if pc is None or pc.GFxUIManager is None:
        return
    with _lock:
        title, message = _notices.pop(0)
    try:
        from ui_utils import TrainingBox  # noqa: PLC0415 - the menu's own dependency
        TrainingBox(title=title, message=message).show()
    except Exception as ex:  # noqa: BLE001 - the log line below is the fallback
        _log(f"notice not shown ({type(ex).__name__}: {ex})")
    _log(f"notice: {message}")


HOOKS = (_show_notices, _place_later)
OPTIONS = (build_button, adopt_button, uninstall_button)
