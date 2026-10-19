"""Writing Scaleform movies by hand: the bit fields, rectangles, matrices and tags of the SWF format.

BL2 shows a picture through a movie, and a few of the mod's pictures have none in BL2 (her skill icons, cryo's icon on
item cards), so small movies are built at runtime. This is the part of the format they share; each module lays out its
own movie with it.
"""

from __future__ import annotations

import struct


def signed_bits(*values: int) -> int:
    """Width of signed bit fields that hold all values (0 when all are zero, as Flash writes them)."""
    if not any(values):
        return 0
    return max(v.bit_length() if v >= 0 else (~v).bit_length() for v in values) + 1


class Bits:
    def __init__(self) -> None:
        self._out = bytearray()
        self._acc = 0
        self._count = 0

    def put(self, value: int, width: int) -> Bits:
        for shift in range(width - 1, -1, -1):
            self._acc = (self._acc << 1) | ((value >> shift) & 1)
            self._count += 1
            if self._count == 8:
                self._out.append(self._acc)
                self._acc = self._count = 0
        return self

    def signed(self, width: int, *values: int) -> Bits:
        for value in values:
            self.put(value & ((1 << width) - 1), width)
        return self

    def done(self) -> bytes:
        if self._count:
            self._out.append(self._acc << (8 - self._count))
            self._acc = self._count = 0
        return bytes(self._out)


def rect(xmin: int, xmax: int, ymin: int, ymax: int) -> bytes:
    width = signed_bits(xmin, xmax, ymin, ymax)
    return Bits().put(width, 5).signed(width, xmin, xmax, ymin, ymax).done()


def scale_matrix(scale: float, tx: int, ty: int) -> bytes:
    fixed = round(scale * 0x10000)
    scale_bits, move_bits = signed_bits(fixed), signed_bits(tx, ty)
    bits = Bits().put(1, 1).put(scale_bits, 5).signed(scale_bits, fixed, fixed)
    return bits.put(0, 1).put(move_bits, 5).signed(move_bits, tx, ty).done()


def pstr(text: str) -> bytes:
    raw = text.encode("ascii")
    return bytes([len(raw)]) + raw


def tag(code: int, data: bytes = b"") -> bytes:
    if len(data) < 0x3F:
        return struct.pack("<H", code << 6 | len(data)) + data
    return struct.pack("<HI", code << 6 | 0x3F, len(data)) + data
