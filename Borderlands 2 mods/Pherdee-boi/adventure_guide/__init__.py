"""Adventure Guide: a sixth tab in the status menu that lists bosses and other loot sources with their drops.

The tab (after Badass Rank) is built from the game's own status menu movie at startup, in memory; nothing from the game is
stored in this mod. See the README for what the tab does and how to report a problem (adventure_guide.log).

Developer trigger: if a file named dev.flag exists in this folder, a Python file named exec.now.py dropped here runs inside the
game within a second (its say() output lands in adventure_guide.log). Releases ship without dev.flag.
"""
import faulthandler
import time
from pathlib import Path

import unrealsdk
from mods_base import SETTINGS_DIR, build_mod, get_pc, hook

HERE = Path(__file__).parent
LOG = (HERE if HERE.is_dir() else SETTINGS_DIR) / "adventure_guide.log"  # (inside a .sdkmod: the settings folder)
EXEC = HERE / "exec.now.py"
DEV = HERE / "dev.flag"
_last = 0.0
_t0 = time.monotonic()
_tab_checked = False
_hang_file = None  # dev only: where the Python stacks go when the game stops answering for 10 s

try:  # a fresh log per game session (the log is what testers send back)
    LOG.write_text("", encoding="utf-8")
except OSError:
    pass


def say(msg: str) -> None:
    unrealsdk.logging.info(f"[AdventureGuide] {msg}")
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


@hook("WillowGame.WillowGameViewportClient:Tick", hook_identifier="adventure_guide_exec")
def tick(*_: object) -> None:
    global _last, _tab_checked, _hang_file
    t = time.monotonic()
    if t - _last < 1.0:
        return
    _last = t
    if DEV.exists():  # a hung game cannot say where it hangs: this watchdog runs without the interpreter lock (faulthandler) and writes every Python stack
        try:
            if _hang_file is None:
                _hang_file = open(HERE / "hang.log", "a", encoding="utf-8")
            faulthandler.dump_traceback_later(10, repeat=False, file=_hang_file)
        except Exception:  # noqa: BLE001
            pass
    if not _tab_checked and t - _t0 > 8:  # the status menu movie must be patched before it is first opened
        _tab_checked = True
        try:
            guide.enable_tab()
        except Exception:  # noqa: BLE001
            import traceback

            say("enable_tab error: " + traceback.format_exc()[-400:])
    if not DEV.exists() or not EXEC.exists():
        return
    src = EXEC.read_text(encoding="utf-8")
    EXEC.unlink()
    try:
        exec(compile(src, "exec.py", "exec"), {**globals(), "pc": get_pc(possibly_loading=True)})
    except Exception:  # noqa: BLE001
        import traceback

        say("exec error: " + traceback.format_exc()[-800:])


try:
    from . import guide

    guide.install(say)
except Exception:  # noqa: BLE001
    import traceback

    say("guide install failed: " + traceback.format_exc()[-600:])

_mod = build_mod(
    name="Adventure Guide",
    description="A sixth status-menu tab: bosses, special enemies, vendors and world drops with every legendary and unique item they drop. R searches, Q changes the sort.",
    hooks=[tick],
    auto_enable=True,
)
try:
    # A mod the loader has never seen starts switched off until it is enabled in the mod menu. Switch ours on the first time it
    # runs (no settings file yet); once the player has used the menu the saved choice rules.
    if _mod.settings_file is not None and not _mod.settings_file.exists():
        _mod.enable()
        say("first run: the mod switched itself on")
except Exception:  # noqa: BLE001
    import traceback

    say("first-run enable failed: " + traceback.format_exc()[-300:])
