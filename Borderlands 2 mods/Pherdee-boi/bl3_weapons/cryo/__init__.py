"""Cryo for Borderlands 2: The Pre-Sequel's element (freezing and shattering, cryo guns, grenades, shields and relics), as a mod of its own.

This is the cryo part of the Aurelia mod by Grim Reader (and Claude), used as the base with Grim Reader's permission. Aurelia's code was already
built in layers: `core` (what the features stand on), `cryo` (the element) and `baroness` (the character). This mod is `core` and `cryo`, with
the build and install parts cut down to cryo's files (builder.py, install.py), and without the character, her skills, class mods, voice, slam and
Contract. Everything cryo did in Aurelia it does here: the freezing, the frozen look, the cryo guns that drop like any other element, cryo
grenades, shields and relics, the icon on item cards, the texts, and the saved items (the packages and sublibraries keep Aurelia's names, so
cryo guns and gear already in your saves load as they did).

DO NOT RUN IT TOGETHER WITH AURELIA: both would build the same objects and hook the same functions. When the Aurelia mod is installed and not
switched off, this mod stands aside (`stands_aside`): it builds nothing and hooks nothing, and says so in the log.

The mod is put together here from two packages, each importing only the ones before it:
- `core`: what the features stand on (the one-time setup, saveable items and the layout keeper, runtime materials);
- `cryo`: The Pre-Sequel's element (damage, freezing, guns and gear, the frozen look).
This module and install.py and builder.py beside it are the mod itself: its menu entry, its console command, and building its files from the
player's TPS install.
"""

import importlib
import json
import shutil
import sys
import threading
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base import SETTINGS_DIR, GroupedOption, command, get_pc, hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

__version__ = "0.1.0"   # the cryo part of BL3 Weapons


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Cryo] {msg}")


def _read(name: str) -> dict[str, Any] | None:
    try:
        data = json.loads((SETTINGS_DIR / name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def stands_aside() -> bool:
    """True when the Aurelia mod is installed (its folder is beside this one) and not switched off in the MODS menu: it has everything cryo
    has, and two copies of the same objects and hooks would fight."""
    if not (SETTINGS_DIR.parent / "aurelia" / "__init__.py").is_file():
        return False
    settings = _read("aurelia.json")
    return not (settings is not None and settings.get("enabled") is False)


def _radiation_beside_aurelia() -> list[Any]:
    """Radiation's effects (radfx) still run when this half stands aside: they need only a little of core, and Aurelia's core has the same. Their
    package is cut from BL2's own Startup.upk (no TPS); a first build needs one restart, which a notice says. Returns the hooks to add."""
    from .. import radfx  # noqa: PLC0415 - on Aurelia's core, see radfx.py
    from . import builder  # noqa: PLC0415

    root = Path.cwd().parents[1]                     # the game runs from Binaries/Win32
    told: list[str] = []
    try:
        builder.apply_pending(root)                  # files that waited for a restart go in before the game opens them
        aurelia_setup = importlib.import_module("aurelia.core.setup")
        aurelia_setup.use(aurelia_setup.Step("looks", "radiation effects (bl3_weapons)", radfx.load))
    except Exception as ex:  # noqa: BLE001 - radiation then keeps its old look
        _log(f"radiation effects beside Aurelia not set up: {type(ex).__name__}: {ex}")
        return []

    def build() -> None:
        try:
            report = builder.build(root, radiation_only=True, log=lambda line: _log(f"build: {line}"))
            _log(f"build: {report.lines[-1] if report.lines else 'nothing done'}")
            if report.built or report.pending:
                told.append("Radiation's effects were built. Restart Borderlands 2 to see them.")
            elif report.failed:
                told.append("Radiation's effects could not be built: the console log has the details.")
        except Exception as ex:  # noqa: BLE001 - report, never take the game down
            _log(f"build failed: {type(ex).__name__}: {ex}")

    threading.Thread(target=build, daemon=True, name="radiation-fx-build").start()

    @hook("WillowGame.WillowGameViewportClient:Tick", Type.POST)
    def notice(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
        pc = get_pc() if told else None
        if pc is None or pc.GFxUIManager is None:
            return
        message = told.pop(0)
        try:
            from ui_utils import TrainingBox  # noqa: PLC0415 - the menu's own dependency

            TrainingBox(title="Radiation", message=message).show()
        except Exception as ex:  # noqa: BLE001
            _log(f"notice not shown ({type(ex).__name__}: {ex})")
        _log(f"notice: {message}")

    return [*radfx.HOOKS, notice]


ASIDE = stands_aside()
if ASIDE:
    _log("stands aside: the Aurelia mod is installed and has cryo in it. Switch Aurelia off (or remove it) to use this mod.")
    PARTS: dict[str, Any] = {"options": [], "hooks": _radiation_beside_aurelia(), "commands": [], "on_enable": None, "on_disable": None}
else:

    # In this order: a feature's modules register with the ones before it as they are imported (listeners on cryo's events, the particle
    # systems to dress, what goes into the save editor's data), and that is the order they then run in.
    from . import core  # isort: skip
    from . import cryo  # isort: skip
    from . import builder, install
    from .core import layout, saveedit, setup

    @hook("WillowGame.FrontendGFxMovie:Start", Type.POST)
    def on_main_menu(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
        # FrontendGFxMovie is both the main menu and the in-game pause menu.
        _log(f"v{__version__} loaded and hooked (main or pause menu opened).")
        _setup_now("main menu")

    def _setup_now(where: str) -> None:
        """CRYO_EARLY_SETUP: the one-time setup (Aurelia's roster ran it; this mod has none). Idempotent and cheap once done; the layout keeper also
        runs it right before a save is read, and a map's arrival covers a new character, which reads no save."""
        try:
            setup.run()
        except Exception as ex:  # noqa: BLE001
            _log(f"setup at the {where} failed: {type(ex).__name__}: {ex}")

    @hook(core.maps.ARRIVED, Type.POST)
    def _setup_on_arrival(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
        _setup_now("map arrival")

    @command("cryo", description="Cryo development commands.")
    def cryo_command(args: Namespace) -> None:
        if args.action == "status":
            _status()
        elif args.action == "layout":
            for lib, names in layout.current_layout().items():
                _log(f"  library {lib}: {names}")
        elif args.action == "cryo":
            for line in cryo.state() or ["no enemy is frozen or slowed"]:
                _log(f"  {line}")
        elif args.action == "saveedit":
            _log(f"  {saveedit.export('console')}")
        elif args.action == "build":
            tps = Path(args.folder) if args.folder else None
            if args.what in ("run", "adopt"):
                _log(install.start(adopt=args.what == "adopt", tps=tps))
            elif args.what == "uninstall":
                _log(install.uninstall())
            else:
                for line in builder.status(install.game_root(), tps):
                    _log(f"  {line}")

    cryo_command.add_argument(
        "action", nargs="?", default="status", choices=("status", "layout", "cryo", "build", "saveedit"),
        help="status: print mod, player and game state (the default). layout: the appended asset sublibraries, in order. cryo: frozen and "
             "slowed enemies. build: cryo's files built from your TPS install. saveedit: write the save editor's data for modded items now.",
    )
    cryo_command.add_argument(
        "what", nargs="?", default="status", choices=("status", "run", "adopt", "uninstall"),
        help="with build: run builds what is missing or outdated (a restart then picks up new files); adopt first moves aside files the "
             "build didn't write; uninstall removes what it built.",
    )
    cryo_command.add_argument("folder", nargs="?", default=None, help="with build: your Pre-Sequel folder, when it isn't found on its own")

    def _try(label: str, getter: Callable[[], Any]) -> None:
        try:
            _log(f"  {label}: {getter()}")
        except Exception as ex:  # noqa: BLE001 - diagnostics only, report and move on
            _log(f"  {label}: n/a ({type(ex).__name__}: {ex})")

    def _status() -> None:
        _log(f"status - mod v{__version__}")
        pc = get_pc(possibly_loading=True)
        if pc is None:
            _log("  no player controller (loading screen?)")
            return
        _try("player", lambda: pc.PlayerReplicationInfo.PlayerName)
        _try("pawn", lambda: str(pc.Pawn))
        _try("playthrough", lambda: pc.GetCurrentPlaythrough())
        _try("map", lambda: pc.WorldInfo.GetMapName(False))

    def _switched_off() -> bool:
        """Switched off in the MODS menu when the game last ran. The settings file says so; the SDK reads it only when the mod is built,
        further down, and the files are looked at before that."""
        settings = _read("bl3_weapons.json")
        return settings is not None and settings.get("enabled") is False

    # Before the game opens cryo's files: the rest of an uninstall, the files that waited for a restart, then a check (and a build when
    # something is missing or outdated) on a background thread. Switched off, the mod checks and builds nothing.
    install.at_load(check=not _switched_off())

    # The one-time setup, from every feature's steps. Within a phase they run in this order (core/setup.py); for the items that is the order
    # their sublibraries are appended in, so it stays as it is (cryo's come right after core's, as in Aurelia, so their indices are the same).
    setup.use(*core.STEPS, *cryo.STEPS, *install.STEPS)

    SECTIONS = (GroupedOption("Cryo", cryo.OPTIONS, description="The Pre-Sequel's element in Borderlands 2."),)

    def tell_enforcer(enabled: bool) -> None:
        """Enforcer (the Wilhelm character mod, which has a skill that freezes) asks "is the Cryo mod on?" by the name of a mod and, failing that, by the
        "enabled" of a `Cryo.json` in the settings folder. Cryo is part of BL3 Weapons now and no mod has that name, so that file says it, in step with
        this mod: on while it is on, off when it is switched off. (Other keys already in the file stay.)"""
        try:
            data = _read("Cryo.json") or {}
            if data.get("enabled") is enabled:
                return
            data["enabled"] = enabled
            (SETTINGS_DIR / "Cryo.json").write_text(json.dumps(data, indent=4), encoding="utf-8")
        except (OSError, ValueError) as ex:
            _log(f"Cryo.json (for Enforcer) not written: {ex}")

    def _on_enable() -> None:
        tell_enforcer(True)

    def _on_disable() -> None:
        """Switched off in the MODS menu. The SDK has just turned off every listed hook, the console command and the options' callbacks;
        here goes what those hooks kept going, so that from now on nothing of the mod runs: the ticks that are turned on by hand while there
        is work for them, and every enemy held frozen or slowed (the tick that thaws them is off now). What the mod built stays in the game
        until it is restarted: cryo's objects and the lists they are in."""
        waiting = 0
        freed = 0
        tell_enforcer(False)
        for label, step in (("materials", core.particles.stop), ("queued looks", core.later.stop), ("gun lists", cryo.guns.stop)):
            try:
                step()
            except Exception as ex:  # noqa: BLE001 - each on its own: one's fault never keeps the others on
                _log(f"switching off, {label}: {type(ex).__name__}: {ex}")
        try:
            waiting = layout.stop()
        except Exception as ex:  # noqa: BLE001
            _log(f"switching off, save records: {type(ex).__name__}: {ex}")
        try:
            freed = cryo.element.release_all()
        except Exception as ex:  # noqa: BLE001
            _log(f"switching off, frozen enemies: {type(ex).__name__}: {ex}")
        _log("switched off: none of the mod's hooks run from now on"
             + (f"; {freed} frozen or slowed enemies let go" if freed else "")
             + (f"; {waiting} save(s) get no layout record" if waiting else "")
             + ". Restart the game to take out what it built.")

    # What BL3 Weapons adds to its own mod (one entry in the MODS menu). Hooks on the same game function run in the order they are listed here
    # (the SDK keeps the order they were added in): core comes last (the layout keeper arranges a save's sublibraries after the setup has
    # appended them all).
    PARTS = {
        "options": [*SECTIONS, *install.OPTIONS],
        "hooks": [on_main_menu, _setup_on_arrival, *install.HOOKS, *cryo.HOOKS, *core.HOOKS],  # (radfx.HOOKS are in cryo.HOOKS)
        "commands": [cryo_command],
        "on_enable": _on_enable,
        "on_disable": _on_disable,
    }
