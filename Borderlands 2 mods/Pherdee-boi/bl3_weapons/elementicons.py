"""An icon of its own for a new element on BL2's item cards and HUD: the radiation symbol (a placeholder that is drawn in code).

BL2 shows an element's icon from two clips of one shared Scaleform movie, SharedWillowComponents (in WillowGame.upk), which the card,
the inventory and the HUD all use: the title icon (frame labels "shock", "fire", "corrosive", "explosive", "amp", "none") and the stat
rows' icon ("elementFire", "elementCorrosive", ...). A gun picks its title frame by name (WillowWeapon.ElementalFrame, set by
WillowWeapon:SetElementalFrame from its damage type's element, a fixed list that has no radiation).

So the movie gets one frame more at the end of each clip, "radiation" and "elementRadiation", that shows a new vector shape (the
trefoil, drawn here with straight edges and one solid colour, no picture needed), and a post-hook names that frame for radiation guns.
The change is made to the movie's data in memory at start-up, before the first map makes the game read it (the front end does not use
the movie; the game frees the data once it has read it); nothing on disk changes, and nothing of the game's art is copied: the shapes
are new. When the movie is not laid out as expected, it is left as it is and radiation guns keep the corrosive icon.

Real art replaces the placeholder without any code: put a PNG (8 bits, with or without an alpha channel, not interlaced; any size, a square
one looks best) in the mod's `art` folder, `radiation_icon.png` for the title icon (the card's top and the HUD) and `radiation_icon_row.png`
for the stat rows' small icon (when only the first is there it is used for both). It is drawn to fit the icon's box, as a picture inside
the movie. See ASSETS.md in that folder.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

import unrealsdk
from mods_base import get_pc, hook
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import UObject

from .core import log

CARD_MOVIE = "SharedWillowComponents.SharedWillowComponents"
TITLE_LABELS = ("shock", "fire", "corrosive", "explosive", "amp")  # the title clip: its element frames
ROW_LABELS = ("elementFire", "elementCorrosive", "elementExplosive", "elementShock", "elementAmp")  # the stat rows' clip
TITLE_FRAME, ROW_FRAME = "radiation", "elementRadiation"
# Where the shared movie draws an element's icon (twips, 20 per pixel): the rectangle of the title icon and of the row icon
TITLE_BOX = (-370, 370, -336, 404)
ROW_BOX = (-207, 207, -214, 200)
ICON_RGB = (214, 231, 25)  # the placeholder's colour
ART = Path(__file__).parent / "art"  # where the player's own pictures go
TITLE_PNG, ROW_PNG = "radiation_icon.png", "radiation_icon_row.png"
_RF_ROOTSET = 0x4000

ready = False  # the card movie has both frames and the game will read it with them
_said: set[str] = set()


class _Unknown(ValueError):
    """The movie is not laid out as BL2's (it says how): it is left alone."""


def _once(key: str, msg: str) -> None:
    if key not in _said:
        _said.add(key)
        log(f"element icons: {msg}")


# ---- writing SWF: bit fields, tags, shapes ------------------------------------------------------------------------------------


class _Bits:
    def __init__(self) -> None:
        self.out = bytearray()
        self.acc = 0
        self.count = 0

    def put(self, value: int, width: int) -> _Bits:
        for shift in range(width - 1, -1, -1):
            self.acc = (self.acc << 1) | ((value >> shift) & 1)
            self.count += 1
            if self.count == 8:
                self.out.append(self.acc)
                self.acc = self.count = 0
        return self

    def signed(self, width: int, *values: int) -> _Bits:
        for value in values:
            self.put(value & ((1 << width) - 1), width)
        return self

    def done(self) -> bytes:
        if self.count:
            self.out.append(self.acc << (8 - self.count))
            self.acc = self.count = 0
        return bytes(self.out)


def _sbits(*values: int) -> int:
    """Width of a signed bit field that holds all the values."""
    if not any(values):
        return 1
    return max(v.bit_length() if v >= 0 else (~v).bit_length() for v in values) + 1


def _rect(xmin: int, xmax: int, ymin: int, ymax: int) -> bytes:
    width = _sbits(xmin, xmax, ymin, ymax)
    return _Bits().put(width, 5).signed(width, xmin, xmax, ymin, ymax).done()


def _tag(code: int, data: bytes = b"", long_header: bool = False) -> bytes:
    if len(data) < 0x3F and not long_header:
        return struct.pack("<H", code << 6 | len(data)) + data
    return struct.pack("<HI", code << 6 | 0x3F, len(data)) + data


def _shape(shape_id: int, fill: bytes, polygons: list[list[tuple[int, int]]]) -> bytes:
    """A DefineShape tag: closed polygons (points in twips, drawn clockwise on the screen) with one fill style (its bytes)."""
    xs = [x for poly in polygons for x, _ in poly]
    ys = [y for poly in polygons for _, y in poly]
    edges = _Bits()
    for poly in polygons:
        x0, y0 = poly[0]
        move = _sbits(x0, y0)
        edges.put(0, 1).put(0b00101, 5)  # a style change: a fill style 1 and a move
        edges.put(move, 5).signed(move, x0, y0)
        edges.put(1, 1)  # fill style 1
        here = (x0, y0)
        for x, y in [*poly[1:], poly[0]]:
            dx, dy = x - here[0], y - here[1]
            here = (x, y)
            width = max(_sbits(dx, dy), 2)
            edges.put(0b11, 2).put(width - 2, 4).put(1, 1).signed(width, dx, dy)  # a straight edge, general line
    edges.put(0, 6)  # end of shape
    body = struct.pack("<H", shape_id) + _rect(min(xs), max(xs), min(ys), max(ys))
    body += bytes([1]) + fill + bytes([0])  # one fill style; no line styles
    body += bytes([0x10]) + edges.done()  # one fill bit, no line bits
    return _tag(2, body)


def trefoil(box: tuple[int, int, int, int]) -> list[list[tuple[int, int]]]:
    """The radiation symbol (three blades round a centre disc) as polygons, to fit the box."""
    xmin, xmax, ymin, ymax = box
    cx, cy, radius = (xmin + xmax) / 2, (ymin + ymax) / 2, min(xmax - xmin, ymax - ymin) / 2 * 0.96
    polys = []
    for centre in (90, 210, 330):  # degrees, counter-clockwise from the right, as drawn on paper
        pts = []
        steps = 8
        for i in range(steps + 1):  # the outer edge, left to right (as seen)
            a = math.radians(centre + 30 - 60 * i / steps)
            pts.append((cx + radius * math.cos(a), cy - radius * math.sin(a)))
        for i in range(steps + 1):  # the inner edge back
            a = math.radians(centre - 30 + 60 * i / steps)
            pts.append((cx + radius * 0.34 * math.cos(a), cy - radius * 0.34 * math.sin(a)))
        polys.append([(round(x), round(y)) for x, y in pts])
    polys.append([(round(cx + radius * 0.17 * math.cos(math.radians(a))), round(cy - radius * 0.17 * math.sin(math.radians(a)))) for a in range(0, 360, 30)])
    return polys


def _matrix(scale: float, tx: int, ty: int) -> bytes:
    fixed = round(scale * 0x10000)
    bits, move = _sbits(fixed), _sbits(tx, ty)
    return _Bits().put(1, 1).put(bits, 5).signed(bits, fixed, fixed).put(0, 1).put(move, 5).signed(move, tx, ty).done()


def read_png(path: Path) -> tuple[int, int, bytes]:
    """(width, height, RGBA bytes) of an 8-bit, non-interlaced PNG (gray, gray+alpha, RGB, RGBA or palette)."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise _Unknown(f"{path.name} is not a PNG")
    pos, header, idat, palette, trns = 8, None, b"", b"", b""
    while pos < len(data):
        length, kind = struct.unpack_from(">I4s", data, pos)
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"PLTE":
            palette = chunk
        elif kind == b"tRNS":
            trns = chunk
        elif kind == b"IEND":
            break
    if header is None:
        raise _Unknown(f"{path.name} has no header")
    width, height, depth, ctype, _comp, _filter, interlace = header
    if depth != 8 or interlace != 0 or ctype not in (0, 2, 3, 4, 6):
        raise _Unknown(f"{path.name}: only 8-bit PNGs that are not interlaced are read (save it again as a plain 32-bit PNG)")
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = width * bpp
    raw = zlib.decompress(idat)
    rows, prev = [], bytearray(stride)
    for y in range(height):
        mode = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if mode == 1:
                line[i] = (line[i] + a) & 255
            elif mode == 2:
                line[i] = (line[i] + b) & 255
            elif mode == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif mode == 4:
                p = a + b - c
                pa, pb, pc_ = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc_ else b if pb <= pc_ else c)) & 255
        rows.append(line)
        prev = line
    out = bytearray(width * height * 4)
    for y, line in enumerate(rows):
        for x in range(width):
            o = (y * width + x) * 4
            if ctype == 6:
                out[o:o + 4] = line[x * 4:x * 4 + 4]
            elif ctype == 2:
                out[o:o + 4] = bytes(line[x * 3:x * 3 + 3]) + b"\xff"
            elif ctype == 0:
                out[o:o + 4] = bytes((line[x],) * 3) + b"\xff"
            elif ctype == 4:
                out[o:o + 4] = bytes((line[x * 2],) * 3) + bytes((line[x * 2 + 1],))
            else:
                index = line[x]
                alpha = trns[index] if index < len(trns) else 255
                out[o:o + 4] = palette[index * 3:index * 3 + 3] + bytes((alpha,))
    return width, height, bytes(out)


def _picture_tags(bitmap_id: int, shape_id: int, picture: tuple[int, int, bytes], box: tuple[int, int, int, int]) -> bytes:
    """A lossless bitmap (premultiplied ARGB, as DefineBitsLossless2 keeps it) and a rectangle filled with it that fits the box."""
    width, height, rgba = picture
    raw = bytearray(width * height * 4)
    for i in range(0, len(rgba), 4):
        r, g, b, a = rgba[i:i + 4]
        raw[i:i + 4] = bytes((a, r * a // 255, g * a // 255, b * a // 255))
    xmin, xmax, ymin, ymax = box
    scale = min((xmax - xmin) / width, (ymax - ymin) / height)  # twips per bitmap pixel
    w, h = round(width * scale), round(height * scale)
    left, top = round((xmin + xmax - w) / 2), round((ymin + ymax - h) / 2)
    fill = bytes([0x41]) + struct.pack("<H", bitmap_id) + _matrix(scale, left, top)
    square = [(left, top), (left + w, top), (left + w, top + h), (left, top + h)]
    bitmap = _tag(36, struct.pack("<HBHH", bitmap_id, 5, width, height) + zlib.compress(bytes(raw), 9), long_header=True)
    return bitmap + _shape(shape_id, fill, [square])


def _own_picture(name: str) -> tuple[int, int, bytes] | None:
    """The player's picture `name` from the art folder, or None (no file, or one that cannot be read: the placeholder stays)."""
    path = ART / name
    if not path.is_file():
        return None
    try:
        return read_png(path)
    except Exception as ex:  # noqa: BLE001
        _once("png " + name, f"{name} could not be used ({ex}): the placeholder icon stays")
        return None


# ---- reading SWF ----------------------------------------------------------------------------------------------------------------


def _tags(body: bytes, pos: int, end: int):
    """Each tag from `pos`: its code, where its data starts, the data's length, where the tag starts."""
    while pos < end:
        head, = struct.unpack_from("<H", body, pos)
        code, length, start = head >> 6, head & 0x3F, pos + 2
        if length == 0x3F:
            length, = struct.unpack_from("<I", body, start)
            start += 4
        if start + length > end:
            raise _Unknown(f"a tag at {pos} runs past its container")
        yield code, start, length, pos
        pos = start + length
        if code == 0:
            return
    raise _Unknown("a tag list without its end tag")


def _frames(body: bytes, start: int, length: int) -> list[tuple[str, dict[int, int]]]:
    """A clip's frames: the label of each and what it shows (depth -> character)."""
    shown: dict[int, int] = {}
    frames, label = [], ""
    for code, at, _size, _tag_at in _tags(body, start + 4, start + length):
        if code == 43:
            label = body[at:body.index(b"\0", at)].decode("latin-1")
        elif code in (26, 70):
            flags = body[at]
            pos = at + (2 if code == 70 else 1)
            depth, = struct.unpack_from("<H", body, pos)
            pos += 2
            if code == 70 and body[at + 1] & 0x08:  # a class name comes first
                pos = body.index(b"\0", pos) + 1
            if flags & 0x02:
                shown[depth], = struct.unpack_from("<H", body, pos)
        elif code == 4:
            character, depth = struct.unpack_from("<HH", body, at)
            shown[depth] = character
        elif code == 5:
            shown.pop(struct.unpack_from("<H", body, at + 2)[0], None)
        elif code == 28:
            shown.pop(struct.unpack_from("<H", body, at)[0], None)
        elif code == 1:
            frames.append((label, dict(shown)))
            label = ""
    return frames


_DEFINITIONS = frozenset({2, 22, 32, 83, 46, 84, 39, 37, 11, 33, 10, 48, 75, 91, 7, 34, 6, 20, 21, 35, 36, 90, 14, 60, 87,
                          1001, 1003, 1005, 1006, 1007, 1008})  # tags that define a character and start with its id


def _free_ids(body: bytes, first: int, count: int) -> list[int]:
    """Character ids above every one the movie defines, imports or places."""
    highest = 0
    stack = [(first, len(body))]
    while stack:
        pos, end = stack.pop()
        for code, at, length, _tag_at in _tags(body, pos, end):
            if code == 39:
                stack.append((at + 4, at + length))
            if code in _DEFINITIONS:
                highest = max(highest, struct.unpack_from("<H", body, at)[0])
            elif code in (57, 71):  # ImportAssets, ImportAssets2
                p = body.index(b"\0", at) + 1 + (2 if code == 71 else 0)
                names, = struct.unpack_from("<H", body, p)
                p += 2
                for _ in range(names):
                    highest = max(highest, struct.unpack_from("<H", body, p)[0])
                    p = body.index(b"\0", p + 2) + 1
            elif code in (26, 70):
                p = at + (4 if code == 70 else 3)
                if code == 70 and body[at + 1] & 0x08:
                    p = body.index(b"\0", p) + 1
                if body[at] & 0x02:
                    highest = max(highest, struct.unpack_from("<H", body, p)[0])
    if highest + count >= 0xFFFF:
        raise _Unknown("no character ids are left")
    return [highest + 1 + i for i in range(count)]


def _clip_with_frame(body: bytes, tag_at: int, start: int, length: int, labels: tuple[str, ...], label: str, character: int) -> bytes:
    """An icon clip's tag with one frame more at its end: what the clip shows for an element, with `character` for the element's icon."""
    frames = _frames(body, start, length)
    by_label = {name: shown for name, shown in frames if name}
    refs = [by_label[name] for name in labels]
    depths = {d for ref in refs for d in ref}
    varying = [d for d in depths if len({ref.get(d) for ref in refs}) > 1]
    if len(varying) != 1:
        raise _Unknown(f"the clip with {labels[0]}: its element frames differ in {len(varying)} places, not one")
    icon_depth = varying[0]
    wanted = dict(refs[0])
    wanted[icon_depth] = character
    ended = frames[-1][1]
    clip_id, count = struct.unpack_from("<HH", body, start)
    if count != len(frames) or body[start + length - 2:start + length] != b"\x00\x00":
        raise _Unknown(f"the clip with {labels[0]}: {count} frames named, {len(frames)} found")
    new = b"".join(_tag(28, struct.pack("<H", depth)) for depth in sorted(ended) if depth not in wanted)
    new += _tag(43, label.encode("ascii") + b"\x00")
    for depth, char in sorted(wanted.items()):
        if ended.get(depth) == char:
            continue
        if depth in ended:  # replace what is there
            new += _tag(26, bytes([0x03]) + struct.pack("<HH", depth, char))
        else:
            new += _tag(26, bytes([0x06]) + struct.pack("<HH", depth, char) + b"\x00")
    new += _tag(1)
    inner = body[start + 4:start + length - 2] + new + _tag(0)
    return _tag(39, struct.pack("<HH", clip_id, count + 1) + inner, long_header=True)


def patched(raw: bytes) -> bytes:
    """The card movie's data with the radiation frames (raises _Unknown when the movie is not what this was written for)."""
    kind, version = raw[:3], raw[3]
    if kind not in (b"CFX", b"GFX"):
        raise _Unknown(f"it starts with {kind!r}")
    body = zlib.decompress(raw[8:]) if kind == b"CFX" else raw[8:]
    if struct.unpack_from("<I", raw, 4)[0] != len(body) + 8:
        raise _Unknown("its length field does not match its data")
    first = (5 + 4 * (body[0] >> 3) + 7) // 8 + 4  # after the stage rectangle, the frame rate and count
    found: dict[str, tuple[int, int, int, tuple[str, ...], tuple[int, int, int, int]]] = {}
    for code, at, length, tag_at in _tags(body, first, len(body)):
        if code != 39:
            continue
        names = {name for name, _shown in _frames(body, at, length)}
        for labels, label, box in ((ROW_LABELS, ROW_FRAME, ROW_BOX), (TITLE_LABELS, TITLE_FRAME, TITLE_BOX)):
            if names.issuperset(labels) and "none" in names:
                if label in names:
                    raise _Unknown(f"it has a frame {label!r} already")
                found[label] = (tag_at, at, length, labels, box)
    if set(found) != {ROW_FRAME, TITLE_FRAME}:
        raise _Unknown(f"its icon clips were not found (found: {sorted(found)})")
    title_picture = _own_picture(TITLE_PNG)
    row_picture = _own_picture(ROW_PNG) or title_picture
    ids = _free_ids(body, first, 4)
    shapes, defs = {}, b""
    for label, box, picture, (a, b) in ((ROW_FRAME, ROW_BOX, row_picture, ids[:2]), (TITLE_FRAME, TITLE_BOX, title_picture, ids[2:])):
        if picture is not None:  # the player's own art: a bitmap and a rectangle that shows it
            defs += _picture_tags(a, b, picture, box)
            shapes[label] = b
        else:  # the placeholder: a trefoil drawn with straight edges and one colour
            defs += _shape(a, bytes([0x00]) + bytes(ICON_RGB), trefoil(box))
            shapes[label] = a
    edits = sorted(found.items(), key=lambda item: item[1][0])
    out, pos = bytearray(), 0
    for index, (label, (tag_at, at, length, labels, box)) in enumerate(edits):
        out += body[pos:tag_at]
        if index == 0:  # the new characters, before the first clip that shows one (a character must be defined before it is placed)
            out += defs
        out += _clip_with_frame(body, tag_at, at, length, labels, label, shapes[label])
        pos = at + length
    out += body[pos:]
    new = bytes(out)
    seen, end = set(), first
    for code, at, length, _tag_at in _tags(new, first, len(new)):  # read it back, as the game will
        end = at + length
        if code == 39:
            seen |= {name for name, _shown in _frames(new, at, length)}
    if end != len(new) or not {ROW_FRAME, TITLE_FRAME} <= seen:
        raise _Unknown("the changed movie did not read back")
    packed = zlib.compress(new, 9) if kind == b"CFX" else new
    return kind + bytes([version]) + struct.pack("<I", len(new) + 8) + packed


# ---- in the game ----------------------------------------------------------------------------------------------------------------------


def _in_front_end() -> bool:
    """No map has been entered yet: nothing has made the game read the card movie."""
    pc = get_pc(possibly_loading=True)
    try:
        return pc is not None and pc.WorldInfo is not None and bool(pc.WorldInfo.IsMenuLevel())
    except Exception:  # noqa: BLE001 - no world yet reads as not knowing
        return False


def apply() -> bool:
    """Give the item cards the radiation icon (once; True when they have it)."""
    global ready
    if ready:
        return True
    try:
        movie = unrealsdk.find_object("SwfMovie", CARD_MOVIE)
    except ValueError:
        return False
    data = bytes(movie.RawData)
    if len(data) < 8:  # the game frees a movie's data once it has read it
        _once("read", "the game has read its card movie already: the radiation icon comes with the next start")
        return False
    try:
        new = patched(data)
    except _Unknown as ex:
        if "already" in str(ex):  # patched before (a hot reload): the frames are there
            ready = True
            return True
        _once("unknown", f"the card movie is left as it is ({ex}): radiation keeps the corrosive icon")
        return False
    if not _in_front_end():
        _once("late", "a map is loaded already, so the game has read its card movie: the radiation icon comes with the next start")
        return False
    movie.RawData = list(new)
    movie.ObjectFlags = int(movie.ObjectFlags) | _RF_ROOTSET
    ready = True
    log(f"element icons: the radiation icon is on the item cards ({len(new)} bytes of movie; frames {TITLE_FRAME!r} and {ROW_FRAME!r})")
    return True


@hook("WillowGame.WillowWeapon:SetElementalFrame", Type.POST, hook_identifier="bl3w_radiation_frame")
def on_frame(obj: UObject, *_: object) -> None:
    """A gun's title icon comes from its damage type's element, an enum that has no radiation: name the radiation frame for a radiation gun."""
    if not ready:
        return
    try:
        from . import radiation

        if radiation.is_radiation(obj):
            obj.ElementalFrame = TITLE_FRAME
    except Exception:  # noqa: BLE001
        pass


def _item_frame(obj: UObject, *_: object) -> None:
    """A radiation grenade mod's or shield's title icon: the radiation frame, not its original element's."""
    if not ready:
        return
    try:
        from . import radgear

        if radgear.is_radiation_gear(obj):
            obj.ElementalFrame = TITLE_FRAME
    except Exception:  # noqa: BLE001
        pass


@hook("Engine.WillowInventory:GetElementalFrame", Type.PRE, hook_identifier="bl3w_radiation_gear_frame")
def on_item_frame(obj: UObject, *_: object):
    """The card and the inventory ask an item for its icon frame by this script function (SetElementalFrame is native: not hookable)."""
    if not ready:
        return None
    try:
        from . import gear, radgear

        if gear.item_kind(obj) in ("grenade", "shield") and radgear.is_radiation_gear(obj):
            return Block, TITLE_FRAME
    except Exception:  # noqa: BLE001
        pass
    return None


def mark_item(obj: UObject) -> None:
    """Called when a radiation item is converted or named: the frame is set at once (the card may have read it already)."""
    _item_frame(obj)


HOOKS = [on_frame, on_item_frame]
