"""A package file of blank objects: named objects of given classes with no values set, inside one package that has a
network identity (co-op; RESEARCH.md §6.73).

Two games name an object to each other as a package and a place in it. Only an object that came out of a package
file has such a place: one constructed at run time has none, and a reference to it arrives on the other game as
None. So the objects the mod used to construct and fill at run time, and that other players' games must be able to
name (her class definition, her name and class ids), come out of a file instead, blank, and are filled as before.

The file is laid out as the game's own cooked files are (read from CD_Mechro_Head_Default_SF.upk): the objects sit
in a package that is a forced export of the file, with its own GUID and its own count of network objects, and each
object's data starts with its place in that package. Nothing here is taken from a game file: the file is tables and
twelve bytes per object.

As in the game's files, one package's objects may be spread over several files (GD_TulipPackageDef is in three):
each file names the whole package, its GUID and its count, and carries some of its objects (`only`). The first
file loaded makes the package; the others' objects take their places in it. That lets an object stay out of the
game until it can be filled: the game picks up some kinds of object by itself as soon as they are loaded, and reads
through a blank one's empty references (her name id, 2026-10-06).

From a shell:
    python tools/cli.py blankpackage build <out.upk> <package> <Class.Package:ClassName:ObjectName> [...] [--only Name ...]
    python tools/cli.py blankpackage read <file.upk> [...]         what a file holds, as the engine will number it
"""

from __future__ import annotations

import hashlib
import struct
import zlib

from upk import Summary, w_fstring

NAME_FLAGS = 0x0007001000000000         # every name's flags in the game's cooked files
PACKAGE_OBJECT_FLAGS = 0x0007000400000000   # a package inside a cooked file: public, loaded for client, server and editor
# An object of the game's data, as its own definitions are flagged (GD_TulipPackageDef.PackageDef_Tulip), without the
# flag that makes the engine look for the object's texts in a localisation file of the package's name.
OBJECT_FLAGS = 0x000F000500000400
FILE_FLAGS = 0x80880009                 # as the converter's own files: cooked, may be named to other games
INNER_PACKAGE_FLAGS = 0x20000001        # every package inside the game's cooked files
FORCED_EXPORT = 1
ENGINE_VERSION = 1712559                # as the converter stamps her other files (tps2bl2.BL2_ENGINE_VERSION)
COOKER_VERSION = 134
Spec = tuple[str, str, str]             # the class's package, the class, the object's name


def split_name(text: str) -> tuple[str, int]:
    """A name as packages store it: its text and its number ("Thing_3" is "Thing" and 4; no number is 0)."""
    stem, _, digits = text.rpartition("_")
    if stem and digits.isdigit() and (digits == "0" or not digits.startswith("0")):
        return stem, int(digits) + 1
    return text, 0


def guid_of(package: str, objects: list[Spec]) -> bytes:
    """The package's GUID: the same on every machine for the same list, another for any other list, so two games
    whose mods number these objects differently don't take each other's numbers."""
    return hashlib.md5(repr((package, list(objects))).encode("utf-8")).digest()     # noqa: S324 - an identity, no secret


def build(package: str, objects: list[Spec], file_name: str, only: list[str] | None = None) -> bytes:
    """The file's bytes: `package`, numbered for one blank object per entry of `objects` in that order, and holding
    those of them named in `only` (all of them when it is None)."""
    if len({name.lower() for _, _, name in objects}) != len(objects):
        raise ValueError("two objects of one name")
    held = [(place, spec) for place, spec in enumerate(objects) if only is None or spec[2] in only]
    if only is not None and len(held) != len(only):
        raise ValueError(f"not among the package's objects: {sorted(set(only) - {spec[2] for spec in objects})}")
    class_packages = sorted({"Core", *(spec[0] for _, spec in held)}, key=str.lower)
    classes = sorted({("Core", "Package"), *((spec[0], spec[1]) for _, spec in held)}, key=lambda c: (c[1].lower(), c[0].lower()))
    texts: list[str] = []
    seen: dict[str, int] = {}
    wanted = ["Class", "None", package, *class_packages, *(c[1] for c in classes), *(split_name(spec[2])[0] for _, spec in held)]
    for text in sorted(set(wanted), key=str.lower):
        if text.lower() not in seen:                        # names are one whatever their case
            seen[text.lower()] = len(texts)
            texts.append(text)

    def fname(text: str, number: int = 0) -> bytes:
        return struct.pack("<ii", seen[text.lower()], number)

    # imports: each class, then each class's package
    class_index = {c: -(i + 1) for i, c in enumerate(classes)}
    package_index = {p: -(len(classes) + i + 1) for i, p in enumerate(class_packages)}
    imports = b"".join(fname("Core") + fname("Class") + struct.pack("<i", package_index[c[0]]) + fname(c[1]) for c in classes)
    imports += b"".join(fname("Core") + fname("Package") + struct.pack("<i", 0) + fname(p) for p in class_packages)

    none = fname("None")
    datas = [struct.pack("<i", -1) + none] + [struct.pack("<i", place) + none for place, _ in held]
    guid = guid_of(package, objects)
    rows = [(class_index[("Core", "Package")], 0, split_name(package), PACKAGE_OBJECT_FLAGS, [len(objects)], guid, INNER_PACKAGE_FLAGS)]
    rows += [(class_index[(spec[0], spec[1])], 1, split_name(spec[2]), OBJECT_FLAGS, [], bytes(16), 0) for _, spec in held]

    names = b"".join(w_fstring(text) + struct.pack("<Q", NAME_FLAGS) for text in texts)
    summary = Summary(file_version=832, licensee_version=46, header_size=0, folder=("None", False),
                      package_flags=FILE_FLAGS, name_count=len(texts), name_offset=0, export_count=len(rows), export_offset=0,
                      import_count=len(classes) + len(class_packages), import_offset=0, depends_offset=0,
                      import_export_guids_offset=0, import_guids_count=0, export_guids_count=0, thumbnail_table_offset=0,
                      guid=hashlib.md5(b"file:" + guid + file_name.encode("utf-8")).digest(),     # noqa: S324
                      generations=[(len(rows), len(texts), 0)], engine_version=ENGINE_VERSION, cooker_version=COOKER_VERSION,
                      compression_flags=0, chunks=[], package_source=zlib.crc32(file_name.upper().encode("ascii")),
                      additional_packages=[], texture_allocations=struct.pack("<i", 0), size=0)
    summary.name_offset = len(summary.write())
    summary.import_offset = summary.name_offset + len(names)
    summary.export_offset = summary.import_offset + len(imports)
    summary.depends_offset = summary.export_offset + sum(68 + 4 * len(row[4]) for row in rows)
    summary.header_size = summary.import_export_guids_offset = summary.depends_offset + 4 * len(rows)

    exports = b""
    offset = summary.header_size
    for (cls, outer, (text, number), flags, nets, package_guid, package_flags), data in zip(rows, datas, strict=True):
        exports += (struct.pack("<3i", cls, 0, outer) + fname(text, number) + struct.pack("<iQiiI", 0, flags, len(data), offset, FORCED_EXPORT)
                    + struct.pack("<i", len(nets)) + b"".join(struct.pack("<i", n) for n in nets) + package_guid
                    + struct.pack("<I", package_flags))
        offset += len(data)
    return summary.write() + names + imports + exports + bytes(4 * len(rows)) + b"".join(datas)


def place_guid(package: str, paths: list[str]) -> bytes:
    """A package's GUID from the list of what has a place in it: the same list, the same GUID, on every machine."""
    return hashlib.md5(repr(("places", package, list(paths))).encode("utf-8")).digest()     # noqa: S324 - an identity


ANCHOR = "AureliaPlaces"                # the one object of the file's own in each package: an empty inner package


def identities(packages: dict[str, list[str]], file_name: str) -> bytes:
    """A file that holds packages and next to nothing in them: for each name in `packages` a package with a GUID of
    its own and one place more than its list is long, and in that last place an empty inner package, `ANCHOR`.
    Loaded before the mod makes its run-time packages, it gives them what a package out of a file has, a network
    identity; the mod's modules then find them and make their objects in them as before, and the mod gives each
    listed object its place itself (core/netobjects.py `place`; RESEARCH.md §6.75).

    The anchor is there because the engine lists a package for other games only once the engine itself has put an
    object into one of its places (measured: twelve packages with a GUID, a count and every place filled by the mod
    were in no host's list; her roster's package, whose objects the engine loaded, was)."""
    names_in = sorted(packages, key=str.lower)
    texts: list[str] = []
    seen: dict[str, int] = {}
    for text in sorted({"Class", "Core", "None", "Package", ANCHOR, *(split_name(name)[0] for name in names_in)}, key=str.lower):
        if text.lower() not in seen:
            seen[text.lower()] = len(texts)
            texts.append(text)

    def fname(text: str, number: int = 0) -> bytes:
        return struct.pack("<ii", seen[text.lower()], number)

    imports = (fname("Core") + fname("Class") + struct.pack("<i", -2) + fname("Package")
               + fname("Core") + fname("Package") + struct.pack("<i", 0) + fname("Core"))
    data = struct.pack("<i", -1) + fname("None")
    rows = [(split_name(name), len(packages[name]), place_guid(name, packages[name])) for name in names_in]
    names = b"".join(w_fstring(text) + struct.pack("<Q", NAME_FLAGS) for text in texts)
    seed = hashlib.md5(b"file:" + b"".join(row[2] for row in rows) + file_name.encode("utf-8")).digest()     # noqa: S324
    summary = Summary(file_version=832, licensee_version=46, header_size=0, folder=("None", False),
                      package_flags=FILE_FLAGS, name_count=len(texts), name_offset=0, export_count=len(rows), export_offset=0,
                      import_count=2, import_offset=0, depends_offset=0, import_export_guids_offset=0, import_guids_count=0,
                      export_guids_count=0, thumbnail_table_offset=0, guid=seed, generations=[(len(rows), len(texts), 0)],
                      engine_version=ENGINE_VERSION, cooker_version=COOKER_VERSION, compression_flags=0, chunks=[],
                      package_source=zlib.crc32(file_name.upper().encode("ascii")), additional_packages=[],
                      texture_allocations=struct.pack("<i", 0), size=0)
    summary.name_offset = len(summary.write())
    summary.import_offset = summary.name_offset + len(names)
    summary.export_offset = summary.import_offset + len(imports)
    summary.export_count = 2 * len(rows)
    summary.generations = [(2 * len(rows), len(texts), 0)]
    summary.name_offset = len(summary.write())
    summary.import_offset = summary.name_offset + len(names)
    summary.export_offset = summary.import_offset + len(imports)
    summary.depends_offset = summary.export_offset + (72 + 68) * len(rows)
    summary.header_size = summary.import_export_guids_offset = summary.depends_offset + 4 * 2 * len(rows)
    exports = b""
    datas = b""
    offset = summary.header_size
    for (text, number), count, guid in rows:                # the packages: exports 1 .. n
        exports += (struct.pack("<3i", -1, 0, 0) + fname(text, number) + struct.pack("<iQiiI", 0, PACKAGE_OBJECT_FLAGS, len(data), offset,
                                                                                   FORCED_EXPORT)
                    + struct.pack("<ii", 1, count + 1) + guid + struct.pack("<I", INNER_PACKAGE_FLAGS))
        datas += data
        offset += len(data)
    for index, (_name, count, _guid) in enumerate(rows):    # each one's anchor, in its last place: exports n+1 .. 2n
        anchor = struct.pack("<i", count) + fname("None")
        exports += (struct.pack("<3i", -1, 0, index + 1) + fname(ANCHOR) + struct.pack("<iQiiI", 0, PACKAGE_OBJECT_FLAGS, len(anchor), offset,
                                                                                      FORCED_EXPORT)
                    + struct.pack("<i", 0) + bytes(16) + struct.pack("<I", 0))
        datas += anchor
        offset += len(anchor)
    return summary.write() + names + imports + exports + bytes(4 * 2 * len(rows)) + datas
