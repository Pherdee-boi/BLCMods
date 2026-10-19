"""TPS's cryo element icon on BL2's item cards (RESEARCH.md §6.33).

Both games draw every item card (the one in the world, the inventory's, a shop's) with one shared movie,
SharedWillowComponents in WillowGame.upk, which the other movies import. An element's icon is a frame of two of its
clips: the title's (labels "shock", "fire", "corrosive", "explosive", "amp", "none") and the stat rows' ("elementFire",
"elementShock"...). TPS's movie has one frame more in each, "ice" and "elementIce": a snowflake, from its sheet of card
art. BL2's has neither, so a cryo gun's card had no icon.

Here, once her build's ice effects are loaded (tools/icefx.py carries the snowflake into them, from the player's TPS):
- a small movie of its own is made beside that texture (GFx finds a movie's pictures by file name next to it, as for
  her skill icons, baroness/skillicons.py), holding the snowflake as two clips, drawn where and as large as TPS's card does;
- BL2's movie gets an import of each clip, and one frame more at the end of each of its two icon clips (at the
  end, so none of BL2's frames moves). This changes the movie's data in memory, before the first map makes the game
  read it (the front end's movies don't use it; once read, the game frees the data); nothing on disk changes;
- cryo guns then name the "ice" frame (the hook here), cryo grenades, shields and relics too (gear.py), and cryo's
  card lines the "elementIce" one (guns.py, gear.py).

Without the snowflake (her files not built yet, or built before 0.3.2), or with a movie this doesn't recognise, nothing
is changed and cryo keeps its empty icon.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterator
from typing import Any

import unrealsdk
from mods_base import get_pc, hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

from ..core import swf
from . import element

CARD_MOVIE = "SharedWillowComponents.SharedWillowComponents"
PACKAGE = "Crocus_IceFX"                  # her build's ice effects (frozenfx.py loads them)
TEXTURE = "Icon_ElementIce"               # tools/icefx.py's ICON, ICON_SIZE and where its blocks put the two icons
TEXTURE_SIZE = (128, 64)
MOVIE = "CardIcons"
# Each icon: where it is in the texture (pixels), the rectangle TPS's card draws it in (twips: xmin, xmax, ymin, ymax)
# and how many twips a texture pixel takes there, read from TPS's shapes 241 and 283 (2026-10-03): the stat rows' is
# the 37x37 picture at 0.56 of its size, the title's at its own.
ROW_ICON = ((3, 3), (-207, 207, -214, 200), 11.2)
TITLE_ICON = ((42, 3), (-370, 370, -336, 404), 20.0)
ROW_CLIP, TITLE_CLIP = "aurelia ice row", "aurelia ice title"       # the two clips' names in her movie
ROW_FRAME, TITLE_FRAME = "elementIce", "ice"                        # TPS's frame labels
# The frames that tell BL2's two icon clips from its others, and which of them the new frame is modelled on
ROW_LABELS = ("elementFire", "elementCorrosive", "elementExplosive", "elementShock", "elementAmp")
TITLE_LABELS = ("shock", "fire", "corrosive", "explosive", "amp")
_RF_ROOTSET = 0x4000

ready = False            # the movie has both frames, and the game reads it with them
_said: set[str] = set()


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] card icons: {msg}")


def _once(key: str, msg: str) -> None:
    if key not in _said:
        _said.add(key)
        _log(msg)


# --- Her movie: the snowflake's two places in the texture, each a rectangle in a clip of its own

def _icon_shape(shape_id: int, bitmap_id: int, icon: tuple[tuple[int, int], tuple[int, int, int, int], float]) -> bytes:
    """A rectangle filled with the texture, moved so that the icon's corner in the texture is the rectangle's."""
    (left, top), (xmin, xmax, ymin, ymax), scale = icon
    tx, ty = round(xmin - left * scale), round(ymin - top * scale)
    fills = (bytes([2])
             + b"\x41" + struct.pack("<H", 0xFFFF) + swf.scale_matrix(20.0, 0, 0)       # as gfxexport writes it
             + b"\x41" + struct.pack("<H", bitmap_id) + swf.scale_matrix(scale, tx, ty))
    edges = swf.Bits().put(2, 4).put(0, 4)                # fill style bits, line style bits
    move = swf.signed_bits(xmin, ymin)
    edges.put(0, 1).put(0b00101, 5).put(move, 5).signed(move, xmin, ymin).put(2, 2)     # move there, fill 2
    for vertical, delta in ((0, xmax - xmin), (1, ymax - ymin), (0, xmin - xmax), (1, ymin - ymax)):
        width = max(swf.signed_bits(delta), 2)
        edges.put(0b11, 2).put(width - 2, 4).put(0, 1).put(vertical, 1).signed(width, delta)
    edges.put(0, 6)                                    # end of shape
    return struct.pack("<H", shape_id) + swf.rect(xmin, xmax, ymin, ymax) + fills + b"\x00" + edges.done()


def _place(depth: int, character: int, replace: bool = False) -> bytes:
    """PlaceObject2: the character on a depth, where it is drawn (no matrix values), in place of what is there."""
    return swf.tag(26, bytes([0x07 if replace else 0x06]) + struct.pack("<HH", depth, character) + b"\x00")


def _clip(clip_id: int, shape_id: int, name: str) -> bytes:
    inner = _place(1, shape_id) + swf.tag(1) + swf.tag(0)
    return (swf.tag(39, struct.pack("<HH", clip_id, 1) + inner)
            + swf.tag(56, struct.pack("<HH", 1, clip_id) + name.encode("ascii") + b"\x00"))


def icon_movie() -> bytes:
    """An uncompressed GFx movie with the two icon clips, its one picture the texture beside it."""
    width, height = TEXTURE_SIZE
    tags = b"".join((
        swf.tag(1000, struct.pack("<HIH", 0x036E, 0, 13) + swf.pstr("") + swf.pstr(MOVIE)),          # exporter info
        swf.tag(69, struct.pack("<I", 0)),                                                     # file attributes
        swf.tag(9, b"\xff\xff\xff"),                                                           # background
        swf.tag(1009, struct.pack("<IHHH", 1, 13, width, height) + swf.pstr("") + swf.pstr(f"{TEXTURE}.tga") + b"\x00"),
        swf.tag(2, _icon_shape(2, 1, ROW_ICON)),
        _clip(3, 2, ROW_CLIP),
        swf.tag(2, _icon_shape(4, 1, TITLE_ICON)),
        _clip(5, 4, TITLE_CLIP),
        swf.tag(1),                                                                            # show frame
        swf.tag(0),
    ))
    body = swf.rect(0, 11000, 0, 8000) + struct.pack("<HH", 24 << 8, 1) + tags
    return b"GFX" + bytes([9]) + struct.pack("<I", 8 + len(body)) + body


# --- BL2's movie: an import of the two clips, and a frame for each at the end of its two icon clips

class _Unknown(ValueError):
    """The movie isn't laid out as BL2's (it says how); it is left as it is."""


def _tags(body: bytes, pos: int, end: int) -> Iterator[tuple[int, int, int, int]]:
    """Each tag from `pos`: its code, where its data starts, the data's length, where the tag starts."""
    while pos < end:
        head = struct.unpack_from("<H", body, pos)[0]
        code, length, start = head >> 6, head & 0x3F, pos + 2
        if length == 0x3F:
            length = struct.unpack_from("<I", body, start)[0]
            start += 4
        if start + length > end:
            raise _Unknown(f"a tag at {pos} runs past its container")
        yield code, start, length, pos
        pos = start + length
        if code == 0:
            return
    raise _Unknown("a tag list without its end tag")


def _text(body: bytes, pos: int) -> str:
    return body[pos:body.index(b"\0", pos)].decode("latin-1")


def _frames(body: bytes, start: int, length: int) -> list[tuple[str, dict[int, int]]]:
    """A clip's frames: each one's label and what it shows (depth -> character)."""
    shown: dict[int, int] = {}
    frames, label = [], ""
    for code, at, _size, _tag_at in _tags(body, start + 4, start + length):
        if code == 43:
            label = _text(body, at)
        elif code in (26, 70):                             # PlaceObject2, PlaceObject3
            flags = body[at]
            pos = at + (2 if code == 70 else 1)
            depth = struct.unpack_from("<H", body, pos)[0]
            pos += 2
            if code == 70 and body[at + 1] & 0x08:         # a class name comes first
                pos = body.index(b"\0", pos) + 1
            if flags & 0x02:
                shown[depth] = struct.unpack_from("<H", body, pos)[0]
        elif code == 4:                                    # PlaceObject
            character, depth = struct.unpack_from("<HH", body, at)
            shown[depth] = character
        elif code == 5:                                    # RemoveObject
            shown.pop(struct.unpack_from("<H", body, at + 2)[0], None)
        elif code == 28:                                   # RemoveObject2
            shown.pop(struct.unpack_from("<H", body, at)[0], None)
        elif code == 1:
            frames.append((label, dict(shown)))
            label = ""
    return frames


def _with_frame(body: bytes, tag_at: int, start: int, length: int, labels: tuple[str, ...], label: str,
                character: int) -> bytes:
    """An icon clip's tag with one frame more at its end: what the element frames show, with `character` for the
    element's icon."""
    frames = _frames(body, start, length)
    by_label = {name: shown for name, shown in frames if name}
    first, last = by_label[labels[0]], by_label[labels[-1]]
    icon_depths = [depth for depth in last if first.get(depth) != last[depth]]
    if len(icon_depths) != 1 or set(first) != set(last):
        raise _Unknown(f"the clip with {labels[0]}: its element frames differ in more than the icon")
    depth = icon_depths[0]
    end = frames[-1][1]
    for other, what in last.items():
        if other != depth and end.get(other) != what:
            raise _Unknown(f"the clip with {labels[0]}: its last frame has something else behind the icon")
    new = b"".join(swf.tag(28, struct.pack("<H", gone)) for gone in sorted(end) if gone not in last)
    new += swf.tag(43, label.encode("ascii") + b"\x00") + _place(depth, character, replace=depth in end) + swf.tag(1)
    clip_id, count = struct.unpack_from("<HH", body, start)
    if count != len(frames) or body[start + length - 2:start + length] != b"\x00\x00":
        raise _Unknown(f"the clip with {labels[0]}: {count} frames named, {len(frames)} found")
    inner = body[start + 4:start + length - 2] + new + swf.tag(0)
    data = struct.pack("<HH", clip_id, count + 1) + inner
    return struct.pack("<HI", 39 << 6 | 0x3F, len(data)) + data     # a long header, as the clip had


# Tags that define a character and start with its id: shapes, morphs, clips, texts, fonts, buttons, pictures, sounds,
# video, and gfxexport's own (a picture, a piece of a sheet, a gradient, a font, a sound)
_DEFINITIONS = frozenset({2, 22, 32, 83, 46, 84, 39, 37, 11, 33, 10, 48, 75, 91, 7, 34, 6, 20, 21, 35, 36, 90, 14, 60, 87,
                          1001, 1003, 1005, 1006, 1007, 1008})


def _free_ids(body: bytes, first: int, count: int) -> list[int]:
    """Character ids the movie doesn't use: above every id it defines, imports or places, and (in case a tag this
    doesn't know defines one) not the number any tag starts with."""
    highest, starts = 0, set()
    stack = [(first, len(body))]
    while stack:
        pos, end = stack.pop()
        for code, at, length, _tag_at in _tags(body, pos, end):
            if length >= 2:
                starts.add(struct.unpack_from("<H", body, at)[0])
            if code == 39:
                stack.append((at + 4, at + length))
            if code in _DEFINITIONS:
                highest = max(highest, struct.unpack_from("<H", body, at)[0])
            elif code in (57, 71):                                 # ImportAssets, ImportAssets2
                p = body.index(b"\0", at) + 1 + (2 if code == 71 else 0)
                names = struct.unpack_from("<H", body, p)[0]
                p += 2
                for _ in range(names):
                    highest = max(highest, struct.unpack_from("<H", body, p)[0])
                    p = body.index(b"\0", p + 2) + 1
            elif code in (26, 70):                                 # PlaceObject2, PlaceObject3
                p = at + (4 if code == 70 else 3)
                if code == 70 and body[at + 1] & 0x08:
                    p = body.index(b"\0", p) + 1
                if body[at] & 0x02:
                    highest = max(highest, struct.unpack_from("<H", body, p)[0])
            elif code == 1009:                                     # a picture: 32 bits; a sheet's is 0x9xxxx
                value = struct.unpack_from("<I", body, at)[0]
                highest = max(highest, value if value < 0x10000 else 0)
    free = [n for n in range(highest + 1, 0xFFFF) if n not in starts][:count]
    if len(free) < count:
        raise _Unknown("no character ids are left")
    return free


def _icon_clips(body: bytes, first: int) -> dict[str, tuple[int, int, int]]:
    """BL2's two icon clips, by their new frame's label: where the tag starts, where its data starts, its length."""
    found: dict[str, tuple[int, int, int]] = {}
    for code, at, length, tag_at in _tags(body, first, len(body)):
        if code != 39:
            continue
        names = {name for name, _shown in _frames(body, at, length)}
        for labels, label in ((ROW_LABELS, ROW_FRAME), (TITLE_LABELS, TITLE_FRAME)):
            if names.issuperset(labels) and "none" in names:
                if label in names:
                    raise _Unknown(f"it has a frame {label!r} already")
                if label in found:
                    raise _Unknown(f"two clips have the frames {labels}")
                found[label] = (tag_at, at, length)
    if set(found) != {ROW_FRAME, TITLE_FRAME}:
        raise _Unknown(f"its icon clips weren't found (found: {sorted(found)})")
    return found


def patched(raw: bytes, movie_url: str) -> bytes:
    """BL2's card movie with the cryo frames: `raw` is its data (compressed or not), `movie_url` her movie's address
    from it. Raises _Unknown when the movie isn't what this was written for."""
    kind, version = raw[:3], raw[3]
    if kind not in (b"CFX", b"GFX"):
        raise _Unknown(f"it starts with {kind!r}")
    body = zlib.decompress(raw[8:]) if kind == b"CFX" else raw[8:]
    if struct.unpack_from("<I", raw, 4)[0] != len(body) + 8:
        raise _Unknown("its length field doesn't match its data")
    first = (5 + 4 * (body[0] >> 3) + 7) // 8 + 4          # after the stage rectangle, the frame rate and count
    clips = _icon_clips(body, first)
    row_id, title_id = _free_ids(body, first, 2)
    # One import per clip, as BL2's own movies have them: of an import naming two, the game's GFx only bound the
    # first (the second one's frame showed nothing, bl2-test 2026-10-03).
    loads = b""
    for character, name in ((row_id, ROW_CLIP), (title_id, TITLE_CLIP)):
        load = (movie_url.encode("ascii") + b"\x00\x01\x00" + struct.pack("<HH", 1, character)
                + name.encode("ascii") + b"\x00")
        loads += struct.pack("<HI", 71 << 6 | 0x3F, len(load)) + load      # a long header, as theirs have
    edits = [(clips[ROW_FRAME], ROW_LABELS, ROW_FRAME, row_id), (clips[TITLE_FRAME], TITLE_LABELS, TITLE_FRAME, title_id)]
    edits.sort(key=lambda edit: edit[0][0])
    out, pos = bytearray(), 0
    for index, ((tag_at, at, length), labels, label, character) in enumerate(edits):
        out += body[pos:tag_at]
        if index == 0:                                     # the imports, before the first clip that uses one
            out += loads
        out += _with_frame(body, tag_at, at, length, labels, label, character)
        pos = at + length
    out += body[pos:]
    new = bytes(out)
    labels_now, end = set(), first
    for code, at, length, _tag_at in _tags(new, first, len(new)):       # read it back, as the game will
        end = at + length
        if code == 39:
            labels_now |= {name for name, _shown in _frames(new, at, length)}
    if end != len(new) or not {ROW_FRAME, TITLE_FRAME} <= labels_now:
        raise _Unknown("the changed movie didn't read back")
    packed = zlib.compress(new, 9) if kind == b"CFX" else new
    return kind + bytes([version]) + struct.pack("<I", len(new) + 8) + packed


# --- Runtime

def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _root(obj: UObject) -> UObject:
    obj.ObjectFlags = int(obj.ObjectFlags) | _RF_ROOTSET
    return obj


def _in_front_end() -> bool:
    """No map has been entered: nothing has made the game read the card movie yet."""
    pc = get_pc(possibly_loading=True)
    try:
        return pc is not None and pc.WorldInfo is not None and bool(pc.WorldInfo.IsMenuLevel())
    except Exception:  # noqa: BLE001 - no world yet reads as not knowing
        return False


def apply() -> bool:
    """Give BL2's cards the cryo icon, once her ice effects are loaded. Idempotent; True when cards have it."""
    global ready  # noqa: PLW0603
    if ready:
        return True
    card = _find("SwfMovie", CARD_MOVIE)
    texture = _find("Texture2D", f"{PACKAGE}.{TEXTURE}")
    package = _find("Package", PACKAGE)
    if card is None or texture is None or package is None:
        if card is not None:
            _once("no texture", "her files have no cryo icon (a rebuild adds it): cryo cards keep an empty icon")
        return False
    if not _in_front_end():
        _once("late", "a map is loaded already, so the game has read its card movie: the cryo icon comes with the next start")
        return False
    movie = _find("SwfMovie", f"{PACKAGE}.{MOVIE}")
    if movie is None:
        movie = unrealsdk.construct_object("SwfMovie", package, MOVIE)
        movie.RawData = list(icon_movie())
        movie.References = [texture]
    _root(movie)
    _root(texture)
    data = bytes(card.RawData)
    if len(data) < 8:
        # The game frees a movie's data once it has read it. On a start that first builds her files the setup runs
        # late, after the title screen has read this one (the user's log, 2026-10-04: an IndexError here).
        _once("read", "the game has read its card movie already: the cryo icon comes with the next start")
        return False
    try:
        new = patched(data, f"../{PACKAGE}/{MOVIE}.swf")
    except _Unknown as ex:
        _once("unknown", f"BL2's card movie is left as it is ({ex}): cryo cards keep an empty icon")
        return False
    card.RawData = list(new)
    card.References = [*card.References, movie, texture]
    ready = True
    _log(f"the cryo icon is on BL2's cards ({len(new)} bytes of movie; frames {TITLE_FRAME!r} and {ROW_FRAME!r})")
    return True


def row_frame() -> str:
    """The stat rows' frame for cryo lines: TPS's, or none while BL2's movie hasn't got it."""
    return ROW_FRAME if ready else ""


def _is_cryo(damage_type: UObject | None) -> bool:
    if damage_type is None:
        return False
    path = damage_type._path_name()
    return path in (element.IMPACT, element.IMPACT_NO_FREEZE, element.STATUS_DAMAGE)


@hook("WillowGame.WillowWeapon:SetElementalFrame", Type.POST)
def _weapon_frame(obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    """A gun's title icon comes from its damage type's element, an enum BL2 has no cryo value in."""
    if not ready:
        return
    found = obj.StaticGetWeaponDamageType(obj.DefinitionData)      # the damage type, then the data handed back
    if _is_cryo(found[0] if isinstance(found, tuple) else found):
        obj.ElementalFrame = TITLE_FRAME


HOOKS = (_weapon_frame,)
