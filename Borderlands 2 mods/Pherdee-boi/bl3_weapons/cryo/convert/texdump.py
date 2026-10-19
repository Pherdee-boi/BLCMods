"""A Texture2D's mips out of a package, decoded; and the encoders for the textures the build makes itself.

Reads the texture's native data after its tagged properties (UE3, BL2/TPS cooked layout): an empty SourceArt bulk
header, then the mip array, each mip a bulk-data header (flags, element count, size on disk, offset), its payload
when stored inline, and its size. Mips stored in a texture file cache (flag 0x1) are read from
`<TextureFileCacheName>.tfc` in the given directories; LZO-compressed payloads (flag 0x10) use the package code's
chunk format. DXT1, DXT5, G8 and A8R8G8B8 are decoded.

From a shell, `python tools/cli.py texdump <package> <texture path> <out.png> [--tfc-dir DIR ...] [--mip N]` writes
one as a PNG.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from props import ctx_for, props
from upk import TAG, Package, lzo1x_decompress

SEPARATE_FILE = 0x1
COMPRESSED_ZLIB = 0x2
COMPRESSED_LZO = 0x10
UNUSED = 0x20


def _tags(pkg: Package, export) -> tuple[dict[str, str], int]:
    tags, end = pkg.tagged_properties(export)
    lines = props(ctx_for(pkg), tags[0].tag_pos, end)[1] if tags else []
    values = {}
    for line in lines:
        if " = " in line and not line.startswith(" "):
            key, value = line.split(" = ", 1)
            values[key.strip()] = value.strip().strip('"')
    return values, end


def _chunks(data: bytes, pos: int, flags: int) -> bytes:
    """A compressed bulk payload: TAG, block size, (total compressed, total uncompressed), block sizes, blocks."""
    tag, _block, _total_c, total_u = struct.unpack_from("<Iiii", data, pos)
    if tag != TAG:
        raise ValueError("bad chunk tag in bulk data")
    p = pos + 16
    blocks, acc = [], 0
    while acc < total_u:
        bc, bu = struct.unpack_from("<ii", data, p)
        blocks.append((bc, bu))
        acc += bu
        p += 8
    out = bytearray()
    for bc, bu in blocks:
        chunk = data[p:p + bc]
        out += lzo1x_decompress(chunk, bu) if flags & COMPRESSED_LZO else zlib.decompress(chunk)
        p += bc
    return bytes(out)


def mips(pkg: Package, export, tfc_dirs: list[Path], skip: int = 0) -> tuple[dict[str, str], list[tuple[int, int, bytes | None]]]:
    """Every mip's size and data (None where it can't be read). The first `skip` mips that live in a texture file
    cache aren't read at all (their data is None): TPS's caches are large and LZO-compressed."""
    values, end = _tags(pkg, export)
    buf = pkg.buf
    pos = end
    # SourceArt (UTexture): a bulk header, and a payload when inline and used
    flags, count, size, _off = struct.unpack_from("<Iiii", buf, pos)
    pos += 16
    if not flags & (SEPARATE_FILE | UNUSED) and size > 0:
        pos += size
    n = struct.unpack_from("<i", buf, pos)[0]
    pos += 4
    out = []
    cache = values.get("TextureFileCacheName")
    for index in range(n):
        flags, count, size, offset = struct.unpack_from("<Iiii", buf, pos)
        pos += 16
        payload: bytes | None = None
        if flags & UNUSED or (flags & SEPARATE_FILE and index < skip):
            payload = None
        elif flags & SEPARATE_FILE:
            tfc = next((d / f"{cache}.tfc" for d in tfc_dirs if (d / f"{cache}.tfc").is_file()), None) if cache else None
            if tfc is not None:
                with tfc.open("rb") as f:          # TPS's caches run to 1.5 GB: read only this mip
                    f.seek(offset)
                    raw = f.read(size)
                payload = _chunks(raw, 0, flags) if flags & (COMPRESSED_LZO | COMPRESSED_ZLIB) else raw
        else:
            raw = bytes(buf[pos:pos + size])
            pos += size
            payload = _chunks(raw, 0, flags) if flags & (COMPRESSED_LZO | COMPRESSED_ZLIB) else raw
        sx, sy = struct.unpack_from("<ii", buf, pos)
        pos += 8
        out.append((sx, sy, payload))
    return values, out


def _rgb565(c: int) -> tuple[int, int, int]:
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _dxt_colors(block: bytes, dxt1: bool) -> list[tuple[int, int, int, int]]:
    c0, c1 = struct.unpack_from("<HH", block, 0)
    a, b = _rgb565(c0), _rgb565(c1)
    if c0 > c1 or not dxt1:
        cs = [a, b, tuple((2 * x + y) // 3 for x, y in zip(a, b)), tuple((x + 2 * y) // 3 for x, y in zip(a, b))]
        return [(*c, 255) for c in cs]
    return [(*a, 255), (*b, 255), (*tuple((x + y) // 2 for x, y in zip(a, b)), 255), (0, 0, 0, 0)]


def decode(fmt: str, sx: int, sy: int, data: bytes) -> list[bytes]:
    """Rows of RGBA bytes."""
    rows = [bytearray(sx * 4) for _ in range(sy)]
    if fmt in ("PF_DXT1", "PF_DXT5"):
        dxt1 = fmt == "PF_DXT1"
        size = 8 if dxt1 else 16
        bw, bh = max(1, (sx + 3) // 4), max(1, (sy + 3) // 4)
        for by in range(bh):
            for bx in range(bw):
                off = (by * bw + bx) * size
                block = data[off:off + size]
                if len(block) < size:
                    continue
                alpha = None
                if not dxt1:
                    a0, a1 = block[0], block[1]
                    bits = int.from_bytes(block[2:8], "little")
                    table = [a0, a1] + ([((6 - i) * a0 + (1 + i) * a1) // 7 for i in range(6)] if a0 > a1 else
                                        [((4 - i) * a0 + (1 + i) * a1) // 5 for i in range(4)] + [0, 255])
                    alpha = [table[(bits >> (3 * i)) & 7] for i in range(16)]
                    block = block[8:]
                colors = _dxt_colors(block, dxt1)
                idx = struct.unpack_from("<I", block, 4)[0]
                for i in range(16):
                    x, y = bx * 4 + i % 4, by * 4 + i // 4
                    if x >= sx or y >= sy:
                        continue
                    r, g, b, a = colors[(idx >> (2 * i)) & 3]
                    if alpha is not None:
                        a = alpha[i]
                    rows[y][x * 4:x * 4 + 4] = bytes((r, g, b, a))
    elif fmt == "PF_G8":
        for y in range(sy):
            for x in range(sx):
                v = data[y * sx + x]
                rows[y][x * 4:x * 4 + 4] = bytes((v, v, v, 255))
    elif fmt == "PF_A8R8G8B8":
        for y in range(sy):
            for x in range(sx):
                b, g, r, a = data[(y * sx + x) * 4:(y * sx + x) * 4 + 4]
                rows[y][x * 4:x * 4 + 4] = bytes((r, g, b, a))
    else:
        raise ValueError(f"format {fmt} not supported")
    return [bytes(r) for r in rows]


def luminance(fmt: str, sx: int, sy: int, data: bytes) -> list[list[int]]:
    rows = decode(fmt, sx, sy, data)
    return [[(r[x * 4] * 299 + r[x * 4 + 1] * 587 + r[x * 4 + 2] * 114) // 1000 for x in range(sx)] for r in rows]


def downsample(image: list[list[int]], sx: int, sy: int) -> list[list[int]]:
    """Area average of a grey image down to sx x sy (a mip level)."""
    h, w = len(image), len(image[0])
    fx, fy = w / sx, h / sy
    out = []
    for y in range(sy):
        y0, y1 = int(y * fy), max(int(y * fy) + 1, int((y + 1) * fy))
        row = []
        for x in range(sx):
            x0, x1 = int(x * fx), max(int(x * fx) + 1, int((x + 1) * fx))
            total = sum(sum(image[j][x0:x1]) for j in range(y0, min(y1, h)))
            row.append(total // max(1, (min(y1, h) - y0) * (x1 - x0)))
        out.append(row)
    return out


def encode_cutout(image: list[list[int]], threshold: int = 128) -> bytes:
    """A grey image as DXT1 with 1-bit alpha: pixels at or above `threshold` opaque white, the rest transparent. Blocks
    use DXT1's three-colour mode (c0 <= c1, index 3 = transparent black), so a canvas can draw the shape in any tint."""
    sy, sx = len(image), len(image[0])
    bw, bh = max(1, (sx + 3) // 4), max(1, (sy + 3) // 4)
    out = bytearray()
    for by in range(bh):
        for bx in range(bw):
            bits = 0
            for i in range(16):
                x, y = bx * 4 + i % 4, by * 4 + i // 4
                lit = x < sx and y < sy and image[y][x] >= threshold
                bits |= (0 if lit else 3) << (2 * i)
            out += struct.pack("<HHI", 0xFFFF, 0xFFFF, bits)
    return bytes(out)
