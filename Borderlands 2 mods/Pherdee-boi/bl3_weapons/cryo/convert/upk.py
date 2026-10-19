"""UE3 package reader/writer for BL2/TPS packages (file version 832, licensee 46).

Loads a package into its uncompressed layout (a bytearray) and records where every table entry
lives, so a converter can patch fields in place. Writing keeps that layout byte for byte: only the
summary is rewritten (uncompressed, versions restamped), so export serial offsets, and any absolute
bulk-data offsets inside export data, stay valid.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

TAG = 0x9E2A83C1
PKG_STORE_COMPRESSED = 0x02000000
COMPRESS_LZO = 2

# Object flags (64-bit). NOTE: BL2 does not skip exports carrying all three NotFor flags: its own
# cooked packages have BehaviorProviderDefinitions flagged that way, and they load (measured
# 2026-09-30, S1/M1). Disable exports with disable_export() instead.
RF_NOT_FOR_CLIENT = 0x0010000000000000
RF_NOT_FOR_SERVER = 0x0020000000000000
RF_NOT_FOR_EDIT = 0x0040000000000000
RF_FILTERED = RF_NOT_FOR_CLIENT | RF_NOT_FOR_SERVER | RF_NOT_FOR_EDIT


# LZO1X, the compression of both games' packages, in plain Python (no library to install).
def lzo1x_decompress(src: bytes, out_len: int) -> bytes:
    dst = bytearray()
    ip = 0

    def copy_match(m_pos: int, count: int) -> None:
        for k in range(count):
            dst.append(dst[m_pos + k])

    t = src[ip]
    state = "loop"
    if t > 17:
        ip += 1
        t -= 17
        if t < 4:
            state = "match_next"
        else:
            dst += src[ip:ip + t]
            ip += t
            state = "first_literal_run"

    while True:
        if state == "loop":
            t = src[ip]; ip += 1
            if t >= 16:
                state = "match"
                continue
            if t == 0:
                while src[ip] == 0:
                    t += 255
                    ip += 1
                t += 15 + src[ip]; ip += 1
            t += 3
            dst += src[ip:ip + t]
            ip += t
            state = "first_literal_run"
            continue

        if state == "first_literal_run":
            t = src[ip]; ip += 1
            if t >= 16:
                state = "match"
                continue
            m_pos = len(dst) - (1 + 0x0800)
            m_pos -= t >> 2
            m_pos -= src[ip] << 2; ip += 1
            copy_match(m_pos, 3)
            state = "match_done"
            continue

        if state == "match":
            if t >= 64:
                m_pos = len(dst) - 1
                m_pos -= (t >> 2) & 7
                m_pos -= src[ip] << 3; ip += 1
                t = (t >> 5) - 1
                copy_match(m_pos, t + 2)
            elif t >= 32:
                t &= 31
                if t == 0:
                    while src[ip] == 0:
                        t += 255
                        ip += 1
                    t += 31 + src[ip]; ip += 1
                m_pos = len(dst) - 1
                m_pos -= (src[ip] >> 2) + (src[ip + 1] << 6)
                ip += 2
                copy_match(m_pos, t + 2)
            elif t >= 16:
                m_pos = len(dst)
                m_pos -= (t & 8) << 11
                t &= 7
                if t == 0:
                    while src[ip] == 0:
                        t += 255
                        ip += 1
                    t += 7 + src[ip]; ip += 1
                m_pos -= (src[ip] >> 2) + (src[ip + 1] << 6)
                ip += 2
                if m_pos == len(dst):
                    break  # EOF
                m_pos -= 0x4000
                copy_match(m_pos, t + 2)
            else:
                m_pos = len(dst) - 1
                m_pos -= t >> 2
                m_pos -= src[ip] << 2; ip += 1
                copy_match(m_pos, 2)
            state = "match_done"
            continue

        if state == "match_done":
            t = src[ip - 2] & 3
            if t == 0:
                state = "loop"
                continue
            state = "match_next"
            continue

        if state == "match_next":
            dst += src[ip:ip + t]
            ip += t
            t = src[ip]; ip += 1
            state = "match"
            continue

    if len(dst) != out_len:
        raise ValueError(f"LZO size mismatch {len(dst)} != {out_len}")
    return bytes(dst)


class Reader:
    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.d = data
        self.p = pos

    def i32(self) -> int:
        (v,) = struct.unpack_from("<i", self.d, self.p)
        self.p += 4
        return v

    def u32(self) -> int:
        (v,) = struct.unpack_from("<I", self.d, self.p)
        self.p += 4
        return v

    def u64(self) -> int:
        (v,) = struct.unpack_from("<Q", self.d, self.p)
        self.p += 8
        return v

    def raw(self, n: int) -> bytes:
        v = bytes(self.d[self.p : self.p + n])
        self.p += n
        return v

    def fstring(self) -> tuple[str, bool]:
        """Returns (text, is_utf16)."""
        n = self.i32()
        if n == 0:
            return "", False
        if n > 0:
            return self.raw(n).rstrip(b"\0").decode("latin-1"), False
        return self.raw(-2 * n).decode("utf-16-le").rstrip("\0"), True


def w_fstring(text: str, utf16: bool = False) -> bytes:
    if not text and not utf16:
        return struct.pack("<i", 0)
    if utf16:
        raw = (text + "\0").encode("utf-16-le")
        return struct.pack("<i", -(len(raw) // 2)) + raw
    raw = (text + "\0").encode("latin-1")
    return struct.pack("<i", len(raw)) + raw


@dataclass
class Summary:
    file_version: int
    licensee_version: int
    header_size: int
    folder: tuple[str, bool]
    package_flags: int
    name_count: int
    name_offset: int
    export_count: int
    export_offset: int
    import_count: int
    import_offset: int
    depends_offset: int
    import_export_guids_offset: int
    import_guids_count: int
    export_guids_count: int
    thumbnail_table_offset: int
    guid: bytes
    generations: list[tuple[int, int, int]]
    engine_version: int
    cooker_version: int
    compression_flags: int
    chunks: list[tuple[int, int, int, int]]
    package_source: int
    additional_packages: list[tuple[str, bool]]
    texture_allocations: bytes  # serialized FTextureAllocations, kept verbatim
    size: int                   # bytes the summary occupied in the source file

    @classmethod
    def read(cls, data: bytes) -> Summary:
        r = Reader(data)
        if r.u32() != TAG:
            raise ValueError("not an Unreal package")
        ver = r.u32()
        fields = dict(file_version=ver & 0xFFFF, licensee_version=ver >> 16, header_size=r.i32(), folder=r.fstring(),
                      package_flags=r.u32(), name_count=r.i32(), name_offset=r.i32(), export_count=r.i32(),
                      export_offset=r.i32(), import_count=r.i32(), import_offset=r.i32(), depends_offset=r.i32(),
                      import_export_guids_offset=r.i32(), import_guids_count=r.i32(), export_guids_count=r.i32(),
                      thumbnail_table_offset=r.i32(), guid=r.raw(16))
        if fields["file_version"] != 832:
            raise ValueError(f"unsupported file version {fields['file_version']}")
        fields["generations"] = [(r.i32(), r.i32(), r.i32()) for _ in range(r.i32())]
        fields["engine_version"] = r.i32()
        fields["cooker_version"] = r.i32()
        fields["compression_flags"] = r.u32()
        fields["chunks"] = [(r.i32(), r.i32(), r.i32(), r.i32()) for _ in range(r.i32())]
        fields["package_source"] = r.u32()
        fields["additional_packages"] = [r.fstring() for _ in range(r.i32())]
        start = r.p
        for _ in range(r.i32()):              # FTextureType: SizeX, SizeY, NumMips, Format, TexCreateFlags, ExportIndices
            r.p += 20
            count = r.i32()                   # read first: "r.p += 4 * r.i32()" would drop the count's own 4 bytes
            r.p += 4 * count
        fields["texture_allocations"] = bytes(data[start : r.p])
        fields["size"] = r.p
        return cls(**fields)

    def write(self) -> bytes:
        out = bytearray()
        out += struct.pack("<II", TAG, self.file_version | (self.licensee_version << 16))
        out += struct.pack("<i", self.header_size) + w_fstring(*self.folder)
        out += struct.pack("<I", self.package_flags)
        out += struct.pack("<7i", self.name_count, self.name_offset, self.export_count, self.export_offset,
                           self.import_count, self.import_offset, self.depends_offset)
        out += struct.pack("<4i", self.import_export_guids_offset, self.import_guids_count, self.export_guids_count,
                           self.thumbnail_table_offset)
        out += self.guid
        out += struct.pack("<i", len(self.generations)) + b"".join(struct.pack("<3i", *g) for g in self.generations)
        out += struct.pack("<iiI", self.engine_version, self.cooker_version, self.compression_flags)
        out += struct.pack("<i", len(self.chunks)) + b"".join(struct.pack("<4i", *c) for c in self.chunks)
        out += struct.pack("<I", self.package_source)
        out += struct.pack("<i", len(self.additional_packages)) + b"".join(w_fstring(*a) for a in self.additional_packages)
        out += self.texture_allocations
        return bytes(out)


@dataclass
class Name:
    text: str
    flags: int
    pos: int


@dataclass
class Import:
    index: int                 # -1, -2, ...
    class_package: str
    class_name: str
    outer: int
    name: str
    name_idx: int
    name_num: int
    pos: int                   # byte offset of this entry in the uncompressed layout


@dataclass
class Export:
    index: int                 # 1, 2, ...
    class_index: int
    super_index: int
    outer: int
    name: str
    name_idx: int
    name_num: int
    archetype: int
    flags: int
    serial_size: int
    serial_offset: int
    export_flags: int
    net_counts: list[int]
    package_guid: bytes
    package_flags: int
    pos: int

    # field offsets inside an export entry
    NAME_NUM_AT = 16
    FLAGS_AT = 24
    SERIAL_SIZE_AT = 32
    SERIAL_OFFSET_AT = 36


PROPERTY_TYPES = frozenset({
    "ByteProperty", "IntProperty", "BoolProperty", "FloatProperty", "ObjectProperty", "NameProperty",
    "StrProperty", "StructProperty", "ArrayProperty", "ClassProperty", "ComponentProperty",
    "DelegateProperty", "InterfaceProperty", "MapProperty", "StringRefProperty",
    # Gearbox attribute-backed properties (TPS uses FloatAttributeProperty on its pawn)
    "FloatAttributeProperty", "IntAttributeProperty", "ByteAttributeProperty",
})


@dataclass
class Tag:
    name: str
    type: str
    struct_name: str     # StructProperty's struct / ByteProperty's enum ("None" for a plain byte)
    array_index: int
    tag_pos: int
    value_pos: int
    size: int


@dataclass
class Package:
    path: Path
    summary: Summary
    buf: bytearray                       # uncompressed layout, patched in place
    names: list[Name] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    exports: list[Export] = field(default_factory=list)

    # ------------------------------------------------------------------ reading
    @classmethod
    def load(cls, path: str | Path) -> Package:
        path = Path(path)
        data = path.read_bytes()
        s = Summary.read(data)
        pkg = cls(path, s, bytearray(_uncompressed(data, s)))
        pkg._read_tables()
        return pkg

    def _fname(self, r: Reader) -> tuple[str, int, int]:
        idx, num = r.i32(), r.i32()
        text = self.names[idx].text if 0 <= idx < len(self.names) else f"<bad name {idx}>"
        return (text if num == 0 else f"{text}_{num - 1}"), idx, num

    def _read_tables(self) -> None:
        s, buf = self.summary, self.buf
        r = Reader(buf, s.name_offset)
        for _ in range(s.name_count):
            pos = r.p
            text, _utf16 = r.fstring()
            self.names.append(Name(text, r.u64(), pos))
        r = Reader(buf, s.import_offset)
        for i in range(s.import_count):
            pos = r.p
            cpkg, _, _ = self._fname(r)
            cname, _, _ = self._fname(r)
            outer = r.i32()
            name, idx, num = self._fname(r)
            self.imports.append(Import(-(i + 1), cpkg, cname, outer, name, idx, num, pos))
        r = Reader(buf, s.export_offset)
        for i in range(s.export_count):
            pos = r.p
            cls_i, sup, outer = r.i32(), r.i32(), r.i32()
            name, idx, num = self._fname(r)
            arch, flags, size, off, eflags = r.i32(), r.u64(), r.i32(), r.i32(), r.u32()
            nets = [r.i32() for _ in range(r.i32())]
            guid, pflags = r.raw(16), r.u32()
            self.exports.append(Export(i + 1, cls_i, sup, outer, name, idx, num, arch, flags, size, off, eflags,
                                       nets, guid, pflags, pos))

    # ------------------------------------------------------------------ queries
    def obj(self, index: int) -> Import | Export | None:
        if index > 0:
            return self.exports[index - 1]
        if index < 0:
            return self.imports[-index - 1]
        return None

    def path_of(self, index: int) -> str:
        parts = []
        while index:
            o = self.obj(index)
            parts.append(o.name)
            index = o.outer
        return ".".join(reversed(parts))

    def class_of(self, export: Export) -> str:
        """Full path of an export's class ("Core.Package", "WillowGame.SkillDefinition", ...)."""
        if export.class_index == 0:
            return "Core.Class"
        return self.path_of(export.class_index)

    def top_level(self, export: Export) -> Export:
        while export.outer > 0:
            export = self.exports[export.outer - 1]
        return export

    def export_data(self, export: Export) -> bytes:
        return bytes(self.buf[export.serial_offset : export.serial_offset + export.serial_size])

    def _fname_at(self, pos: int) -> str:
        idx, num = struct.unpack_from("<ii", self.buf, pos)
        if not 0 <= idx < len(self.names):
            raise ValueError(f"bad name index {idx} at {pos}")
        text = self.names[idx].text
        return text if num == 0 else f"{text}_{num - 1}"

    def tagged_properties(self, export: Export) -> tuple[list[Tag], int]:
        """The export's top-level tagged properties and the offset just past their "None" terminator.

        Export data starts with the object's NetIndex (int32), then the tags; components first carry
        TemplateOwnerClass (int32) and TemplateName (FName). Native data, if the class has any,
        follows the terminator.
        """
        start = export.serial_offset + 4
        end = export.serial_offset + export.serial_size
        for skip in (0, 12, 4):
            try:
                return self.tags_at(start + skip, end)
            except (ValueError, struct.error):
                continue
        raise ValueError(f"{self.path_of(export.index)}: no tagged-property stream found")

    def tags_at(self, pos: int, end: int) -> tuple[list[Tag], int]:
        """Parse one tag list (a UObject's, or a non-immutable struct's) from pos."""
        tags: list[Tag] = []
        while pos < end:
            tag_pos = pos
            name = self._fname_at(pos)
            pos += 8
            if name == "None":
                return tags, pos
            typ = self._fname_at(pos)
            if typ not in PROPERTY_TYPES:
                raise ValueError(f"not a property tag at {tag_pos} ({name}: {typ})")
            pos += 8
            size, array_index = struct.unpack_from("<ii", self.buf, pos)
            pos += 8
            struct_name = ""
            if typ in ("StructProperty", "ByteProperty"):
                struct_name = self._fname_at(pos)
                pos += 8
            if typ == "BoolProperty":        # the value is one byte in the tag itself
                tags.append(Tag(name, typ, struct_name, array_index, tag_pos, pos, 1))
                pos += 1
                continue
            if size < 0 or pos + size > end:
                raise ValueError(f"tag {name} ({typ}) at {tag_pos} runs past the data")
            tags.append(Tag(name, typ, struct_name, array_index, tag_pos, pos, size))
            pos += size
        raise ValueError("tag list without a None terminator")

    # ------------------------------------------------------------------ references
    def is_object_index(self, value: int) -> bool:
        return value != 0 and -len(self.imports) <= value <= len(self.exports)

    def references(self, export: Export) -> set[int]:
        """Object indices this export refers to: its outer and archetype, and every reference found
        in its tagged properties (recursing into structs and arrays of structs). Native data is not
        scanned, except SkeletalMesh's material list."""
        refs: set[int] = set()
        for idx in (export.outer, export.archetype):
            if idx:
                refs.add(idx)
        try:
            tags, props_end = self.tagged_properties(export)
        except ValueError:
            return refs
        self._collect_refs(tags, refs)
        cls = self.class_of(export)
        if cls == "GearboxFramework.BehaviorProviderDefinition":
            refs |= self._bpd_variable_refs(export, props_end)
        if cls == "Engine.SkeletalMesh":
            count = struct.unpack_from("<i", self.buf, props_end + 28)[0]   # after FBoxSphereBounds
            if 0 <= count < 64:
                for k in range(count):
                    idx = struct.unpack_from("<i", self.buf, props_end + 32 + 4 * k)[0]
                    if self.is_object_index(idx):
                        refs.add(idx)
        return refs

    def _bpd_variable_refs(self, export: Export, props_end: int) -> set[int]:
        """A behaviour graph's variable values (the attributes, damage types, explosions... its BVAR_ variables
        hold) are native data after the tagged properties, not tags: 4-byte words, each value a packed
        (variable << 16 | 1) header and its data (an attribute is 0, the definition, 0, 1.0f). Without them a
        closure drops e.g. Long Range Killer's Att_LongRangeKiller_MaxDistance and the skill reads 0 (2026-10-01).
        The layout isn't fully decoded, so any word that names a data definition counts; a stray match only keeps
        one more object."""
        refs: set[int] = set()
        end = export.serial_offset + export.serial_size
        for pos in range(props_end, end - 3, 4):
            idx = struct.unpack_from("<i", self.buf, pos)[0]
            if idx > 0 and self.is_object_index(idx) and self.class_of(self.exports[idx - 1]).endswith(
                    ("Definition", "AkEvent")):
                refs.add(idx)
        return refs

    def _collect_refs(self, tags: list[Tag], refs: set[int]) -> None:
        for t in tags:
            if t.type in ("ObjectProperty", "ComponentProperty", "ClassProperty", "InterfaceProperty", "DelegateProperty"):
                idx = struct.unpack_from("<i", self.buf, t.value_pos)[0]
                if self.is_object_index(idx):
                    refs.add(idx)
            elif t.type == "StructProperty":
                try:
                    inner, _ = self.tags_at(t.value_pos, t.value_pos + t.size)
                except (ValueError, struct.error):
                    continue                     # immutable struct stored raw (Vector, Rotator, ...)
                self._collect_refs(inner, refs)
            elif t.type == "ArrayProperty" and t.size >= 4:
                count = struct.unpack_from("<i", self.buf, t.value_pos)[0]
                body = t.size - 4
                if count <= 0:
                    continue
                if body == 4 * count:
                    values = struct.unpack_from(f"<{count}i", self.buf, t.value_pos + 4)
                    if all(v == 0 or self.is_object_index(v) for v in values):
                        refs.update(v for v in values if v)
                    continue
                pos, end = t.value_pos + 4, t.value_pos + t.size
                try:
                    for _ in range(count):
                        inner, pos = self.tags_at(pos, end)
                        self._collect_refs(inner, refs)
                except (ValueError, struct.error):
                    continue

    # ------------------------------------------------------------------ in-place edits (layout-neutral)
    def set_export_flags(self, export: Export, flags: int) -> None:
        export.flags = flags
        struct.pack_into("<Q", self.buf, export.pos + Export.FLAGS_AT, flags)

    def filter_export(self, export: Export) -> None:
        """Set the NotFor* flags. BL2 ignores them at load; kept for experiments only."""
        self.set_export_flags(export, export.flags | RF_FILTERED)

    def set_class_index(self, export: Export, class_index: int) -> None:
        export.class_index = class_index
        struct.pack_into("<i", self.buf, export.pos, class_index)

    def disable_export(self, export: Export, missing_class_import: int) -> None:
        """Make the engine skip this export: point its class at an import the target game lacks.
        The linker can't create an object of a missing class, and references to it load as None."""
        self.set_class_index(export, missing_class_import)

    def find_import(self, path: str) -> Import | None:
        for imp in self.imports:
            if self.path_of(imp.index) == path:
                return imp
        return None

    def rename_import(self, imp: Import, new_name: str) -> None:
        """Point an import at another object name already in the name table (layout-neutral): e.g. a TPS-only
        class import renamed to a BL2 class, so every export of that class becomes the BL2 class."""
        idx = next((i for i, n in enumerate(self.names) if n.text == new_name), None)
        if idx is None:
            raise ValueError(f"{new_name!r} is not in the name table")
        struct.pack_into("<ii", self.buf, imp.pos + 20, idx, 0)       # after ClassPackage, ClassName, Outer
        imp.name, imp.name_idx, imp.name_num = new_name, idx, 0

    def set_export_name_number(self, export: Export, number: int) -> None:
        """Rename by FName number: "Foo" becomes "Foo_<number-1>" without touching the name table."""
        export.name_num = number
        struct.pack_into("<i", self.buf, export.pos + Export.NAME_NUM_AT, number)
        base = self.names[export.name_idx].text
        export.name = base if number == 0 else f"{base}_{number - 1}"

    def set_serial_size(self, export: Export, size: int) -> None:
        export.serial_size = size
        struct.pack_into("<i", self.buf, export.pos + Export.SERIAL_SIZE_AT, size)

    def relocate_export(self, export: Export, data: bytes) -> int:
        """Give an export new serialized data of any size: appended at the end of the file, the export entry pointed
        at it (two fixed-size ints, so no table moves). The old bytes stay as dead space. Absolute offsets inside
        `data` (inline bulk data) must already point at the returned offset."""
        offset = len(self.buf)
        if not isinstance(self.buf, bytearray):
            self.buf = bytearray(self.buf)
        self.buf += data
        export.serial_offset, export.serial_size = offset, len(data)
        struct.pack_into("<ii", self.buf, export.pos + Export.SERIAL_SIZE_AT, len(data), offset)
        return offset

    # ------------------------------------------------------------------ writing
    def save(self, path: str | Path, engine_version: int | None = None, cooker_version: int | None = None) -> Path:
        s = self.summary
        s.compression_flags = 0
        s.chunks = []
        s.package_flags &= ~PKG_STORE_COMPRESSED
        if engine_version is not None:
            s.engine_version = engine_version
        if cooker_version is not None:
            s.cooker_version = cooker_version
        head = s.write()
        first_table = min(s.name_offset, s.import_offset, s.export_offset)
        if len(head) > first_table:
            raise ValueError(f"summary ({len(head)} bytes) no longer fits before the tables at {first_table}")
        out = bytearray(self.buf)
        out[:first_table] = head + b"\0" * (first_table - len(head))
        path = Path(path)
        path.write_bytes(out)
        return path


def _uncompressed(data: bytes, s: Summary) -> bytes:
    """The package in its uncompressed layout (the chunks placed at their uncompressed offsets)."""
    if not s.chunks:
        return data
    if s.compression_flags != COMPRESS_LZO:
        raise ValueError(f"unsupported compression {s.compression_flags}")
    total = max(u_off + u_size for u_off, u_size, _, _ in s.chunks)
    out = bytearray(total)
    out[: s.size] = data[: s.size]
    for u_off, u_size, c_off, _c_size in s.chunks:
        r = Reader(data, c_off)
        if r.u32() != TAG:
            raise ValueError("bad chunk tag")
        r.i32()                                  # block size
        r.i32()                                  # total compressed
        total_u = r.i32()
        blocks, acc = [], 0
        while acc < total_u:
            bc, bu = r.i32(), r.i32()
            blocks.append((bc, bu))
            acc += bu
        p, at = r.p, u_off
        for bc, bu in blocks:
            out[at : at + bu] = lzo1x_decompress(data[p : p + bc], bu)
            p += bc
            at += bu
        if at - u_off != u_size:
            raise ValueError(f"chunk at {u_off}: got {at - u_off} bytes, expected {u_size}")
    return bytes(out)
