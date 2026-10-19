"""Cryo's game files, built from the player's own Borderlands: The Pre-Sequel (CRYO_BUILDER: the builder of the Aurelia mod, cut down to cryo).

Nothing of TPS ships with the mod: the converter runs on the player's machine, over their TPS files, and writes into their BL2 install:

| Output (BL2 install) | From (TPS) | Recipe |
|---|---|---|
| `WillowGame/CookedPCConsole/Crocus_IceFX_SF.upk` | TPS's `WillowGame/CookedPCConsole/Startup.upk` and `WillowGame.upk` (and, when the Baroness Pack is there, her slam burst) | `icefx` |
| `WillowGame/Localization/<language>/GD_AureliaTexts.<language>` | TPS's own `WillowGame/Localization/<language>/` (ten files) | `cryotexts` |

The names keep Aurelia's: the engine, the saves and the converter know the package as Crocus_IceFX, and a save names cryo's items by the packages
the mod makes. The Baroness Pack is NOT needed. If the Aurelia mod built these files already, its records are read at the first start
(`load_manifest`): a file that is still the one it wrote is taken over as it is.

Rules (as in the Aurelia builder):
- The engine finds files by name only if they exist when it starts, so a first build needs one restart.
- The manifest (`sdk_mods/settings/cryo_build.json`) records every file written: size, SHA-1, recipe, and the stamps of its sources and of the
  converter code it ran. A rebuild skips outputs whose record still matches.
- A file the manifest doesn't know is never overwritten: the build stops and names it. `adopt=True` moves such files aside first.
- Writes go to a temporary file, then replace the target; a target the running game holds open waits beside it (`<name>.cryo-new`) and
  `apply_pending()` swaps it in at the next start. An outdated output that comes out byte for byte as the file in place is left where it is.
- Every TPS source is checked against the SHA-1s of the tested install (`known_tps.json` beside the converter): a different TPS build gets a
  warning, not a stop.
- `uninstall()` removes the recorded files that still match their record, and the folders the build created, when empty.
- `decide()` is what a launch does: build what's missing or outdated, or say why not.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TPS_APP_ID = "261640"
TPS_MARKER = Path("WillowGame/CookedPCConsole/Startup.upk")      # TPS's base game: what cryo is cut from
TPS_EXE = Path("Binaries/Win32/BorderlandsPreSequel.exe")
COOKED = Path("WillowGame/CookedPCConsole")
LOCALIZATION = Path("WillowGame/Localization")
TEXT_LANGUAGES = ("DEU", "ESN", "FRA", "ITA", "JPN", "KOR", "RUS")   # the languages TPS has cryo's texts in, beside English
TEXT_RECIPES = ("cryotexts",)             # outputs the game reads by itself when asked: no restart, no check
MANIFEST = Path("sdk_mods/settings/cryo_build.json")
AURELIA_MANIFEST = Path("sdk_mods/settings/aurelia_build.json")     # read once, when this one is missing (see load_manifest)
REPLACED = Path("sdk_mods/settings/cryo_replaced")
PENDING = ".cryo-new"
TEMP = ".cryo-tmp"
STREAMING_PROFILE = "skills"
MANIFEST_VERSION = 1
KNOWN_TPS = "known_tps.json"          # beside the converter: the tested TPS install's source SHA-1s
AUTO = "auto"                         # manifest: False after an uninstall, until a build is asked for
UNINSTALL = "uninstall"               # manifest: files an uninstall couldn't remove yet (held open)

# The converter modules each recipe runs; their contents are part of the recipe's stamp, so a converter change
# rebuilds exactly the outputs it affects.
RECIPE_CODE = {
    "cryotexts": ("cryotexts.py",),
    "icefx": ("icefx.py", "upkextract.py", "upk.py", "staticmesh.py", "findexport.py",
              "texdump.py", "props.py"),      # texdump reads the cryo icon's sheet
    "radfx": ("radfx.py", "upkextract.py", "upk.py", "findexport.py", "texdump.py", "props.py"),   # the radiation element's particle systems, from BL2 alone
}
# Converter options per recipe (part of the stamp too). Her streaming package keeps its body's default head and
# skin slots: the build installs the head and skin packages they load (as the in-game-verified dev build did).
RECIPE_OPTIONS: dict[str, dict] = {}


# ---------------------------------------------------------------- the converter and TPS
class ConverterMissing(FileNotFoundError):
    """The mod's folder has no converter: an incomplete install (a copy of the repo's `src/aurelia`, or of only part
    of the release's `aurelia` folder). Nothing can be built or checked without it."""


def tools_dir() -> Path:
    """The converter: shipped inside the mod (`cryo/convert`) or the repo's `tools/` (development)."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "convert", here.parents[1] / "tools"):
        if (candidate / "icefx.py").is_file():
            return candidate
    raise ConverterMissing(f"the mod's folder is incomplete: {here / 'convert'} (the converter) is missing; the release "
                           "zip has it inside its aurelia folder")


def _converter() -> Any:
    path = str(tools_dir())
    if path not in sys.path:
        sys.path.insert(0, path)
    import icefx  # noqa: PLC0415 - found through tools_dir()
    return icefx


def radfx_tool() -> Any:
    """The converter's cutter of the radiation effects out of BL2's own Startup.upk."""
    path = str(tools_dir())
    if path not in sys.path:
        sys.path.insert(0, path)
    import radfx  # noqa: PLC0415 - found through tools_dir()
    return radfx


def texts_tool() -> Any:
    """The converter's picker of cryo's texts out of TPS's base localization files."""
    path = str(tools_dir())
    if path not in sys.path:
        sys.path.insert(0, path)
    import cryotexts  # noqa: PLC0415 - found through tools_dir()
    return cryotexts


def guid_tool() -> Any:
    """The converter's reader and writer of GUID caches (install.py: her packages' names for other players' games)."""
    path = str(tools_dir())
    if path not in sys.path:
        sys.path.insert(0, path)
    import guidcache  # noqa: PLC0415 - found through tools_dir()
    return guidcache


def blank_tool() -> Any:
    """The converter's writer of package files of blank objects (core/netobjects.py: objects of the mod's own that
    other players' games can name)."""
    path = str(tools_dir())
    if path not in sys.path:
        sys.path.insert(0, path)
    import blankpackage  # noqa: PLC0415 - found through tools_dir()
    return blankpackage


def _steam_libraries(near: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    # BL2's own library, derived from where the game runs (<library>/steamapps/common/Borderlands 2): needs no
    # registry, so it works under Proton/Wine, where the native Linux Steam isn't in the prefix's registry and the
    # lookups below find nothing. Its libraryfolders.vdf still lists every library, so a TPS in another one is found.
    if near is not None:
        try:
            here = near.resolve()
            if len(here.parents) >= 3 and here.parents[1].name.lower() == "steamapps":
                roots.append(here.parents[2])
        except OSError:
            pass
    try:
        import winreg  # noqa: PLC0415 - Windows only
        for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(Path(winreg.QueryValueEx(k, value)[0]))
            except OSError:
                pass
    except ImportError:
        pass
    libraries = list(roots)
    for root in roots:
        try:
            text = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        libraries += [Path(m.group(1).replace("\\\\", "\\")) for m in re.finditer(r'"path"\s+"([^"]+)"', text)]
    unique: dict[str, Path] = {}
    for lib in libraries:
        unique.setdefault(str(lib).lower().rstrip("\\/"), lib)
    return list(unique.values())


def _steam_candidates(near: Path | None = None) -> list[Path]:
    out = []
    for lib in _steam_libraries(near):
        installdir = "BorderlandsPreSequel"
        try:
            acf = (lib / "steamapps" / f"appmanifest_{TPS_APP_ID}.acf").read_text(encoding="utf-8", errors="replace")
            m = re.search(r'"installdir"\s+"([^"]+)"', acf)
            installdir = m.group(1) if m else installdir
        except OSError:
            pass
        out.append(lib / "steamapps" / "common" / installdir)
    return out


def _epic_candidates() -> list[Path]:
    """Every folder the Epic launcher installed a game into (its records: one `.item` per install). `find_tps()`
    looks for TPS's own files in each, so nothing depends on what the store calls the game."""
    base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic/EpicGamesLauncher/Data/Manifests"
    out: dict[str, Path] = {}
    try:
        items = sorted(base.glob("*.item"))
    except OSError:
        return []
    for item in items:
        try:
            where = json.loads(item.read_text(encoding="utf-8-sig")).get("InstallLocation")
        except (OSError, ValueError, AttributeError):
            continue
        if where and isinstance(where, str):
            out.setdefault(where.lower().rstrip("\\/"), Path(where))
    return list(out.values())


def _is(path: Path, kind: str) -> bool:
    try:
        return path.is_file() if kind == "file" else path.is_dir()
    except OSError:                       # a folder we may not look into
        return False


def _neighbours(bl2: Path | None) -> list[Path]:
    """The folders beside the BL2 install: the two games usually share a library, whatever installed them (and this
    needs neither the registry nor a launcher's records)."""
    if bl2 is None:
        return []
    try:
        here = bl2.resolve()
        return sorted(p for p in here.parent.iterdir() if p != here and _is(p, "dir"))
    except OSError:
        return []


def _given(path: Path) -> list[Path]:
    """A folder the player named, and — under Proton, where the mod runs on a Windows Python that sees Wine's drive
    map — the same path on `Z:` (Wine maps the Linux root there), so a native "/home/..." path still resolves."""
    s = str(path)
    if s[:1] in ("/", "\\") and not s.startswith("\\\\"):
        return [path, Path("Z:" + s)]
    return [path]


def find_tps(explicit: Path | None = None, near: Path | None = None) -> tuple[Path | None, str]:
    """TPS's install folder, and how it was found (or why not). In order: the folder given,
    `CRYO_TPS` (or `AURELIA_TPS`), Steam's libraries, the Epic launcher's installs, the folders beside `near` (the BL2 install). Each
    given folder is also tried on Wine's `Z:` drive, and Steam's libraries include BL2's own, so finding TPS needs no
    Windows registry (it is empty under Proton)."""
    candidates: list[tuple[Path, str]] = []
    if explicit:
        candidates += [(p, "given") for p in _given(explicit)]
    for variable in ("CRYO_TPS", "AURELIA_TPS"):
        if os.environ.get(variable):
            candidates += [(p, variable) for p in _given(Path(os.environ[variable]))]
    candidates += [(p, "Steam") for p in _steam_candidates(near)] + [(p, "Epic") for p in _epic_candidates()]
    candidates += [(p, "beside Borderlands 2") for p in _neighbours(near)]
    without_pack = []
    for path, how in candidates:
        if _is(path / TPS_MARKER, "file"):
            return path, how
        # Epic's list and the neighbours hold other games too: there only TPS's own program says "this is TPS".
        if _is(path / TPS_EXE, "file") or (how in ("given", "CRYO_TPS", "AURELIA_TPS", "Steam") and _is(path / "DLC", "dir")):
            without_pack.append(path)
    if without_pack:
        return None, f"TPS is at {without_pack[0]}, but its {TPS_MARKER.as_posix()} is missing"
    return None, ("TPS was not found (checked Steam, Epic and the folders beside Borderlands 2); give its folder with "
                  "cryo build run \"<folder>\" (on Linux, the Z: path Wine shows: Z:\\...\\BorderlandsPreSequel)")


# ---------------------------------------------------------------- the plan
@dataclass
class Target:
    out: str                             # relative to the BL2 root, forward slashes
    recipe: str
    sources: list[Path]                  # absolute
    make: Callable[[Path], str] = field(repr=False)   # writes the output to the given path, returns a note
    optional: bool = False               # an extra: a file of somebody else's in its way is left, and stops nothing


def _cryo_targets(bl2: Path, tps: Path) -> list[Target]:
    """Cryo's files: TPS's own frozen-enemy effects, ice explosions and cryo icon, cut from TPS's base packages (and, when the Baroness Pack is
    there, her cryo slam's burst, which is where TPS keeps it: left out otherwise); and cryo's texts in the game's other languages, picked out
    of TPS's base localization files, one file for each language this BL2 has a folder of and TPS has cryo's texts in (extras)."""
    icefx = _converter()
    startup, game, hers = tps / icefx.SOURCE, tps / icefx.ICON_SOURCE, tps / icefx.SLAM_SOURCE
    slam = hers.is_file()
    sources = [startup, game] + ([hers] if slam else [])
    targets = [Target(f"{COOKED.as_posix()}/{icefx.PACKAGE}", "icefx", sources,
                      lambda tmp: icefx.build(startup, tmp, game, hers if slam else None))]
    cryotexts = texts_tool()

    def texts(language: str) -> Callable[[Path], str]:
        return lambda tmp: cryotexts.build(tps, language, tmp)

    for language in TEXT_LANGUAGES:
        text_sources = cryotexts.sources(tps, language)
        if text_sources and (bl2 / LOCALIZATION / language).is_dir():
            targets.append(Target(f"{LOCALIZATION.as_posix()}/{language}/{cryotexts.NAME}.{language}", "cryotexts", text_sources,
                                  texts(language), optional=True))
    return targets


def _radfx_targets(bl2: Path) -> list[Target]:
    """The radiation effects' particle systems: cut from BL2's own Startup.upk, so they need no TPS."""
    radfx = radfx_tool()
    source, game = bl2 / radfx.SOURCE, bl2 / radfx.GAME_SOURCE
    return [Target(f"{COOKED.as_posix()}/{radfx.PACKAGE}", "radfx", [source, game], lambda tmp: radfx.build(source, tmp, game))]


def plan(bl2: Path, tps: Path | None, streaming: str = STREAMING_PROFILE) -> list[Target]:
    """Every output (cryo's need TPS; the radiation effects' don't)."""
    targets = _radfx_targets(bl2)
    if tps is not None:
        targets += _cryo_targets(bl2, tps)
    return targets


# ---------------------------------------------------------------- stamps and the manifest
def _sha1(path: Path) -> str:
    h = hashlib.sha1()  # noqa: S324 - a content fingerprint, not security
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


_code_stamps: dict[str, str] = {}


def _code_stamp(recipe: str) -> str:
    if recipe not in _code_stamps:
        h = hashlib.sha1((recipe + repr(sorted(RECIPE_OPTIONS.get(recipe, {}).items()))).encode())  # noqa: S324
        for name in RECIPE_CODE[recipe]:
            h.update((tools_dir() / name).read_bytes())
        _code_stamps[recipe] = h.hexdigest()
    return _code_stamps[recipe]


def _source_stamps(target: Target) -> dict[str, list[int]]:
    out = {}
    for src in target.sources:
        st = src.stat()
        out[src.as_posix()] = [st.st_size, st.st_mtime_ns]
    return out


def known_sources() -> dict[str, str]:
    """The tested TPS install's sources: path under TPS -> SHA-1 (empty when the record is missing)."""
    try:
        return dict(json.loads((tools_dir() / KNOWN_TPS).read_text(encoding="utf-8"))["files"])
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _tps_sources(tps: Path, targets: list[Target]) -> list[Path]:
    """The targets' sources that come from TPS (the others are BL2's own files) and are converted: a text file is
    copied as it is, whatever TPS it is from, and is not checked against the tested one."""
    out = []
    for target in targets:
        if target.recipe in TEXT_RECIPES:
            continue
        out += [src for src in target.sources if src.is_relative_to(tps) and src not in out]
    return out


def check_sources(tps: Path, targets: list[Target]) -> list[str]:
    """TPS sources that differ from the tested install's. Warnings: a different TPS build may convert wrongly."""
    known = known_sources()
    if not known:
        return ["no record of the tested TPS files beside the converter; sources not checked"]
    out = []
    for src in _tps_sources(tps, targets):
        rel = src.relative_to(tps).as_posix()
        if rel not in known:
            out.append(f"not in the tested TPS: {rel}")
        elif _sha1(src) != known[rel]:
            out.append(f"differs from the tested TPS: {rel}")
    return out


def _seed_from_aurelia(bl2: Path) -> dict[str, Any] | None:
    """The Aurelia mod's record of the files that are cryo's (a first start after Aurelia): each one is taken over as the file Aurelia wrote, so
    it is neither "foreign" nor rebuilt when it is still byte for byte what was recorded. Only the records of this build's recipes."""
    try:
        old = json.loads((bl2 / AURELIA_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if old.get("version") != MANIFEST_VERSION:
        return None
    files = {rel: dict(record) for rel, record in old.get("files", {}).items() if record.get("recipe") in RECIPE_CODE}
    if not files:
        return None
    seeded = {"version": MANIFEST_VERSION, "files": files, "dirs": [], "seeded_from": "aurelia"}
    if old.get("tps"):
        seeded["tps"] = old["tps"]
    return seeded


def load_manifest(bl2: Path) -> dict[str, Any]:
    try:
        data = json.loads((bl2 / MANIFEST).read_text(encoding="utf-8"))
        if data.get("version") == MANIFEST_VERSION:
            return data
    except (OSError, ValueError):
        pass
    seeded = None if (bl2 / MANIFEST).exists() else _seed_from_aurelia(bl2)
    if seeded is not None:
        _save_manifest(bl2, seeded)
        return seeded
    return {"version": MANIFEST_VERSION, "files": {}, "dirs": []}


def _again(action: Callable[[], Any], tries: int = 4, pause: float = 0.15) -> Any:
    """Run a rename or a delete, and again a little later when Windows refuses it: a virus scanner or the indexer may
    hold a file for a moment right after it was written. The last refusal is raised."""
    for attempt in range(tries):
        try:
            return action()
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(pause * (attempt + 1))
    return None


def _save_manifest(bl2: Path, manifest: dict[str, Any]) -> None:
    path = bl2 / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json" + TEMP)
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    _again(lambda: tmp.replace(path))


def _matches(path: Path, record: dict[str, Any] | None, deep: bool = False) -> bool:
    """The file on disk is the one the record describes (size and time; SHA-1 too when `deep`)."""
    if record is None or not path.is_file():
        return False
    st = path.stat()
    if st.st_size != record["size"]:
        return False
    if st.st_mtime_ns == record.get("mtime_ns") and not deep:
        return True
    return _sha1(path) == record["sha1"]


def state(bl2: Path, target: Target, manifest: dict[str, Any]) -> str:
    """up to date | missing | outdated | foreign (a file we didn't write) | pending (waits for a restart)"""
    record = manifest["files"].get(target.out)
    path = bl2 / target.out
    if record is not None and record.get("pending"):
        if path.with_name(path.name + PENDING).is_file():
            return "pending"
        return "outdated" if path.exists() else "missing"      # the waiting file is gone: it is built again
    if not path.exists():
        return "missing"
    if record is None:
        return "foreign"
    if not _matches(path, record):
        return "foreign"                      # changed by someone else since we wrote it
    try:
        fresh = record.get("code") == _code_stamp(target.recipe) and record.get("sources") == _source_stamps(target)
    except OSError:
        fresh = False
    return "up to date" if fresh and record.get("recipe") == target.recipe else "outdated"


# ---------------------------------------------------------------- build, pending swaps, uninstall
@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    built: int = 0
    pending: int = 0
    same: int = 0                        # made again and found byte for byte the file in place: nothing was replaced
    texts: int = 0                       # of `built`: text files (her texts in the game's other languages)
    blocked: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)      # failed output -> why
    warnings: list[str] = field(default_factory=list)

    def add(self, line: str) -> None:
        self.lines.append(line)


def _discard(tmp: Path) -> None:
    """Remove a temporary file. One that can't be removed now is in nobody's way: the next build writes over it."""
    try:
        _again(lambda: tmp.unlink(missing_ok=True))
    except OSError:
        pass


def _place(tmp: Path, path: Path) -> bool:
    """Move a finished output into place. False: the running game holds the file it replaces, so the new one waits
    beside it for the next start (`apply_pending`)."""
    if not path.exists():
        _again(lambda: tmp.replace(path))             # nothing to replace: only a moment's hold on the new file
        return True                                   # itself can be in the way
    try:
        _again(lambda: tmp.replace(path), tries=2)
        return True
    except PermissionError:
        _again(lambda: tmp.replace(path.with_name(path.name + PENDING)))
        return False


def _created_dirs(bl2: Path, rel: str) -> list[str]:
    """The folders on the way to `rel` that don't exist yet (so uninstall can remove them)."""
    out, parent = [], (bl2 / rel).parent
    while not parent.exists() and parent != bl2:
        out.append(parent.relative_to(bl2).as_posix())
        parent = parent.parent
    return out


def build(bl2: Path, tps: Path | None = None, adopt: bool = False, streaming: str = STREAMING_PROFILE,
          log: Callable[[str], None] = print) -> Report:
    report = Report()
    tps, how = find_tps(tps or _recorded_tps(bl2), bl2)
    report.add(f"TPS: {tps} ({how})" if tps is not None else f"{how}; only what needs no TPS is built")
    manifest = load_manifest(bl2)
    targets = plan(bl2, tps, streaming)
    states = {t.out: state(bl2, t, manifest) for t in targets}
    foreign = [t.out for t in targets if states[t.out] == "foreign" and not t.optional]
    if foreign and not adopt:
        report.blocked += foreign
        report.add(f"stopped: {len(foreign)} file(s) the build didn't write are in the way (adopt moves them aside): "
                   f"{foreign[:4]}{' ...' if len(foreign) > 4 else ''}")
        return report
    todo = [t for t in targets if states[t.out] not in ("up to date", "pending")]
    report.warnings += check_sources(tps, [t for t in todo if t.recipe != "radfx"]) if tps is not None and todo else []
    for line in report.warnings[:6]:
        report.add(f"warning: {line}")
        log(f"warning: {line}")
    if len(report.warnings) > 6:
        report.add(f"warning: ... and {len(report.warnings) - 6} more")
    manifest[AUTO] = True                             # a build was asked for: launches may build again
    manifest.pop(UNINSTALL, None)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    for target in targets:
        st = states[target.out]
        if st in ("up to date", "pending"):
            continue
        path = bl2 / target.out
        if st == "foreign" and target.optional:
            report.add(f"left as it is (not the build's file): {target.out}")
            continue
        if st == "foreign":
            aside = bl2 / REPLACED / stamp / target.out
            aside.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(aside))
            report.add(f"moved aside: {target.out} -> {aside.relative_to(bl2).as_posix()}")
        for d in _created_dirs(bl2, target.out):
            if d not in manifest["dirs"]:
                manifest["dirs"].append(d)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + TEMP)
        t0 = time.monotonic()
        try:
            note = target.make(tmp)
            record = {"recipe": target.recipe, "code": _code_stamp(target.recipe), "sources": _source_stamps(target),
                      "size": tmp.stat().st_size, "sha1": _sha1(tmp)}
            old = manifest["files"].get(target.out)
            if (st == "outdated" and old is not None and not old.get("pending")
                    and (old.get("size"), old.get("sha1")) == (record["size"], record["sha1"])):
                # The same bytes as the file in place (the converter changed, this output didn't): the file stays,
                # and nothing has to wait for a restart because the game holds it.
                _discard(tmp)
                record["mtime_ns"] = path.stat().st_mtime_ns
                report.same += 1
                done = "unchanged"
            elif _place(tmp, path):
                record["mtime_ns"] = path.stat().st_mtime_ns
                report.built += 1
                report.texts += target.recipe in TEXT_RECIPES
                done = "built"
            else:
                record["pending"] = True
                report.pending += 1
                done = "waits for a restart"
        except Exception as ex:  # noqa: BLE001 - one bad output must not stop the rest
            _discard(tmp)
            report.failed.append(target.out)
            report.errors[target.out] = f"{type(ex).__name__}: {ex}"
            line = f"FAILED: {target.out} ({target.recipe}): {type(ex).__name__}: {ex}"
            report.add(line)
            log(line)
            continue
        manifest["files"][target.out] = record
        _save_manifest(bl2, manifest)                 # after every file: an interrupted build loses nothing
        line = f"{done}: {target.out} ({target.recipe}; {note}; {time.monotonic() - t0:.1f}s)"
        report.add(line)
        log(line)
    if tps is not None:
        manifest["tps"] = tps.as_posix()
    _save_manifest(bl2, manifest)
    report.add(f"{report.built} built, {report.pending} waiting for a restart, {report.same} unchanged, "
               f"{len(report.failed)} failed, {sum(s == 'up to date' for s in states.values())} already up to date")
    return report


def apply_pending(bl2: Path) -> list[str]:
    """At the start of a session, before the game opens our files: swap in outputs that waited for a restart."""
    manifest = load_manifest(bl2)
    done = []
    for rel, record in manifest["files"].items():
        if not record.get("pending"):
            continue
        path = bl2 / rel
        new = path.with_name(path.name + PENDING)
        if not new.is_file():
            continue
        try:
            _again(lambda new=new, path=path: new.replace(path), tries=2)
        except OSError:                               # still held: it waits for another start
            continue
        record.pop("pending")
        record["mtime_ns"] = path.stat().st_mtime_ns
        done.append(rel)
    if done:
        _save_manifest(bl2, manifest)
    return done


def uninstall(bl2: Path) -> list[str]:
    """Remove what the build wrote: recorded files that still match their record, then the folders it created. A file
    the running game holds open stays recorded for `finish_uninstall()` at the next start. Launches stop building
    automatically until a build is asked for again."""
    manifest = load_manifest(bl2)
    lines, kept, held = [], {}, {}
    for rel, record in sorted(manifest["files"].items()):
        path = bl2 / rel
        try:
            for extra in (path.with_name(path.name + PENDING), path.with_name(path.name + TEMP)):
                if extra.is_file():
                    extra.unlink()
            if not path.exists():
                continue
            if not _matches(path, record, deep=True):
                kept[rel] = record
                lines.append(f"left in place (changed since the build): {rel}")
                continue
            path.unlink()
            lines.append(f"removed {rel}")
        except PermissionError:                       # the running game holds it open
            held[rel] = record
            lines.append(f"in use; removed at the next start: {rel}")
    for rel in sorted(manifest["dirs"], key=lambda d: d.count("/"), reverse=True):
        try:
            (bl2 / rel).rmdir()
            lines.append(f"removed folder {rel}")
        except OSError:
            pass
    manifest["files"] = {**kept, **held}
    manifest["dirs"] = [d for d in manifest["dirs"] if (bl2 / d).exists()]
    manifest[AUTO] = False
    if held:
        manifest[UNINSTALL] = sorted(held)
    else:
        manifest.pop(UNINSTALL, None)
    _save_manifest(bl2, manifest)
    return lines


def finish_uninstall(bl2: Path) -> list[str]:
    """At the start of a session, before the game opens our files: the rest of an uninstall run in game."""
    if not load_manifest(bl2).get(UNINSTALL):
        return []
    return uninstall(bl2)


def _recorded_tps(bl2: Path) -> Path | None:
    """The TPS folder the last build used (it may have been given by hand)."""
    recorded = load_manifest(bl2).get("tps")
    return Path(recorded) if recorded else None


def _all_built(bl2: Path, manifest: dict[str, Any]) -> bool:
    """Every recorded output is on disk as written (without TPS, nothing can say whether one is outdated)."""
    files = manifest["files"]
    return bool(files) and all(_matches(bl2 / rel, record) for rel, record in files.items()
                               if not record.get("pending"))


def decide(bl2: Path, tps: Path | None = None, streaming: str = STREAMING_PROFILE) -> tuple[str, str]:
    """What a launch does: ("build", why) | ("ready", "") | ("restart", why) | ("blocked", why) | ("no tps", why) |
    ("off", why). Reads only (sizes, times and the converter's own files)."""
    manifest = load_manifest(bl2)
    if manifest.get(AUTO) is False:
        return "off", "cryo's files were uninstalled; automatic builds stay off until a build is asked for"
    found, how = find_tps(tps or _recorded_tps(bl2), bl2)
    if found is None:
        radfx_todo = [t for t in _radfx_targets(bl2) if state(bl2, t, manifest) in ("missing", "outdated")]
        if radfx_todo:
            return "build", f"the radiation effects are {'missing' if state(bl2, radfx_todo[0], manifest) == 'missing' else 'outdated'} (no TPS needed)"
        if _all_built(bl2, manifest):
            return "ready", f"{how}; cryo's files stay as built"
        return "no tps", how
    targets = plan(bl2, found, streaming)
    states = [state(bl2, target, manifest) for target in targets]
    foreign = sum(1 for target, st in zip(targets, states) if st == "foreign" and not target.optional)
    todo = states.count("missing") + states.count("outdated")
    if foreign:
        return "blocked", f"{foreign} file(s) the build didn't write are in the way"
    if todo:
        return "build", f"{states.count('missing')} missing, {states.count('outdated')} outdated"
    if "pending" in states:
        return "restart", f"{states.count('pending')} file(s) wait for a restart"
    return "ready", ""


def status(bl2: Path, tps: Path | None = None, streaming: str = STREAMING_PROFILE) -> list[str]:
    found, how = find_tps(tps or _recorded_tps(bl2), bl2)       # as a build would look for it
    lines = [f"TPS: {found} ({how})" if found else how]
    if found is None:
        return lines
    manifest = load_manifest(bl2)
    counts: dict[str, list[str]] = {}
    try:
        targets = plan(bl2, found, streaming)
    except ConverterMissing as ex:
        return [*lines, str(ex)]
    for target in targets:
        counts.setdefault(state(bl2, target, manifest), []).append(target.out)
    for st, outs in sorted(counts.items()):
        lines.append(f"{st}: {len(outs)}" + (f"  {outs[:3]}{' ...' if len(outs) > 3 else ''}" if st != "up to date" else ""))
    return lines
