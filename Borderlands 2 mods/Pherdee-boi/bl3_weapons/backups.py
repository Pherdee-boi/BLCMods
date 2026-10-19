"""Save backups, once per session at the main menu (learned from Ribs Arsenal's save_backups.py).

The mods here change what a save holds (guns are written in their valid form and given their parts back by remembered ids: radiation, HeartShatter,
Hellwalker, the cryo guns), so before anything of that runs the player's saves and the mods' own records are copied into
sdk_mods/_bl3w_backups/<time>/. A snapshot is complete only when its manifest.json exists, which is written last, after every copy was checked against
its SHA-256. A session whose files are all the same as the newest snapshot's makes no new one; the newest KEEP complete snapshots are kept, nothing
else in that folder is touched. To get a character back: close the game, copy its .sav (and .sav.bak) from the snapshot's SaveData folder over the
one in Documents/My Games/Borderlands 2/WillowGame/SaveData/<account>/, and the settings json files over those in sdk_mods/settings.
"""

import hashlib
import json
import os
import shutil
import time
import traceback
from pathlib import Path

from mods_base import SETTINGS_DIR, hook
from unrealsdk.hooks import Type

from .core import log

KEEP = 10
FOLDER = SETTINGS_DIR.parent / "_bl3w_backups"
RECORDS = ("bl3_weapons.json", "hellwalker.json", "cryo.json", "cryo_layouts.json")  # the mods' own records, in sdk_mods/settings
_done = False


def _documents() -> Path:
    try:
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            return Path(os.path.expandvars(winreg.QueryValueEx(key, "Personal")[0]))
    except (ImportError, OSError):
        return Path.home() / "Documents"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sources() -> list[tuple[Path, Path]]:
    """(file, where it goes inside a snapshot): every save, bak and profile, and the mods' records."""
    save_root = _documents() / "My Games" / "Borderlands 2" / "WillowGame" / "SaveData"
    found = [(p, Path("SaveData") / p.relative_to(save_root)) for p in save_root.rglob("*") if p.is_file() and p.suffix in (".sav", ".bak", ".bin")]
    found += [(SETTINGS_DIR / name, Path("settings") / name) for name in RECORDS if (SETTINGS_DIR / name).is_file()]
    return found


def _newest() -> dict | None:
    for folder in sorted((d for d in FOLDER.iterdir() if d.is_dir()), reverse=True) if FOLDER.is_dir() else ():
        try:
            return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["files"]
        except (OSError, ValueError, KeyError):
            continue  # no manifest: not a complete snapshot
    return None


def snapshot() -> str:
    """Make a snapshot unless nothing changed since the newest one. Returns a line for the log."""
    sources = _sources()
    if not sources:
        return "no saves found: nothing to back up"
    hashes = {rel.as_posix(): _sha(src) for src, rel in sources}
    if _newest() == hashes:
        return f"saves unchanged since the newest snapshot ({len(hashes)} files)"
    target = FOLDER / time.strftime("%Y%m%d_%H%M%S")
    for src, rel in sources:
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target / rel)
        if _sha(target / rel) != hashes[rel.as_posix()]:
            raise OSError(f"copy of {rel} differs from the original")
    (target / "manifest.json").write_text(json.dumps({"files": hashes}, indent=1), encoding="utf-8")
    complete = sorted(d for d in FOLDER.iterdir() if d.is_dir() and (d / "manifest.json").is_file())
    for old in complete[:-KEEP]:
        shutil.rmtree(old)
    return f"snapshot {target.name}: {len(hashes)} files"


@hook("WillowGame.FrontendGFxMovie:Start", Type.POST, hook_identifier="bl3w_backups_menu")
def on_menu(*_: object) -> None:
    global _done
    if _done:
        return
    _done = True  # one try a session: a failure is logged, never retried while the player plays
    try:
        log("backups: " + snapshot())
    except Exception:  # noqa: BLE001 - a failed backup must not stop the game, but must be seen
        log("backups FAILED (play on with care): " + traceback.format_exc()[-300:])


HOOKS = [on_menu]
