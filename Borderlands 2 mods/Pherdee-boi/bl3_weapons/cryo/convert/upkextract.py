"""A compact package made of parts of other packages (RESEARCH.md §6.30): what tps2bl2.py's in-place conversion can't do.

tps2bl2.py converts a TPS package in place, which suits her own packages. TPS keeps its shared ice effects (a frozen
enemy's icicles, its thaw and its shatter) in its 79 MB Startup.upk, among some 60,000 other exports. This writes a new
package with only the exports asked for: each root and everything inside it, re-rooted under one new top-level package
(so nothing clashes with BL2's own objects of the same path), with fresh name, import and export tables and every
export's tagged properties re-encoded for them.

What it handles:
- exports made of tagged properties only; native data after the properties stops it (none of the particle classes have
  any);
- references between extracted exports, kept;
- references out of them: an import when `keep` names the target's path (BL2 has that object); a TPS StaticMesh
  of the layout staticmesh.py handles, carried along under `<top>.Meshes` (converted to BL2's layout, its sections'
  materials None: a particle mesh emitter draws with its own); else None (the caller fills such holes at runtime, as
  particles.py does for materials);
- array properties: the data doesn't say what an array holds, so ARRAY_KINDS names those met; an unknown one stops it;
- a Texture2D carried with other mips than its own (`add_texture`: a piece cut out of a sheet of UI art), stored inline.

    from upk import Package
    from upkextract import Extraction
    ex = Extraction("Crocus_IceFX")
    ex.add(Package.load(startup), "FX_CO_Damage_Ice.Particles.Part_Frozen_Shatter", "Crocus_IceFX.Particles.Part_Frozen_Shatter")
    report = ex.write(Path("Crocus_IceFX_SF.upk"), template=Package.load(any_bl2_sf_package).summary)
"""

from __future__ import annotations

import hashlib
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import staticmesh
from upk import TAG, Package, Summary, w_fstring

OBJECT_TYPES = frozenset({"ObjectProperty", "ComponentProperty", "ClassProperty", "InterfaceProperty"})
# What an array property holds, by name (met in the particle systems extracted so far)
OBJECT_ARRAYS = frozenset({"Emitters", "LODLevels", "Modules", "SpawnModules", "SpawningModules", "UpdateModules",
                           "ReferencedModules"})
FLOAT_ARRAYS = frozenset({"LookupTable", "LODDistances"})
STRUCT_ARRAYS = frozenset({"Points", "BurstList", "LODSettings", "Events", "SourceLocations",
                           "ParticleModuleEventsToSendToGame", "LODInfo", "Elements",
                           "DynamicParams"})
NAME_FLAGS = 0x0007001000000000         # BL2's usual name flags
OBJECT_PACKAGE_FLAGS = 0x0007000400000000   # a package export's object flags (BL2 seekfree packages)
FORCED_EXPORT = 0x1
BULK_SEPARATE_FILE = 0x1                # bulk data flags: its data is in a texture file cache
BULK_UNUSED = 0x20                      # it has none
TOP_PACKAGE_FLAGS = 0x20000001
INDEX_NONE = -1


class ExtractError(ValueError):
    """The data has something this tool doesn't handle (it says what and where)."""


@dataclass
class _Export:
    path: str
    class_index: int
    outer: int
    name: tuple[str, int]
    archetype: int
    flags: int
    export_flags: int
    package_flags: int
    nets: list[int]
    guid: bytes
    data: bytes = b""
    mesh: bytes = b""        # a TPS StaticMesh's native data (materials cleared), converted when its offset is known
    mips: list[tuple[int, int, bytes]] = field(default_factory=list)   # a texture's: width, height, data (inline)
    tail: bytes = b""        # and what follows its mips


@dataclass
class _Source:
    pkg: Package
    path: str            # source root path
    new_path: str        # where it goes


@dataclass
class _Texture:
    pkg: Package
    path: str            # the source texture
    new_path: str        # where it goes
    levels: list[tuple[int, int, bytes]]    # its mips there: width, height, data (the largest first)


@dataclass
class Extraction:
    top: str                                            # the new package's top-level package (and file stem's base)
    keep: frozenset[str] = frozenset()                  # outside objects BL2 has: kept as imports
    sources: list[_Source] = field(default_factory=list)
    textures: list[_Texture] = field(default_factory=list)
    report: Counter[str] = field(default_factory=Counter)

    def add(self, pkg: Package, path: str, new_path: str) -> None:
        if not new_path.startswith(self.top + "."):
            raise ExtractError(f"{new_path} isn't under {self.top}")
        self.sources.append(_Source(pkg, path, new_path))

    def add_texture(self, pkg: Package, path: str, new_path: str, levels: list[tuple[int, int, bytes]]) -> None:
        """The Texture2D `path` of `pkg` as `new_path`, with `levels` (width, height and data each, in its format, the
        largest first) in place of its own mips: its settings (format, group, sRGB) on other pixels, such as a piece
        cut out of a sheet. Stored inline, with no texture file cache."""
        if not new_path.startswith(self.top + "."):
            raise ExtractError(f"{new_path} isn't under {self.top}")
        if not levels:
            raise ExtractError(f"{new_path}: no mips given")
        self.textures.append(_Texture(pkg, path, new_path, levels))

    # ---------------------------------------------------------------- tables
    def _setup(self) -> None:
        self._names: dict[str, int] = {}
        self._imports: list[tuple[tuple[str, int], tuple[str, int], int, tuple[str, int]]] = []
        self._import_at: dict[str, int] = {}
        self._exports: list[_Export] = []
        self._export_at: dict[str, int] = {}
        self._mapped: dict[tuple[int, int], int] = {}      # (source id, source index) -> new index
        self.name("None")

    def name(self, text: str) -> int:
        if text not in self._names:
            self._names[text] = len(self._names)
        return self._names[text]

    def _fname(self, base: str, number: int = 0) -> bytes:
        return struct.pack("<ii", self.name(base), number)

    def _import(self, path: str, class_package: str, class_name: str) -> int:
        """The import of `path` (its outers become package imports), made once."""
        if path in self._import_at:
            return self._import_at[path]
        outer_path, _, name = path.rpartition(".")
        outer = self._import(outer_path, "Core", "Package") if outer_path else 0
        base, number = _split(name)
        self._imports.append((( class_package, 0), (class_name, 0), outer, (base, number)))
        for text in (class_package, class_name, base):
            self.name(text)
        index = -len(self._imports)
        self._import_at[path] = index
        return index

    def _class_import(self, class_path: str) -> int:
        """`Engine.ParticleSystem` -> an import of class Core.Class in package import Engine."""
        return self._import(class_path, "Core", "Class")

    def _package_export(self, path: str) -> int:
        """A package export for `path` and its outers (the top one a forced export, as BL2's seekfree packages have)."""
        if path in self._export_at:
            return self._export_at[path]
        outer_path, _, name = path.rpartition(".")
        outer = self._package_export(outer_path) if outer_path else 0
        top = not outer_path
        entry = _Export(path, self._class_import("Core.Package"), outer, _split(name), 0, OBJECT_PACKAGE_FLAGS,
                        FORCED_EXPORT if top else 0, TOP_PACKAGE_FLAGS if top else 0, [0] if top else [],
                        _guid(path) if top else bytes(16))
        entry.data = struct.pack("<i", INDEX_NONE) + self._fname("None")    # what BL2's package exports hold
        return self._add_export(entry)

    def _add_export(self, entry: _Export) -> int:
        self._exports.append(entry)
        index = len(self._exports)
        self._export_at[entry.path] = index
        self.name(entry.name[0])
        return index

    # ---------------------------------------------------------------- references
    def _members(self, src: _Source) -> list:
        pkg = src.pkg
        found = [e for e in pkg.exports if pkg.path_of(e.index) == src.path or pkg.path_of(e.index).startswith(src.path + ".")]
        if not found:
            raise ExtractError(f"{src.path} isn't in {pkg.path.name}")
        for export in found:
            _load(pkg, export)
        return found

    def _new_path(self, src: _Source, path: str) -> str:
        return src.new_path + path[len(src.path):]

    def _ref(self, src: _Source, index: int) -> int:
        """A source object index as a new one: an extracted export, an import, or 0."""
        if index == 0:
            return 0
        key = (id(src.pkg), index)
        if key in self._mapped:
            return self._mapped[key]
        pkg = src.pkg
        path = pkg.path_of(index)
        for other in self.sources:          # inside one of the roots (of this package): extracted
            if other.pkg is pkg and (path == other.path or path.startswith(other.path + ".")):
                new = self._export_at.get(self._new_path(other, path), 0)
                self._mapped[key] = new
                return new
        if index < 0:                        # an import of the source: kept if BL2 surely has it (a class, a class
            imp = pkg.imports[-index - 1]    # default, or named in `keep`), else None: a missing import is a warning
            if imp.class_name == "Class" or imp.name.startswith("Default__") or path in self.keep:
                new = self._import(path, imp.class_package, imp.class_name)
            else:
                new = 0
                self.report[f"dropped to None (an import BL2 may not have): {imp.class_name} {path}"] += 1
        elif path in self.keep:              # an object BL2 has
            cls = pkg.class_of(pkg.exports[index - 1])
            package, _, name = cls.rpartition(".")
            new = self._import(path, package, name)
        elif pkg.class_of(pkg.exports[index - 1]) == "Engine.StaticMesh" and self._mesh_native(pkg, pkg.exports[index - 1]):
            new = self._carry_mesh(src, pkg.exports[index - 1], key)
        else:
            new = 0
            self.report[f"dropped to None: {pkg.class_of(pkg.exports[index - 1])} {path}"] += 1
        self._mapped[key] = new
        return new

    def _mesh_native(self, pkg: Package, export) -> bytes | None:
        """A TPS StaticMesh's native data with its sections' materials cleared, or None if staticmesh.py can't take it."""
        _load(pkg, export)
        _tags, props_end = pkg.tagged_properties(export)
        native = bytearray(pkg.buf[props_end:export.serial_offset + export.serial_size])
        refs = _mesh_material_refs(bytes(native))
        if refs is None:
            return None
        for pos in refs:
            struct.pack_into("<i", native, pos, 0)
        return bytes(native)

    def _carry_mesh(self, src: _Source, export, key: tuple[int, int]) -> int:
        pkg = src.pkg
        name = (pkg.names[export.name_idx].text, export.name_num)
        path = f"{self.top}.Meshes.{name[0]}" + (f"_{name[1] - 1}" if name[1] else "")
        if path in self._export_at:
            raise ExtractError(f"two meshes would be {path}")
        entry = _Export(path, self._class_import(pkg.class_of(export)), self._package_export(f"{self.top}.Meshes"),
                        name, 0, export.flags, export.export_flags, 0, [], bytes(16))
        index = self._add_export(entry)
        self._mapped[key] = index           # before its data: its tags may point back at it
        entry.archetype = self._ref(src, export.archetype)
        entry.data = self._data(src, export, native_ok=True)
        entry.mesh = self._mesh_native(pkg, export) or b""
        self.report[f"carried and converted: StaticMesh {pkg.path_of(export.index)}"] += 1
        return index

    def _carry_texture(self, texture: _Texture) -> None:
        """A texture's export: its tags with the sizes of the mips it gets, no cache name and no source-art strings (a
        path on TPS's build machine); then, as tps2bl2.carry_textures writes them, the 16 bytes before its mip array
        and what follows the array, both as they are. The mips themselves are written once their place in the file is
        known (`_bytes`): inline bulk data records its own offset."""
        pkg = texture.pkg
        export = next((e for e in pkg.exports if pkg.path_of(e.index) == texture.path), None)
        if export is None or pkg.class_of(export) != "Engine.Texture2D":
            raise ExtractError(f"{texture.path} isn't a texture of {pkg.path.name}")
        _load(pkg, export)
        tags, props_end = pkg.tagged_properties(export)
        width, height, _data = texture.levels[0]
        ints = {"SizeX": width, "SizeY": height, "OriginalSizeX": width, "OriginalSizeY": height,
                "MipTailBaseIdx": len(texture.levels) - 1, "FirstResourceMemMip": 0, "LODBias": 0}
        out = bytearray(struct.pack("<i", INDEX_NONE))
        for tag in tags:
            if tag.type == "StrProperty":
                continue
            out += self._name_at(pkg, tag.tag_pos) + self._name_at(pkg, tag.tag_pos + 8)
            out += pkg.buf[tag.tag_pos + 16:tag.tag_pos + 24]
            if tag.type in ("StructProperty", "ByteProperty"):
                out += self._name_at(pkg, tag.tag_pos + 24)
            if tag.type == "BoolProperty":
                out += pkg.buf[tag.value_pos:tag.value_pos + 1]
            elif tag.type == "IntProperty" and tag.name in ints:
                out += struct.pack("<i", ints[tag.name])
            elif tag.name == "TextureFileCacheName":
                out += self._fname("None")
            elif tag.type == "NameProperty" or (tag.type == "ByteProperty" and tag.size == 8):
                out += self._name_at(pkg, tag.value_pos)
            elif tag.type in OBJECT_TYPES or tag.type == "ArrayProperty":
                raise ExtractError(f"{texture.path}.{tag.name}: a {tag.type} on a texture isn't handled")
            else:
                out += pkg.buf[tag.value_pos:tag.value_pos + tag.size]
        out += self._fname("None")
        pos = props_end + 16                 # the mip array: a count, then each mip's bulk header, data and size
        count = struct.unpack_from("<i", pkg.buf, pos)[0]
        pos += 4
        end = export.serial_offset + export.serial_size
        if not 0 < count < 16:
            raise ExtractError(f"{texture.path}: its mip array doesn't read ({count} mips)")
        for _ in range(count):
            flags, _n, size, _off = struct.unpack_from("<Iiii", pkg.buf, pos)
            pos += 16 + (0 if flags & (BULK_SEPARATE_FILE | BULK_UNUSED) else size) + 8
        if pos > end:
            raise ExtractError(f"{texture.path}: its mips run past its data")
        outer_path, _, name = texture.new_path.rpartition(".")
        entry = _Export(texture.new_path, self._class_import("Engine.Texture2D"), self._package_export(outer_path),
                        _split(name), 0, export.flags, export.export_flags, 0, [], bytes(16))
        entry.data = bytes(out) + bytes(pkg.buf[props_end:props_end + 16]) + struct.pack("<i", len(texture.levels))
        entry.mips = list(texture.levels)
        entry.tail = bytes(pkg.buf[pos:end])
        self._add_export(entry)
        self.report[f"carried with other pixels: Texture2D {texture.path}"] += 1

    # ---------------------------------------------------------------- tagged properties
    def _src_fname(self, pkg: Package, pos: int) -> tuple[str, int]:
        idx, number = struct.unpack_from("<ii", pkg.buf, pos)
        if not 0 <= idx < len(pkg.names):
            raise ExtractError(f"bad name index {idx} at {pos}")
        return pkg.names[idx].text, number

    def _name_at(self, pkg: Package, pos: int) -> bytes:
        base, number = self._src_fname(pkg, pos)
        return self._fname(base, number)

    def _tags(self, src: _Source, pos: int, end: int, where: str) -> tuple[bytes, int]:
        """One tag list (an object's or a struct's) re-encoded; returns the bytes and the position after it."""
        pkg, out = src.pkg, bytearray()
        while pos < end:
            name, _ = self._src_fname(pkg, pos)
            out += self._name_at(pkg, pos)
            pos += 8
            if name == "None":
                return bytes(out), pos
            typ, _ = self._src_fname(pkg, pos)
            out += self._name_at(pkg, pos)
            pos += 8
            size, array_index = struct.unpack_from("<ii", pkg.buf, pos)
            out += pkg.buf[pos:pos + 8]
            pos += 8
            struct_name = ""
            if typ in ("StructProperty", "ByteProperty"):
                struct_name, _ = self._src_fname(pkg, pos)
                out += self._name_at(pkg, pos)
                pos += 8
            if typ == "BoolProperty":
                out += pkg.buf[pos:pos + 1]
                pos += 1
                continue
            value = self._value(src, typ, struct_name, name, pos, size, f"{where}.{name}")
            if len(value) != size:
                raise ExtractError(f"{where}.{name}: re-encoded to {len(value)} bytes, not {size}")
            out += value
            pos += size
        raise ExtractError(f"{where}: tag list without a None terminator")

    def _value(self, src: _Source, typ: str, struct_name: str, name: str, pos: int, size: int, where: str) -> bytes:
        pkg = src.pkg
        raw = bytes(pkg.buf[pos:pos + size])
        if typ in OBJECT_TYPES:
            return struct.pack("<i", self._ref(src, struct.unpack_from("<i", raw)[0]))
        if typ == "DelegateProperty":
            return struct.pack("<i", self._ref(src, struct.unpack_from("<i", raw)[0])) + self._name_at(pkg, pos + 4)
        if typ == "NameProperty" or (typ == "ByteProperty" and size == 8):
            return self._name_at(pkg, pos)
        if typ == "StructProperty":
            try:
                pkg.tags_at(pos, pos + size)
            except (ValueError, struct.error):
                return raw                   # an immutable struct (Vector, Color, Box...): raw values, no names
            data, _ = self._tags(src, pos, pos + size, f"{where}<{struct_name}>")
            return data
        if typ == "ArrayProperty":
            return self._array(src, name, pos, size, where)
        if typ == "MapProperty":
            raise ExtractError(f"{where}: map properties aren't handled")
        return raw                           # int, float, string, plain byte

    def _array(self, src: _Source, name: str, pos: int, size: int, where: str) -> bytes:
        pkg = src.pkg
        count = struct.unpack_from("<i", pkg.buf, pos)[0]
        body = size - 4
        out = bytearray(pkg.buf[pos:pos + 4])
        if count == 0:
            return bytes(out)
        if name in OBJECT_ARRAYS:
            if body != 4 * count:
                raise ExtractError(f"{where}: an object array of {count} in {body} bytes")
            for k in range(count):
                out += struct.pack("<i", self._ref(src, struct.unpack_from("<i", pkg.buf, pos + 4 + 4 * k)[0]))
            return bytes(out)
        if name in FLOAT_ARRAYS:
            return bytes(pkg.buf[pos:pos + size])
        if name in STRUCT_ARRAYS:
            p, end = pos + 4, pos + size
            for k in range(count):
                data, p = self._tags(src, p, end, f"{where}[{k}]")
                out += data
            if p != end:
                raise ExtractError(f"{where}: {end - p} bytes after its {count} structs")
            return bytes(out)
        raise ExtractError(f"{where}: an array this tool doesn't know ({count} in {body} bytes): add it to ARRAY tables")

    def _data(self, src: _Source, export, native_ok: bool = False) -> bytes:
        """An export's data: a component (distributions are) starts with its TemplateOwnerClass and, for a template, its
        TemplateName (checked 2026-10-03: BL2's own -245 is Engine.ParticleModuleSpawn, the class of the
        distribution's owner); then the NetIndex, written as none (the package has no net objects); then the tags."""
        pkg = src.pkg
        start = export.serial_offset
        end = start + export.serial_size
        tags, props_end = pkg.tagged_properties(export)
        tags_start = tags[0].tag_pos if tags else props_end - 8
        head = tags_start - start - 4           # 0: an object; 4: a component; 12: a component template
        if head not in (0, 4, 12):
            raise ExtractError(f"{pkg.path_of(export.index)}: {head} bytes before its properties")
        if props_end != end and not native_ok:
            raise ExtractError(f"{pkg.path_of(export.index)}: {end - props_end} bytes of native data after its properties")
        prefix = b""
        if head:
            prefix += struct.pack("<i", self._ref(src, struct.unpack_from("<i", pkg.buf, start)[0]))
        if head == 12:
            prefix += self._name_at(pkg, start + 4)
        body, _ = self._tags(src, tags_start, props_end, pkg.path_of(export.index))
        return prefix + struct.pack("<i", INDEX_NONE) + body

    # ---------------------------------------------------------------- writing
    def write(self, path: Path, template: Summary) -> Counter[str]:
        """Write the package; `template` gives the header's versions and flags (a BL2 seekfree package's)."""
        self._setup()
        self.report = Counter()
        members: list[tuple[_Source, object]] = []
        for src in self.sources:            # exports first, in source order, so references can point at any of them
            outer_path = src.new_path.rpartition(".")[0]
            self._package_export(outer_path)
            for export in self._members(src):
                new_path = self._new_path(src, src.pkg.path_of(export.index))
                entry = _Export(new_path, 0, 0, (src.pkg.names[export.name_idx].text, export.name_num), 0,
                                export.flags, export.export_flags, 0, [], bytes(16))
                self._add_export(entry)
                members.append((src, export))
        for src, export in members:          # then their classes, outers, archetypes and data
            entry = self._exports[self._export_at[self._new_path(src, src.pkg.path_of(export.index))] - 1]
            entry.class_index = self._class_import(src.pkg.class_of(export))
            entry.outer = self._export_at[entry.path.rpartition(".")[0]]
            entry.archetype = self._ref(src, export.archetype)
            entry.data = self._data(src, export)
            self.report[f"extracted: {src.pkg.class_of(export)}"] += 1
        for texture in self.textures:
            self._carry_texture(texture)
        path.write_bytes(self._bytes(template))
        return self.report

    def _bytes(self, template: Summary) -> bytes:
        names = list(self._names)
        name_table = b"".join(w_fstring(text) + struct.pack("<Q", NAME_FLAGS) for text in names)
        import_table = b"".join(self._fname(*cp) + self._fname(*cn) + struct.pack("<i", outer) + self._fname(*nm)
                                for cp, cn, outer, nm in self._imports)
        if len(self._names) != len(names):
            raise ExtractError("a name was added while writing the tables")

        def export_entry(e: _Export, offset: int) -> bytes:
            return (struct.pack("<iii", e.class_index, 0, e.outer) + self._fname(*e.name)
                    + struct.pack("<iQiiI", e.archetype, e.flags, len(e.data), offset, e.export_flags)
                    + struct.pack("<i", len(e.nets)) + b"".join(struct.pack("<i", n) for n in e.nets)
                    + e.guid + struct.pack("<I", e.package_flags))

        summary = Summary(
            file_version=template.file_version, licensee_version=template.licensee_version, header_size=0,
            folder=("None", False), package_flags=template.package_flags, name_count=len(names), name_offset=0,
            export_count=len(self._exports), export_offset=0, import_count=len(self._imports), import_offset=0,
            depends_offset=0, import_export_guids_offset=0, import_guids_count=0, export_guids_count=0,
            thumbnail_table_offset=0, guid=_guid("summary:" + self.top),
            generations=[(len(self._exports), len(names), 0)], engine_version=template.engine_version,
            cooker_version=template.cooker_version, compression_flags=0, chunks=[],
            package_source=template.package_source, additional_packages=[], texture_allocations=struct.pack("<i", 0),
            size=0)
        head_size = len(summary.write())
        name_offset = head_size
        import_offset = name_offset + len(name_table)
        export_offset = import_offset + len(import_table)
        export_table_size = len(b"".join(export_entry(e, 0) for e in self._exports))
        depends_offset = export_offset + export_table_size
        guids_offset = depends_offset + 4 * len(self._exports)
        data_offset = guids_offset
        offsets, at = [], data_offset
        for e in self._exports:             # a mesh's converted data has the same size wherever it sits
            if e.mesh:
                converted = staticmesh.to_bl2(e.mesh, 0)
                if converted is None:
                    raise ExtractError(f"{e.path}: the mesh no longer converts")
                e.data += b"\0" * len(converted)       # placeholder, filled below
            if e.mips:
                e.data += _mips(e.mips, 0) + e.tail    # likewise
            offsets.append(at)
            at += len(e.data)
        for e, off in zip(self._exports, offsets):    # inline bulk offsets point into the file: convert in place
            if e.mesh:
                converted = staticmesh.to_bl2(e.mesh, 0)
                head = len(e.data) - len(converted)
                e.data = e.data[:head] + staticmesh.to_bl2(e.mesh, off + head)
            if e.mips:
                head = len(e.data) - len(_mips(e.mips, 0)) - len(e.tail)
                e.data = e.data[:head] + _mips(e.mips, off + head) + e.tail
        export_table = b"".join(export_entry(e, off) for e, off in zip(self._exports, offsets))
        summary.header_size, summary.name_offset, summary.import_offset = data_offset, name_offset, import_offset
        summary.export_offset, summary.depends_offset = export_offset, depends_offset
        summary.import_export_guids_offset = guids_offset
        head = summary.write()
        if len(head) != head_size or len(export_table) != export_table_size:
            raise ExtractError("the header's size changed when its offsets were filled in")
        out = head + name_table + import_table + export_table + bytes(4 * len(self._exports))
        out += b"".join(e.data for e in self._exports)
        if struct.unpack_from("<I", out)[0] != TAG:
            raise ExtractError("bad package tag")
        return out


def _mesh_material_refs(data: bytes) -> list[int] | None:
    """Where a TPS StaticMesh's native data holds object references: each section's material (the body setup is none in
    the layout staticmesh.py handles). Offsets into `data`; None if the layout isn't handled. The walk is read_tps's
    (kept there, so the converter's stamps don't change with this tool)."""
    layout = staticmesh.read_tps(data)
    if layout is None:
        return None
    r = staticmesh._Reader(data)      # noqa: SLF001 - same tool family
    r.skip(56)                        # bounds, body setup (none), kDOP root bound
    r.bulk()
    r.bulk()
    r.skip(16)
    found = []
    for _ in range(r.i32()):
        r.skip(16)                    # RawTriangles (empty once cooked)
        for _e in range(r.i32()):
            found.append(r.p)         # the section's material
            r.skip(36)
            r.skip(8 * r.i32())
        r.skip(1)
        r.skip(8)                     # position buffer: stride, count
        r.skip(26)                    # 1, 0, bias, scale
        r.bulk()
        r.skip(16)                    # tangents and UVs
        r.bulk()
        r.skip(12)                    # colour buffer stride and vertices (none), the vertex count
        r.bulk()                      # index buffer
        r.skip(4)                     # the 1 that ends the LOD
    return found if r.p == layout.trailer else None


def _mips(levels: list[tuple[int, int, bytes]], offset: int) -> bytes:
    """A texture's mips as inline bulk data, the first one's header at `offset` in the file: each a header (no flags,
    its size twice, where its data sits), the data, then its width and height."""
    out = bytearray()
    for width, height, data in levels:
        out += struct.pack("<Iiii", 0, len(data), len(data), offset + len(out) + 16) + data
        out += struct.pack("<ii", width, height)
    return bytes(out)


def _guid(text: str) -> bytes:
    """A GUID derived from a name: the same sources give the same bytes."""
    return hashlib.md5(text.encode("utf-8")).digest()  # noqa: S324 - an identifier, not security


def _load(pkg: Package, export) -> None:
    """A partly read package (findexport.PartialPackage: TPS's fully compressed Startup.upk) unpacks the blocks that
    hold this export's data; a whole one has them already."""
    ensure = getattr(pkg, "ensure_export", None)
    if ensure is not None:
        ensure(export)


def _split(name: str) -> tuple[str, int]:
    """An object name as FName (base, number): "Foo_3" is ("Foo", 4); only a decimal suffix without a leading 0 counts."""
    base, _, digits = name.rpartition("_")
    if base and digits.isdigit() and (digits == "0" or not digits.startswith("0")):
        return base, int(digits) + 1
    return name, 0
