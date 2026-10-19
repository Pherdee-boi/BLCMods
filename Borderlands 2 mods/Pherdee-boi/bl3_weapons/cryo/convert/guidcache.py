"""A GUID cache package: package names and their GUIDs, as each of the game's own DLCs has one
(`DLC/<Name>/Compat/Content/GuidCache_<name>.upk`), read and written (co-op; RESEARCH.md §6.69).

A client that joins a game is sent the host's list of loaded packages and looks each one up, by name, in the GUID
caches it has loaded (the main game's and one per installed DLC). A name none of them knows is a package to
download, and the connection ends there. Her packages are in none of the game's caches, so she needs one of her own.

The file is two exports and their tables: a `GuidCache` whose data is a map of name to GUID, and an
`ObjectReferencer` holding it (the layout of the game's own, read from GuidCache_tulip.upk and
GuidCache_gardenia01.upk).

From a shell:
    python tools/cli.py guidcache read <GuidCache*.upk> [...]                      the names and GUIDs in them
    python tools/cli.py guidcache build <out.upk> <package file> [...] [--known <GuidCache*.upk> ...]
        a cache of the packages inside those files (each top-level package that is a forced export), without the
        names the `--known` caches already have
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import findexport
from upk import Summary, w_fstring

NAME_FLAGS = 0x0007001000000000         # every name's flags in the game's own caches
OBJECT_FLAGS = 0x0007000000000000       # both exports' flags there
ENGINE_VERSION = 1712559                # as the converter stamps her other files (tps2bl2.BL2_ENGINE_VERSION)
COOKER_VERSION = 134
PACKAGE_FLAGS = 0xC
STRUCTURE = ("ArrayProperty", "Class", "Core", "Engine", "GuidCache", "None", "ObjectReferencer", "Package",
             "ReferencedObjects")
Key = tuple[str, int]                   # a name as packages store it: its text, and its number (0: none)


def read(path: Path) -> dict[Key, bytes]:
    """The names and GUIDs of a GUID cache package."""
    pkg = findexport.load_any(path)
    if pkg is None:
        raise ValueError(f"{path}: not a package")
    names = [n.text for n in pkg.names]
    none = names.index("None")
    for export in pkg.exports:
        if not pkg.class_of(export).endswith("GuidCache"):
            continue
        data = pkg.export_data(export)
        at = 4                                              # the object's net index comes first
        if struct.unpack_from("<ii", data, at) != (none, 0):
            raise ValueError(f"{path}: the cache has properties before its map")
        (count,) = struct.unpack_from("<i", data, at + 8)
        at += 12
        out: dict[Key, bytes] = {}
        for _ in range(count):
            index, number = struct.unpack_from("<ii", data, at)
            out[(names[index], number)] = data[at + 8:at + 24]
            at += 24
        return out
    raise ValueError(f"{path}: no GuidCache in it")


def packages_of(path: Path) -> dict[Key, bytes]:
    """The packages inside a cooked file: its top-level packages that are forced exports (the file's own among
    them), each with the GUID the engine gives it when the file loads."""
    pkg = findexport._tables(path)                          # noqa: SLF001 - same tool family
    if pkg is None:
        raise ValueError(f"{path}: not a package")
    names = [n.text for n in pkg.names]
    return {(names[e.name_idx], e.name_num): e.package_guid for e in pkg.exports
            if e.outer == 0 and e.export_flags & 1 and pkg.class_of(e) == "Core.Package" and any(e.package_guid)}


def build(entries: dict[Key, bytes], name: str = "GuidCache") -> bytes:
    """A GUID cache package holding `entries`."""
    texts: list[str] = []
    seen: dict[str, int] = {}
    for text in (*STRUCTURE, *sorted({key[0] for key in entries}, key=str.lower)):
        if text.lower() not in seen:                        # names are one whatever their case
            seen[text.lower()] = len(texts)
            texts.append(text)

    def fname(text: str, number: int = 0) -> bytes:
        return struct.pack("<ii", seen[text.lower()], number)

    rows = sorted(entries.items(), key=lambda kv: (kv[0][0].lower(), kv[0][1]))
    cache = struct.pack("<i", -1) + fname("None") + struct.pack("<i", len(rows))
    cache += b"".join(fname(*key) + guid for key, guid in rows)
    referencer = (struct.pack("<i", -1) + fname("ReferencedObjects") + fname("ArrayProperty")
                  + struct.pack("<4i", 8, 0, 1, 1) + fname("None"))       # an array of one: export 1, the cache

    names = b"".join(w_fstring(text) + struct.pack("<Q", NAME_FLAGS) for text in texts)
    imports = (fname("Core") + fname("Class") + struct.pack("<i", -3) + fname("GuidCache")
               + fname("Core") + fname("Class") + struct.pack("<i", -3) + fname("ObjectReferencer")
               + fname("Core") + fname("Package") + struct.pack("<i", 0) + fname("Engine"))
    summary = Summary(file_version=832, licensee_version=46, header_size=0, folder=("None", False),
                      package_flags=PACKAGE_FLAGS, name_count=len(texts), name_offset=0, export_count=2, export_offset=0,
                      import_count=3, import_offset=0, depends_offset=0, import_export_guids_offset=0,
                      import_guids_count=0, export_guids_count=0, thumbnail_table_offset=0, guid=bytes(16),
                      generations=[(2, len(texts), 0)], engine_version=ENGINE_VERSION, cooker_version=COOKER_VERSION,
                      compression_flags=0, chunks=[], package_source=zlib.crc32(name.upper().encode("ascii")),
                      additional_packages=[], texture_allocations=struct.pack("<i", 0), size=0)
    summary.name_offset = len(summary.write())
    summary.import_offset = summary.name_offset + len(names)
    summary.export_offset = summary.import_offset + len(imports)
    summary.depends_offset = summary.export_offset + 2 * 68
    summary.header_size = summary.import_export_guids_offset = summary.depends_offset + 8

    def export(class_index: int, text: str, number: int, size: int, offset: int) -> bytes:
        return (struct.pack("<3i", class_index, 0, 0) + fname(text, number) + struct.pack("<iQiiI", 0, OBJECT_FLAGS, size, offset, 0)
                + struct.pack("<i", 0) + bytes(16) + struct.pack("<I", 0))

    exports = (export(-1, "GuidCache", 0, len(cache), summary.header_size)
               + export(-2, "ObjectReferencer", 1, len(referencer), summary.header_size + len(cache)))
    return summary.write() + names + imports + exports + struct.pack("<2i", 0, 0) + cache + referencer
