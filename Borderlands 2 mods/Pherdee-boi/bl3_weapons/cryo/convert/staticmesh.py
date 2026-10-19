"""TPS -> BL2 StaticMesh data (decoded 2026-10-01; RESEARCH.md §7, "Cold as Ice's visuals").

TPS StaticMesh exports are a hard load failure in BL2, yet both games write package version 832, so the loader can't tell
the formats apart. Diffing meshes that both games ship (FX_Shared_Mesh.Meshes.3p_Buddy_Plane,
FX_GOR_Mesh.Meshes.Generic.Smesh_Blood_Drop, FX_CREA_Skag.Meshes.Smesh_FireBase) shows Gearbox changed two things for TPS:
- **Positions are quantized.** After Stride (8) and NumVertices come a byte 1, a byte 0, an FVector Bias and an FVector
  Scale, then a bulk array of four int16 per vertex: position = Bias + q / 32767 * Scale. BL2 has Stride 12 and plain
  FVectors. Unpacked, TPS's positions match BL2's floats to within 0.002 units.
- **BL2's trailer has one more zero byte** before the LightingGuid. After the last LOD (its index buffer, then an int 1)
  come the thumbnail angle (three ints) and distance, an empty source-mesh name and its CRC (zero), then the
  LightingGuid: 24 bytes before it in TPS, 25 in BL2. (Read once as an empty second index buffer, the 1 and the angle's
  pitch, which held only while the pitch was 0: SM_FX_SaveTheQueen's is -704.)
Everything else (bounds, kDOP tree, sections, tangents and UVs, colours, indices, the rest of the trailer) is
byte-identical.

Only the layout seen in her effect meshes is handled: no body setup, any number of LODs and UV channels, no colour
buffer, the 68-byte trailer. Anything else returns None, and the caller leaves that mesh disabled.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

TRAILER = 68


class _Reader:
    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.d, self.p = data, pos

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def skip(self, n: int) -> None:
        if n < 0 or self.p + n > len(self.d):
            raise ValueError("out of range")
        self.p += n

    def bulk(self) -> tuple[int, int, int]:
        """A bulk-serialized array: element size, count; returns (data start, size, count)."""
        size, count = self.i32(), self.i32()
        start = self.p
        self.skip(size * count)
        return start, size, count


@dataclass
class Lod:
    start: int                       # RawTriangles bulk header
    pos_start: int                   # position buffer: Stride
    pos_end: int
    end: int
    num_vertices: int = 0
    bias: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    positions: tuple[int, int, int] = (0, 0, 0)    # data start, element size, count


@dataclass
class Layout:
    lods: list[Lod] = field(default_factory=list)
    trailer: int = 0


def read_tps(data: bytes) -> Layout | None:
    """Where the parts of a TPS StaticMesh's native data are, or None if it isn't the handled layout."""
    try:
        r = _Reader(data)
        r.skip(28)                    # bounds: origin, box extent, sphere radius
        if r.i32() != 0:              # body setup: her effect meshes have none
            return None
        r.skip(24)                    # kDOP root bound
        r.bulk()                      # kDOP nodes
        r.bulk()                      # kDOP triangles
        r.skip(16)                    # internal version, three ints
        layout = Layout()
        for _ in range(r.i32()):
            start = r.p
            r.skip(16)                # RawTriangles: flags, count, size, offset (empty once cooked)
            if struct.unpack_from("<i", data, start + 8)[0] != 0:
                return None
            for _e in range(r.i32()):
                r.skip(36)            # material, two collision flags, shadows, first index, triangles, min/max, index
                r.skip(8 * r.i32())   # fragments
            r.skip(1)
            pos_start = r.p
            stride, count = r.i32(), r.i32()
            if stride != 8 or data[r.p] != 1:
                return None
            r.skip(2)
            bias = struct.unpack_from("<3f", data, r.p)
            scale = struct.unpack_from("<3f", data, r.p + 12)
            r.skip(24)
            positions = r.bulk()
            if positions[1] != 8 or positions[2] != count:
                return None
            pos_end = r.p
            r.skip(16)                # tangents and UVs: channels, stride, count, full-precision flag
            r.bulk()
            r.skip(4)                 # colour buffer stride
            if r.i32() != 0:          # colour buffer vertices: not handled
                return None
            num_vertices = r.i32()
            r.bulk()                  # index buffer
            if r.i32() != 1:          # then a 1, which ends the LOD
                return None
            layout.lods.append(Lod(start, pos_start, pos_end, r.p, num_vertices, bias, scale, positions))
        layout.trailer = r.p
        tail = data[r.p:]
        if len(tail) != TRAILER or any(tail[16:24]):      # no source-mesh name, no CRC
            return None
        return layout
    except (ValueError, struct.error, IndexError):
        return None


def to_bl2(data: bytes, base: int) -> bytes | None:
    """BL2's native data for a TPS StaticMesh's, to sit at absolute file offset `base` (inline bulk offsets point
    there). None if the layout isn't handled."""
    layout = read_tps(data)
    if layout is None or not layout.lods:
        return None
    out = bytearray(data[:layout.lods[0].start])
    for lod in layout.lods:
        head = bytearray(data[lod.start:lod.pos_start])
        struct.pack_into("<i", head, 12, base + len(out) + 16)      # RawTriangles: an empty bulk right after its header
        out += head
        start, size, count = lod.positions
        out += struct.pack("<4i", 12, count, 12, count)
        for i in range(count):
            x, y, z, _w = struct.unpack_from("<4h", data, start + size * i)
            out += struct.pack("<3f", lod.bias[0] + x / 32767 * lod.scale[0], lod.bias[1] + y / 32767 * lod.scale[1],
                               lod.bias[2] + z / 32767 * lod.scale[2])
        out += data[lod.pos_end:lod.end]
    tail = data[layout.trailer:]
    out += tail[:24] + b"\0" + tail[24:]
    return bytes(out)
