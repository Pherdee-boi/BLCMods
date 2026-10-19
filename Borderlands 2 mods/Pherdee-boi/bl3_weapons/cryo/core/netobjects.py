"""Objects of the mod's own that other players' games can name (co-op; RESEARCH.md §6.73).

Two games name an object to each other as a package and a place in it. Only an object that came out of a package file
has a place: one constructed at run time has none, and a reference to it arrives in the other game as None. That was
the lot of her class definition up to 0.4.20: a player who joined as Aurelia asked the host for class None and got no
body, and the host's Aurelia was a name tag and a blank icon on everyone else's screen.

So such objects come out of a file. `load` writes a small package file of blank objects (the converter's
blankpackage.py: names, classes and places, no values and nothing of any game file), loads it, and keeps what came
out of it; the mod then fills those objects as it filled the ones it constructed (`fill` copies a template's values,
which is what constructing from a template did for them). Every game with the same list writes the same file, so the
places agree between players; the package's GUID is made from the list, so another list is another package.

The file lives in the mod's settings folder and is loaded by its path, as her GUID cache is. Its package must be in
that cache too (install.py lists `files()`): a joining game looks every package of the host's up there by name.

**No blank object may be left in the game.** The game picks up some kinds of object by itself once they are loaded
(a name id goes into the list of characters) and reads through their references: her name id, loaded blank at the
start of the game and filled twelve seconds later, took the dev copy down in between (2026-10-06). So a package's
objects can come in several files (`only`), as the game's own packages do, and a file is loaded at the moment its
objects are filled, in the same call.

When a file can't be made or loaded, `load` returns None and the caller constructs its objects as before: the game
plays as it did, and those objects have no place (`placed`). A file named `aurelia_coop.off` in the settings folder
does the same on purpose.

**The other way round, for everything else the mod constructs** (0.5.1; `identify`, `place`). The mod makes some
230 more objects at run time that a game is told of: the cryo parts of guns and gear, her legendaries, the cryo
damage types and status, particle systems and materials. They are made and filled where they always were; nothing
about that changes. What changes is their packages: one file that holds only packages (`Aurelia_Places.upk`:
their names, a GUID each, a count of places, no object) is loaded at the very start of the setup, before any of
them is made, so the packages come out of a file and the mod's modules find them there (each looks for its package
before it makes one). The order matters: a package made first is taken for the file's when the file is loaded over
it, and gets its GUID and its places, but the game does not list it for other games then (measured: none of
twelve was in a host's list; `adopted` names any that existed too early). Nor does it list a package before the
engine itself has put an object into one of its places, so the file carries one object of its own for each: an
empty inner package in the last place. Then each object named in core/placelist.py gets its place: its own net
index, and its slot in its package's list of network objects, which the engine made when the file gave the
package its count. Those two are what the engine itself sets for an object out of a file; it has no call for it
that script or the SDK can reach, so the slot is written in memory. Before anything is written the layout is tried
on an object of the game's own (`_layout_holds`): if the game's memory is not laid out as measured, nothing is
loaded or written and the objects stay without a place, as up to 0.5.0.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base import SETTINGS_DIR
from unrealsdk.unreal import UObject

_RF_ROOTSET = 0x4000
Spec = tuple[str, str, str]             # the class's package, the class, the object's name

# The converter's blankpackage module, when asked for: set by the mod's top (install.py), which knows where the
# converter is. None in a shell, where nothing is loaded anyway.
tool: Callable[[], Any] | None = None

OFF = SETTINGS_DIR / "cryo_coop.off"  # there: nothing is loaded, everything is constructed as up to 0.4.20

_files: dict[str, Path] = {}            # file stem -> the file, once loaded this session
_packages: set[str] = set()             # the packages those files made
_said: set[str] = set()


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] co-op: {msg}")


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _root(obj: UObject) -> None:
    flags = int(obj.ObjectFlags)
    if not flags & _RF_ROOTSET:
        obj.ObjectFlags = flags | _RF_ROOTSET


def file_of(stem: str) -> Path:
    return SETTINGS_DIR / f"{stem}.upk"


def files() -> list[Path]:
    """The files loaded this session: their packages belong in her GUID cache."""
    return sorted(set(_files.values()))


def placed(obj: UObject | None) -> bool:
    """The object has a place in its package: another player's game can be told of it."""
    return obj is not None and int(obj.NetIndex) >= 0


def load(stem: str, package: str, objects: Sequence[Spec], only: Sequence[str] | None = None) -> UObject | None:
    """The package `package`, numbered for one object per entry of `objects`, with those named in `only` (all, when
    None) now in it, blank, out of the file `<stem>.upk` in the mod's settings folder (written when it isn't there
    or isn't this list's). FILL THEM BEFORE RETURNING TO THE GAME (see above). The package and the objects are kept
    for the session. None when it can't be had, or when the package is in the game already without a file behind it
    (then nothing is loaded over it: that would blank objects in use)."""
    if stem in _files:
        return _find("Package", package)
    if OFF.is_file():
        _once("off", f"{OFF.name} is there: nothing is loaded from files, and the mod's objects have no place")
        return None
    if package not in _packages and _find("Package", package) is not None:
        _once(package, f"{package} was made before its file could be loaded: its objects have no place")
        return None
    try:
        if tool is None:
            raise RuntimeError("the converter isn't known here")
        held = [(place, spec) for place, spec in enumerate(objects) if only is None or spec[2] in only]
        data = tool().build(package, list(objects), stem, None if only is None else list(only))
        target = file_of(stem)
        if not target.is_file() or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
            _log(f"{target.name} made: {len(held)} of the {len(objects)} objects of {package}")
        unrealsdk.load_package(str(target))
        pkg = _find("Package", package)
        if pkg is None:
            raise RuntimeError(f"{target.name} loaded, and {package} isn't in the game")
        for obj in (pkg, _find("Package", stem)):
            if obj is not None:
                _root(obj)
        wrong = []
        for place, (_class_package, cls, name) in held:
            obj = _find(cls, f"{package}.{name}")
            if obj is None or int(obj.NetIndex) != place:
                wrong.append(name)
            if obj is not None:
                _root(obj)
        _files[stem] = target
        _packages.add(package)
        _log(f"{target.name}: {len(held) - len(wrong)} of its {len(held)} objects have their place in {package}"
             + (f" (not {', '.join(wrong)})" if wrong else ""))
        return pkg
    except Exception as ex:  # noqa: BLE001 - without it the game plays as before; co-op is what is lost
        _once(stem, f"{stem}.upk could not be loaded ({type(ex).__name__}: {ex}): its objects have no place")
        return None


# ---------------------------------------------------------------------------------------------- places, afterwards
PLACES_FILE = "Aurelia_Places"
# A package object in the game's memory (BL2, 32-bit; RESEARCH.md §6.69): after the 0x3C bytes every object has, its
# list of network objects (a pointer and a count) and the count of those in it.
_SLOTS_AT = 0x3C + 0x34
_COUNT_AT = 0x3C + 0x40
_layout: list[bool | None] = [None]
adopted: list[str] = []                 # packages that were in the game before their identity file: not listed for others


def _slots(pkg: UObject) -> tuple[int, int]:
    at = pkg._get_address()
    return ctypes.c_uint32.from_address(at + _SLOTS_AT).value, ctypes.c_int32.from_address(at + _SLOTS_AT + 4).value


def _layout_holds() -> bool:
    """The game's memory is laid out as measured: the game's own first class sits in its package's slot for its own
    net index. Asked once; nothing is ever written when it doesn't hold."""
    if _layout[0] is None:
        try:
            cls = unrealsdk.find_class("Object")
            place = int(cls.NetIndex)
            data, count = _slots(cls.Outer)
            _layout[0] = bool(data) and 0 <= place < count and ctypes.c_uint32.from_address(data + 4 * place).value == cls._get_address()
        except Exception:  # noqa: BLE001 - not as measured
            _layout[0] = False
        if not _layout[0]:
            _log("the game's memory isn't laid out as measured: the mod's own objects get no place (co-op items stay partial)")
    return bool(_layout[0])


def identify(packages: Mapping[str, Sequence[str]]) -> bool:
    """The mod's run-time packages made packages other games can name: one file holding them, each with a GUID, a
    place for each object of its list and one more for the file's own anchor, loaded before the mod's modules make
    them (they find them then). True when that file is loaded."""
    if PLACES_FILE in _files:
        return True
    if OFF.is_file() or not _layout_holds():
        return False
    try:
        if tool is None:
            raise RuntimeError("the converter isn't known here")
        data = tool().identities({name: list(paths) for name, paths in packages.items()}, PLACES_FILE)
        target = file_of(PLACES_FILE)
        if not target.is_file() or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
            _log(f"{target.name} made: {len(packages)} packages")
        adopted[:] = [name for name in packages if _find("Package", name) is not None]
        unrealsdk.load_package(str(target))
        wrong = []
        for name, paths in packages.items():
            pkg = _find("Package", name)
            if pkg is None or _slots(pkg)[1] != len(paths) + 1:      # its list, and the file's own anchor after it
                wrong.append(name)
            else:
                _root(pkg)
                anchor = _find("Package", f"{name}.{tool().ANCHOR}")
                if anchor is not None:
                    _root(anchor)
        if wrong:
            raise RuntimeError(f"not as the file says: {wrong}")
        if adopted:
            _log(f"{len(adopted)} of the mod's packages were made before their identity file: other games aren't told of "
                 f"what is in them ({', '.join(adopted)})")
        root = _find("Package", PLACES_FILE)
        if root is not None:
            _root(root)
        _files[PLACES_FILE] = target
        _packages.update(packages)
        return True
    except Exception as ex:  # noqa: BLE001 - without it the game plays as before; co-op items stay partial
        _once(PLACES_FILE, f"{PLACES_FILE}.upk could not be loaded ({type(ex).__name__}: {ex}): the mod's own objects have no place")
        return False


def place(packages: Mapping[str, Sequence[str]]) -> tuple[int, int]:
    """Each listed object that is in the game and has no place yet is given its own: (how many now, how many of the
    list are not in the game). Objects made later get theirs at the next call."""
    if not identify(packages):
        return 0, 0
    given = absent = 0
    for name, paths in packages.items():
        pkg = _find("Package", name)
        if pkg is None:
            absent += len(paths)
            continue
        data, count = _slots(pkg)
        if not data or count != len(paths) + 1:
            continue
        for index, path in enumerate(paths):
            obj = _find("Object", f"{name}.{path}")
            if obj is None:
                absent += 1
                continue
            slot = ctypes.c_uint32.from_address(data + 4 * index)
            if int(obj.NetIndex) != -1 or slot.value:
                continue                                    # it has its place (or the slot is taken: left alone)
            slot.value = obj._get_address()
            obj.NetIndex = index
            ctypes.c_int32.from_address(pkg._get_address() + _COUNT_AT).value += 1
            _root(obj)                                      # a slot must never outlive its object
            given += 1
    return given, absent


def fill(blank: UObject, template: UObject) -> list[str]:
    """Every value of `template` copied onto `blank`, an object of its class: what constructing from that template
    gave these objects (measured for the roster's classes: value for value the same, 6.73). The object's own identity
    (its name, owner, flags, place) is left alone. Returns the names of the fields that couldn't be copied."""
    left = []
    for prop in template.Class._properties():
        if prop.Outer is None or prop.Outer.Name == "Object":
            continue
        try:
            setattr(blank, prop.Name, getattr(template, prop.Name))
        except Exception as ex:  # noqa: BLE001 - reported by the caller: a field left at its default
            left.append(f"{prop.Name} ({type(ex).__name__})")
    return left


def _once(key: str, msg: str) -> None:
    if key not in _said:
        _said.add(key)
        _log(msg)


def status() -> list[str]:
    return [f"{path.name} is loaded" for _stem, path in sorted(_files.items())] or ["no file of blank objects is loaded"]
