"""Packages read by their tables alone: which package exports an object, and TPS's fully compressed packages.

Only the compressed chunks that hold the name, import and export tables are decompressed (python-lzo when installed,
else upk's pure-Python LZO): fast enough for a whole install. The build finds the textures it carries and the
classes of its blank objects this way.

From a shell, `python tools/cli.py findexport <folder> [...] --name <object> [--class Texture2D]` prints every package
that exports an object of that name, with its full path, class and size (RESEARCH.md §7, "Measured against TPS").
"""

from __future__ import annotations

import struct
from pathlib import Path

from upk import TAG, Package, Reader, Summary, lzo1x_decompress

try:
    import lzo  # type: ignore[import-not-found]

    def _lzo(data: bytes, size: int) -> bytes:
        return lzo.decompress(data, False, size)
except ImportError:  # pragma: no cover - slower fallback
    _lzo = lzo1x_decompress


def fully_compressed(data: bytes) -> bytes | None:
    """A fully compressed file's package (Startup.upk and the script packages: one LZO stream, no summary first)."""
    r = Reader(data, 0)
    if r.u32() != TAG:
        return None
    r.i32()                                       # block size
    r.i32()                                       # compressed total
    total_u = r.i32()
    blocks, acc = [], 0
    while acc < total_u:
        bc, bu = r.i32(), r.i32()
        blocks.append((bc, bu))
        acc += bu
    out, p = bytearray(), r.p
    for bc, bu in blocks:
        out += _lzo(data[p : p + bc], bu)
        p += bc
    return bytes(out)


class PartialPackage(Package):
    """A fully compressed package with only the blocks read that something needs: its tables at load, any export's
    data on `ensure_export` (pure-Python LZO reads about 10 MB/s; Startup.upk unpacks to 79 MB, its tables to 5)."""

    blocks: list[tuple[int, int, int, int]]       # compressed offset, compressed size, uncompressed offset, size
    raw: bytes
    done: set[int]

    @classmethod
    def open(cls, path: Path) -> PartialPackage:
        raw = path.read_bytes()
        r = Reader(raw, 0)
        if r.u32() != TAG:
            raise ValueError(f"{path.name}: not a fully compressed package")
        r.i32()
        r.i32()
        total_u = r.i32()
        sizes, acc = [], 0
        while acc < total_u:
            bc, bu = r.i32(), r.i32()
            sizes.append((bc, bu))
            acc += bu
        blocks, c_off, u_off = [], r.p, 0
        for bc, bu in sizes:
            blocks.append((c_off, bc, u_off, bu))
            c_off += bc
            u_off += bu
        buf = bytearray(total_u)
        pkg = cls.__new__(cls)
        pkg.path, pkg.buf, pkg.names, pkg.imports, pkg.exports = path, buf, [], [], []
        pkg.blocks, pkg.raw, pkg.done = blocks, raw, set()
        pkg.ensure(0, min(total_u, 1 << 16))
        pkg.summary = Summary.read(bytes(buf[: 1 << 16]))
        s = pkg.summary
        pkg.ensure(s.name_offset, s.depends_offset if s.depends_offset > 0 else total_u)
        pkg._read_tables()                     # noqa: SLF001 - same tool family
        return pkg

    def ensure(self, start: int, end: int) -> None:
        for i, (c_off, c_size, u_off, u_size) in enumerate(self.blocks):
            if i in self.done or u_off + u_size <= start or u_off >= end:
                continue
            self.buf[u_off:u_off + u_size] = _lzo(self.raw[c_off:c_off + c_size], u_size)
            self.done.add(i)

    def ensure_export(self, export) -> None:
        self.ensure(export.serial_offset, export.serial_offset + export.serial_size)


def load_any(path: Path) -> Package | None:
    """A whole package, fully compressed or not."""
    data = path.read_bytes()
    if struct.unpack_from("<I", data, 0)[0] == TAG and struct.unpack_from("<H", data, 4)[0] != 832:
        data = fully_compressed(data)
        if data is None:
            return None
        s = Summary.read(data)
        pkg = Package(path, s, bytearray(data))
    else:
        return Package.load(path)
    pkg._read_tables()                            # noqa: SLF001 - same tool family
    return pkg


def _tables(path: Path) -> Package | None:
    """The package with only its tables readable (export data is not decompressed)."""
    with path.open("rb") as fh:
        head = fh.read(1 << 20)
        try:
            s = Summary.read(head)
        except (ValueError, struct.error):
            try:
                return load_any(path)
            except (ValueError, struct.error, IndexError, UnicodeDecodeError):
                return None
        end = s.depends_offset if s.depends_offset > 0 else s.export_offset + 1
        if not s.chunks:
            fh.seek(0)
            data = fh.read(max(end, s.size) + (1 << 16))
            buf = bytearray(data)
        else:
            total = max(u + n for u, n, _c, _k in s.chunks)
            buf = bytearray(min(total, end + (1 << 20)))
            buf[: s.size] = head[: s.size]
            for u_off, u_size, c_off, c_size in s.chunks:
                if u_off >= end or u_off + u_size <= s.name_offset:
                    continue
                fh.seek(c_off)
                chunk = fh.read(c_size)
                r = Reader(chunk, 0)
                if r.u32() != TAG:
                    return None
                r.i32()
                r.i32()
                total_u = r.i32()
                blocks, acc = [], 0
                while acc < total_u:
                    bc, bu = r.i32(), r.i32()
                    blocks.append((bc, bu))
                    acc += bu
                p, at = r.p, u_off
                for bc, bu in blocks:
                    if at + bu > len(buf):
                        buf.extend(bytes(at + bu - len(buf)))
                    buf[at : at + bu] = _lzo(chunk[p : p + bc], bu)
                    p += bc
                    at += bu
    pkg = Package(path, s, buf)
    try:
        pkg._read_tables()                       # noqa: SLF001 - same tool family
    except (ValueError, struct.error, IndexError, UnicodeDecodeError):
        return None
    return pkg
