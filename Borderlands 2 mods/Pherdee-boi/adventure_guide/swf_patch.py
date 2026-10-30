"""Adventure Guide: builds the patched status menu movie from the game's own copy, in memory.

The status menu (UI_StatusMenu.StatusMenu) is a Scaleform SWF that lives in the player's own game files. This module takes
those bytes (SwfMovie.RawData as the game loaded them), adds a sixth tab button and the item icons the guide's list uses, and
returns the new bytes. Nothing from the game is stored in this mod: the result exists only in memory and the player's own
install is the source, every time. Pure Python (zlib + struct), no external tools.

What is changed (everything else keeps its exact original bytes):
  * header clip (sprite 638): a sixth nav button + tab icon, title and plate shifted one button right, a sixth frame "guide";
    the frame scripts get the matching AS2 edits (bytecode patches, see the AS2 section)
  * root frames 3 and 4: the sixth button's action name; the Mission Log setup shows the "guide" header while _root.guideMode is set
  * the mission list's type icon clip (sprite 37) and the summary box's title icon clip (sprite 522): extra frames that draw
    the item type icons of the reward panel (sprite 592), tinted by rarity
"""
import struct
import zlib

# ---- bit helpers -------------------------------------------------------------------------------------------------------------


class BitReader:
    def __init__(self, data: bytes, pos: int = 0):
        self.d, self.pos, self.bit = data, pos, 0

    def read(self, n: int) -> int:
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.pos] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit, self.pos = 0, self.pos + 1
        return v

    def reads(self, n: int) -> int:
        v = self.read(n)
        return v - (1 << n) if n and v & (1 << (n - 1)) else v

    def align(self) -> int:
        if self.bit:
            self.bit, self.pos = 0, self.pos + 1
        return self.pos


class BitWriter:
    def __init__(self):
        self.bits = []

    def write(self, v: int, n: int) -> None:
        v &= (1 << n) - 1 if n else 0
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)

    def tobytes(self) -> bytes:
        b = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int("".join(map(str, b[i : i + 8])), 2) for i in range(0, len(b), 8))


def sb_bits(*vals: int) -> int:
    """Smallest signed bit width that holds all values."""
    n = 1
    for v in vals:
        n = max(n, (v if v >= 0 else ~v).bit_length() + 1)
    return n


def read_matrix(d: bytes, pos: int):
    r = BitReader(d, pos)
    m = {"scale": None, "rot": None}
    if r.read(1):
        n = r.read(5)
        m["scale"] = (n, r.reads(n), r.reads(n))
    if r.read(1):
        n = r.read(5)
        m["rot"] = (n, r.reads(n), r.reads(n))
    n = r.read(5)
    m["tx"], m["ty"] = r.reads(n), r.reads(n)
    return m, r.align()


def write_matrix(m) -> bytes:
    w = BitWriter()
    if m["scale"]:
        n, sx, sy = m["scale"]
        w.write(1, 1); w.write(n, 5); w.write(sx, n); w.write(sy, n)
    else:
        w.write(0, 1)
    if m["rot"]:
        n, r0, r1 = m["rot"]
        w.write(1, 1); w.write(n, 5); w.write(r0, n); w.write(r1, n)
    else:
        w.write(0, 1)
    n = sb_bits(m["tx"], m["ty"])
    w.write(n, 5); w.write(m["tx"], n); w.write(m["ty"], n)
    return w.tobytes()


def cxform_alpha(r_mult: int, g_mult: int, b_mult: int, a_mult: int) -> bytes:
    """CXFORMWITHALPHA with multiplier terms only (256 = 1.0)."""
    n = sb_bits(r_mult, g_mult, b_mult, a_mult)
    w = BitWriter()
    w.write(0, 1); w.write(1, 1); w.write(n, 4)
    for v in (r_mult, g_mult, b_mult, a_mult):
        w.write(v, n)
    return w.tobytes()


def skip_cxform(d: bytes, pos: int) -> int:
    r = BitReader(d, pos)
    has_add, has_mult, n = r.read(1), r.read(1), r.read(4)
    r.read(n * 4 * has_mult)
    r.read(n * 4 * has_add)
    return r.align()


# ---- tags --------------------------------------------------------------------------------------------------------------------


class Tag:
    def __init__(self, code: int, data: bytes, raw: bytes | None = None):
        self.code, self.data, self.raw = code, data, raw  # raw = original bytes incl. header (kept when untouched)

    def encode(self) -> bytes:
        if self.raw is not None:
            return self.raw
        n = len(self.data)
        if n < 0x3F:
            return struct.pack("<H", (self.code << 6) | n) + self.data
        return struct.pack("<HI", (self.code << 6) | 0x3F, n) + self.data


def parse_tags(body: bytes, pos: int) -> list[Tag]:
    out = []
    while pos < len(body):
        h = struct.unpack_from("<H", body, pos)[0]
        code, ln, hs = h >> 6, h & 0x3F, 2
        if ln == 0x3F:
            ln, hs = struct.unpack_from("<I", body, pos + 2)[0], 6
        out.append(Tag(code, body[pos + hs : pos + hs + ln], body[pos : pos + hs + ln]))
        pos += hs + ln
        if code == 0:
            break
    return out


class Swf:
    def __init__(self, d: bytes):
        self.sig, self.version = d[:3], d[3]
        body = zlib.decompress(d[8:]) if self.sig in (b"CWS", b"CFX") else d[8:]
        nb = body[0] >> 3
        self.header_len = (5 + 4 * nb + 7) // 8 + 4  # RECT + frame rate + frame count
        self.head = body[: self.header_len]
        self.tags = parse_tags(body, self.header_len)

    def to_bytes(self) -> bytes:
        body = self.head + b"".join(t.encode() for t in self.tags)
        comp = zlib.compress(body, 9) if self.sig in (b"CWS", b"CFX") else body
        return self.sig + bytes([self.version]) + struct.pack("<I", 8 + len(body)) + comp

    def sprite(self, sid: int) -> int:
        return next(i for i, t in enumerate(self.tags) if t.code == 39 and struct.unpack_from("<H", t.data, 0)[0] == sid)


def parse_place2(t: Tag):
    d, flags = t.data, t.data[0]
    depth, pos = struct.unpack_from("<H", d, 1)[0], 3
    char = None
    if flags & 0x02:
        char, pos = struct.unpack_from("<H", d, pos)[0], pos + 2
    matrix = None
    if flags & 0x04:
        matrix, pos = read_matrix(d, pos)
    cx = None
    if flags & 0x08:
        end = skip_cxform(d, pos)
        cx, pos = d[pos:end], end
    ratio = None
    if flags & 0x10:
        ratio, pos = d[pos : pos + 2], pos + 2
    name = None
    if flags & 0x20:
        end = d.index(0, pos)
        name, pos = d[pos:end].decode("latin1"), end + 1
    return {"flags": flags, "depth": depth, "char": char, "matrix": matrix, "cx": cx, "ratio": ratio, "name": name, "rest": d[pos:]}


def build_place2(p) -> Tag:
    flags = p["flags"]
    d = bytes([flags]) + struct.pack("<H", p["depth"])
    if flags & 0x02:
        d += struct.pack("<H", p["char"])
    if flags & 0x04:
        d += write_matrix(p["matrix"])
    if flags & 0x08:
        d += p["cx"]
    if flags & 0x10:
        d += p["ratio"]
    if flags & 0x20:
        d += p["name"].encode("latin1") + b"\0"
    return Tag(26, d + p["rest"])


def place3_matrix_span(d: bytes):
    """(start, end, matrix) of the matrix inside a PlaceObject3 tag, or None."""
    f1, f2 = d[0], d[1]
    pos = 4  # flags(2) + depth(2)
    if f2 & 0x08 or ((f2 & 0x10) and (f1 & 0x02)):  # class name
        pos = d.index(0, pos) + 1
    if f1 & 0x02:
        pos += 2
    if not f1 & 0x04:
        return None
    m, end = read_matrix(d, pos)
    return pos, end, m


# ---- AS2 bytecode ------------------------------------------------------------------------------------------------------------
# Only what the six script edits need: parse a DoAction into records, find a statement by its shape, insert / replace records.
# Inserted code pushes strings as literals (Push type 0), so the scripts' constant pools stay as they are.

A_END, A_POP, A_NOT, A_GETVAR, A_SETMEMBER, A_ADD2, A_GETMEMBER, A_CALLMETHOD = 0x00, 0x17, 0x12, 0x1C, 0x4F, 0x47, 0x4E, 0x52
A_POOL, A_PUSH, A_JUMP, A_IF = 0x88, 0x96, 0x99, 0x9D


def parse_actions(data: bytes) -> list[tuple[int, bytes]]:
    out, pos = [], 0
    while pos < len(data):
        code = data[pos]
        if code >= 0x80:
            ln = struct.unpack_from("<H", data, pos + 1)[0]
            out.append((code, data[pos + 3 : pos + 3 + ln]))
            pos += 3 + ln
        else:
            out.append((code, b""))
            pos += 1
    return out


def build_actions(recs: list[tuple[int, bytes]]) -> bytes:
    out = bytearray()
    for code, d in recs:
        out.append(code)
        if code >= 0x80:
            out += struct.pack("<H", len(d)) + d
    return bytes(out)


def rec_size(rec) -> int:
    return 3 + len(rec[1]) if rec[0] >= 0x80 else 1


def p_str(s: str) -> bytes:
    return b"\x00" + s.encode("latin1") + b"\x00"


def p_int(v: int) -> bytes:
    return b"\x07" + struct.pack("<i", v)


def p_const(i: int) -> bytes:
    return bytes([8, i]) if i < 256 else b"\x09" + struct.pack("<H", i)


def push(*items: bytes) -> tuple[int, bytes]:
    return (A_PUSH, b"".join(items))


def pool_strings(recs) -> list[str]:
    for code, d in recs:
        if code == A_POOL:
            n = struct.unpack_from("<H", d, 0)[0]
            return [s.decode("latin1") for s in d[2:].split(b"\0")[:n]]
    raise ValueError("no constant pool")


def find_seq(recs, shape) -> int:
    """Index of the single place where the records match `shape` (a list of (code, data-or-None) pairs; None = any)."""
    hits = [i for i in range(len(recs) - len(shape) + 1)
            if all(recs[i + k][0] == c and (d is None or recs[i + k][1] == d) for k, (c, d) in enumerate(shape))]
    if len(hits) != 1:
        raise ValueError(f"AS2 pattern matched {len(hits)} times (expected 1)")
    return hits[0]


def call_stmt(obj_push: bytes, args: bytes, method_push: bytes) -> list[tuple[int, bytes]]:
    """obj.method(args): Push args..., argc, object; GetVariable; Push method; CallMethod; Pop."""
    return [push(args, obj_push), (A_GETVAR, b""), push(method_push), (A_CALLMETHOD, b""), (A_POP, b"")]


def patch_header_frame(data: bytes, new_tab_frame: bool) -> bytes:
    """Frame script of the header clip. Tabs 1-5: icon7 (the new tab's icon) joins icon2..icon6 in being stopped, and the title
    plate / close button sit 45 px further right. new_tab_frame: the script of tab 5 turned into the new tab's (icon7 plays instead
    of icon5, title text "ADVENTURE GUIDE")."""
    recs = parse_actions(data)
    strs = pool_strings(recs)
    c = {s: i for i, s in enumerate(strs)}
    # ---- the two x offsets behind TabName.textWidth: <const>.<x> = TabName.textWidth + N
    for k in range(len(recs) - 2):
        if recs[k][0] == A_PUSH and recs[k][1][0] == 7 and len(recs[k][1]) == 5 and recs[k + 1][0] == A_ADD2 and recs[k + 2][0] == A_SETMEMBER:
            n = struct.unpack_from("<i", recs[k][1], 1)[0]
            recs[k] = (A_PUSH, p_int(n + 45))
    # the statement for icon6 is gotoAndStop(1) in some frames and gotoAndPlay(2) in others: icon7's stop goes after either
    # (Push value, 1, "icon6"; GetVariable; Push method; CallMethod; Pop)
    hits = []
    for k in range(len(recs) - 4):
        r = recs[k]
        if r[0] == A_PUSH and r[1].endswith(p_const(c["icon6"])) and recs[k + 1][0] == A_GETVAR and recs[k + 3][0] == A_CALLMETHOD and recs[k + 4][0] == A_POP:
            hits.append(k)
    if len(hits) != 1:
        raise ValueError(f"icon6 statement matched {len(hits)} times (expected 1)")
    i6 = hits[0]
    if not new_tab_frame:
        recs[i6 + 5 : i6 + 5] = call_stmt(p_str("icon7"), p_int(1) + p_int(1), p_const(c["gotoAndStop"]))
    else:
        # icon5.gotoAndPlay(2) -> icon5.gotoAndStop(1); icon7.gotoAndPlay(2)
        i5 = find_seq(recs, [(A_PUSH, p_int(2) + p_int(1) + p_const(c["icon5"])), (A_GETVAR, None), (A_PUSH, p_const(c["gotoAndPlay"])), (A_CALLMETHOD, None), (A_POP, None)])
        recs[i5 : i5 + 5] = (call_stmt(p_const(c["icon5"]), p_int(1) + p_int(1), p_const(c["gotoAndStop"]))
                             + call_stmt(p_str("icon7"), p_int(2) + p_int(1), p_const(c["gotoAndPlay"])))
        # the tab's title: the localized "Header_Badass" string of the pool becomes the new tab's name
        title = next(i for i, s in enumerate(strs) if s.startswith("$WillowGame.StatusMenuExGFxMovie.Header_"))
        strs[title] = "ADVENTURE GUIDE"
        for k, (code, d) in enumerate(recs):
            if code == A_POOL:
                recs[k] = (A_POOL, struct.pack("<H", len(strs)) + b"".join(s.encode("latin1") + b"\0" for s in strs))
    return build_actions(recs)


def patch_root_frame3(data: bytes) -> bytes:
    """After header.nav5.ActionName = "challenges": header.nav6.ActionName = "guide"; header.nav6._z = -200."""
    recs = parse_actions(data)
    c = {s: i for i, s in enumerate(pool_strings(recs))}
    at = find_seq(recs, [(A_PUSH, p_const(c["header"])), (A_GETVAR, None), (A_PUSH, p_const(c["nav5"])), (A_GETMEMBER, None),
                         (A_PUSH, p_const(c["ActionName"]) + p_const(c["challenges"])), (A_SETMEMBER, None)])
    add = []
    for prop, value in (("ActionName", p_str("guide")), ("_z", p_int(-200))):
        add += [push(p_str("header")), (A_GETVAR, b""), push(p_str("nav6")), (A_GETMEMBER, b""), push(p_str(prop), value), (A_SETMEMBER, b"")]
    recs[at + 6 : at + 6] = add
    return build_actions(recs)


def patch_root_frame4(data: bytes) -> bytes:
    """The Mission Log setup ends with header.gotoAndStop("logs"); show the "guide" header instead while _root.guideMode is set."""
    recs = parse_actions(data)
    c = {s: i for i, s in enumerate(pool_strings(recs))}
    stmt = [(A_PUSH, p_const(c["logs"]) + p_int(1) + p_const(c["header"])), (A_GETVAR, b""), (A_PUSH, p_const(c["gotoAndStop"])), (A_CALLMETHOD, b""), (A_POP, b"")]
    at = find_seq(recs, stmt)
    assert recs[at + 5][0] == A_END, "header.gotoAndStop(\"logs\") is expected to be the last statement"
    logs = recs[at : at + 5]
    guide = [(A_PUSH, p_str("guide") + p_int(1) + p_const(c["header"])), *recs[at + 1 : at + 5]]
    skip_guide = sum(rec_size(r) for r in guide) + 5  # the Jump record after the "guide" branch (1 + 2 + 2 bytes) is skipped too
    cond = [push(p_str("guideMode")), (A_GETVAR, b""), (A_NOT, b""), (A_IF, struct.pack("<h", skip_guide))]
    jump = [(A_JUMP, struct.pack("<h", sum(rec_size(r) for r in logs)))]
    recs[at : at + 5] = cond + guide + jump + logs
    return build_actions(recs)


# ---- icons -------------------------------------------------------------------------------------------------------------------

SHIFT = 895  # twips between two nav buttons
NEW_ICON_ID = 745
LIST_ICON_SPRITE = 37
ITEM_ICON_SPRITE = 592
ICON_FRAMES = ["pistol", "shotgun", "smg", "ar", "sniper", "rocket", "mod", "comm", "shield", "artifact"]  # 592 frame labels with art
ICON_NAME = {"mod": "grenade"}  # label used by the guide for the sprite's "mod" art (grenade mods)
# rarity class -> RGB, drawn over the white art; BL2's own RarityLevelColors (GD_Globals): 2 uncommon, 3 rare, 4 epic, 5 legendary,
# 6 e-tech, 7 pearlescent, 8 seraph, 9 gemstone, 10 rainbow
RARITY_TINT = {2: (61, 210, 11), 3: (60, 142, 255), 4: (168, 63, 229), 5: (255, 140, 0), 6: (202, 0, 168), 7: (0, 255, 255), 8: (255, 154, 184), 9: (145, 50, 200), 10: (242, 255, 161)}
ICON_SCALE = 0.8
INDENT_TWIPS = 16 * 20  # one level of the list's tree: "i_<icon>" frames sit this far to the right (drop rows under a boss at the left edge)
ITEM_INDENTS = {1: "i_", 3: "i3_"}  # item icon frames by level: drops under a boss (1), drops under a boss under a map (3, the Map sorting)
NATIVE_INDENTED = ((1, "inprogress"), (2, "inprogressMission"))  # the list's own icons moved one and two levels right: "n<level>_<label>" (map and boss rows)
HEADER_ICON_SPRITE = 522  # the summary box's title icon clip
HEADER_ICON_SCALE = 0.95
HEADER_ICON_CENTER = (36.3 * 20, 15.5 * 20)  # twips: where sprite 522 draws its title icon
ICON_CENTER = (220.0 * 20, 105.0 * 20)  # twips: both sprites draw their icons around this point
TAB_ICON_ART = "MissionObject"  # fallback tab icon: the trophy of the reward panel's icon set
TAB_ICON_SCALE = 1.25
TAB_ICON_CENTER = (13.2 * 20, 13.7 * 20)  # twips: centre of the other tabs' icons inside their clip
TAB_ICON_TINT = {"idle": (170, 205, 200), "selected": (255, 215, 40)}


def scaled(p, s, to=ICON_CENTER):
    """A copy of a PlaceObject2 dict scaled by s about ICON_CENTER and moved so that point lands on `to` (adds a matrix if needed)."""
    p = dict(p)
    m = dict(p["matrix"]) if p["matrix"] else {"scale": None, "rot": None, "tx": 0, "ty": 0}
    assert not m["scale"] and not m["rot"], "unexpected scaled/rotated placement"
    f = int(round(s * 65536))
    m["scale"] = (sb_bits(f), f, f)
    m["tx"] = int(round(s * (m["tx"] - ICON_CENTER[0]) + to[0]))
    m["ty"] = int(round(s * (m["ty"] - ICON_CENTER[1]) + to[1]))
    p["matrix"] = m
    p["flags"] |= 0x04
    return p


def item_art(swf: Swf):
    """{592 frame label: [PlaceObject2 dicts]} of the reward panel's item type icons."""
    src, label = {}, None
    for t in parse_tags(swf.tags[swf.sprite(ITEM_ICON_SPRITE)].data, 4):
        if t.code == 43:
            label = t.data[:-1].decode()
        elif t.code == 26 and label:
            src.setdefault(label, []).append(parse_place2(t))
    return src


def add_header_icons(swf: Swf) -> int:
    """Frames "<icon>", "<icon>_2".."<icon>_9" for the summary box's title icon (sprite 522): item type icon + title colour."""
    src = item_art(swf)
    i522 = swf.sprite(HEADER_ICON_SPRITE)
    sid, frames = struct.unpack_from("<HH", swf.tags[i522].data, 0)
    inner = parse_tags(swf.tags[i522].data, 4)
    k = next(i for i, t in enumerate(inner) if t.code == 43 and t.data[:-1] == b"undiscovered")
    do_action, plate = inner[k - 1], inner[k + 1]  # the "undiscovered" frame: title colour script, plate tint
    assert do_action.code == 12 and plate.code == 26
    end_tag = inner.pop()
    assert end_tag.code == 0
    new_frames, first = 0, True
    needle = b"textColor\0"
    for lab in ICON_FRAMES:
        for rarity in (None, *RARITY_TINT):
            name = ICON_NAME.get(lab, lab) + ("" if rarity is None else f"_{rarity}")
            r, g, b = (255, 255, 255) if rarity is None else RARITY_TINT[rarity]
            da = bytearray(do_action.data)
            j = bytes(da).index(needle) + len(needle) + 1  # the int32 follows the push-type byte (7)
            da[j : j + 4] = struct.pack("<I", (r << 16) | (g << 8) | b)  # little endian: 0x00RRGGBB
            if first:
                inner.append(Tag(28, struct.pack("<H", 4)))
                first = False
            else:
                inner += [Tag(28, struct.pack("<H", 4)), Tag(28, struct.pack("<H", 6))]
            inner += [Tag(12, bytes(da)), Tag(43, name.encode() + b"\0"), plate]
            for p in src[lab]:
                q = scaled(p, HEADER_ICON_SCALE, HEADER_ICON_CENTER)
                q["depth"] = 4 if p["depth"] == 1 else 6
                q["name"], q["flags"] = None, q["flags"] & ~0x20
                if rarity is not None and p["depth"] == 2:
                    tr, tg, tb = RARITY_TINT[rarity]
                    q["flags"] |= 0x08
                    q["cx"] = cxform_alpha(tr, tg, tb, 256)
                inner.append(build_place2(q))
            inner.append(Tag(1, b""))
            new_frames += 1
    inner.append(end_tag)
    swf.tags[i522].data = struct.pack("<HH", sid, frames + new_frames) + b"".join(t.encode() for t in inner)
    swf.tags[i522].raw = None
    return new_frames


def native_art(inner: list[Tag]) -> dict[str, list]:
    """{frame label: [PlaceObject2 dicts]} of the list icon sprite's own frames."""
    out, label, places = {}, None, []
    for t in inner:
        if t.code == 43:
            label = t.data[:-1].decode()
        elif t.code == 26:
            places.append(parse_place2(t))
        elif t.code == 1:
            if label:
                out[label] = places
            label, places = None, []
    return out


def moved_right(p, twips: int, depth: int):
    """A copy of a PlaceObject2 dict moved right by `twips` and put at `depth` (a placement without a matrix gets one)."""
    p = dict(p)
    m = dict(p["matrix"]) if p["matrix"] else {"scale": None, "rot": None, "tx": 0, "ty": 0}
    m["tx"] += twips
    p["matrix"] = m
    p["flags"] = (p["flags"] | 0x04) & ~0x20
    p["name"], p["depth"] = None, depth
    return p


TILE_PX = 23.0  # height of a tile icon in the list, pixels (list_icons.bin: build_list_icons.py)
TILE_STATES = (1, *RARITY_TINT)  # a tile per rarity state: 1 plain, 2 uncommon ... 10 rainbow
EXTRA_TILES = {"skull": (0, 1, 2), "logo": (0,), "map_pin": (1,)}  # single tiles: frames "skull", "n1_skull", "n2_skull" (boss rows), "logo" (expansion rows), "n1_map_pin" (map rows)


def tile_places(info: dict, level: int) -> list[dict]:
    """The PlaceObject2 dict (depth 10) of one tile shape, centred like the item icons and moved right by `level` indents."""
    s = TILE_PX / info["h"]
    f = int(round(s * 65536))
    cx, cy = ICON_CENTER[0] + level * INDENT_TWIPS - 2 * 20, ICON_CENTER[1]  # (two pixels left: a square tile is wider than the gun icons it replaces)
    m = {"scale": (sb_bits(f), f, f), "rot": None, "tx": int(round(cx - s * info["w"] * 10)), "ty": int(round(cy - s * info["h"] * 10))}
    return [{"flags": 0x02 | 0x04, "depth": 10, "char": info["shape"], "matrix": m, "cx": None, "ratio": None, "name": None, "rest": b""}]


def add_list_icons(swf: Swf, tiles: dict | None = None) -> int:
    """Append frames "<icon>", "<icon>_2".."<icon>_9" (plain / rarity tinted) to the mission list's type icon sprite, also moved right by one and three
    levels ("i_...", "i3_...": ITEM_INDENTS), and the list's own map and boss icons moved right ("n1_...", "n2_...": NATIVE_INDENTED)."""
    src = item_art(swf)
    i37 = swf.sprite(LIST_ICON_SPRITE)
    sid, frames = struct.unpack_from("<HH", swf.tags[i37].data, 0)
    inner = parse_tags(swf.tags[i37].data, 4)
    end_tag = inner.pop()
    assert end_tag.code == 0
    native = native_art(inner)
    specs: list[tuple[str, list]] = []  # (frame label, PlaceObject2 dicts for depths 10 and 11)
    for level, prefix in ((0, ""), *ITEM_INDENTS.items()):
        for lab in ICON_FRAMES:
            places = src[lab]  # base (depth 1) and the tintable overlay (depth 2) of that icon
            for rarity in (None, *RARITY_TINT):
                frame = []
                tile = tiles and tiles["shapes"].get(ICON_NAME.get(lab, lab), {}).get(str(1 if rarity is None else rarity))
                if tile:  # the user's own tile for this type and rarity replaces the game's pale icon
                    specs.append((prefix + ICON_NAME.get(lab, lab) + ("" if rarity is None else f"_{rarity}"), tile_places(tile, level)))
                    continue
                for p in places:
                    q = scaled(p, ICON_SCALE, (ICON_CENTER[0] + level * INDENT_TWIPS, ICON_CENTER[1]))
                    q["depth"] += 9  # depths 10 and 11
                    q["flags"] &= ~0x20  # no instance names needed
                    q["name"] = None
                    if rarity is not None and p["depth"] == 2:  # the overlay carries the colour
                        r, g, b = RARITY_TINT[rarity]
                        q["flags"] |= 0x08
                        q["cx"] = cxform_alpha(r, g, b, 256)
                    frame.append(q)
                specs.append((prefix + ICON_NAME.get(lab, lab) + ("" if rarity is None else f"_{rarity}"), frame))
    for level, lab in NATIVE_INDENTED:
        specs.append((f"n{level}_{lab}", [moved_right(p, level * INDENT_TWIPS, 10 + k) for k, p in enumerate(native[lab])]))
    for name, levels in EXTRA_TILES.items():
        if tiles and name in tiles["shapes"]:
            for level in levels:
                specs.append((("" if level == 0 else f"n{level}_") + name, tile_places(tiles["shapes"][name]["1"], level)))
    new_frames, first = 0, True
    for name, frame in specs:
        if first:
            inner.append(Tag(28, struct.pack("<H", 2)))  # the last original frame ("playing") leaves depth 2 behind
            first = False
        else:
            inner += [Tag(28, struct.pack("<H", 10)), Tag(28, struct.pack("<H", 11))]
        inner.append(Tag(43, name.encode() + b"\0"))
        inner += [build_place2(q) for q in frame]
        inner.append(Tag(1, b""))
        new_frames += 1
    inner.append(end_tag)
    swf.tags[i37].data = struct.pack("<HH", sid, frames + new_frames) + b"".join(t.encode() for t in inner)
    swf.tags[i37].raw = None
    return new_frames


def relocate_item_art(swf: Swf) -> int:
    """Move the item type icon art (shapes 542.. and sprite 592) in front of the sprites that now place it: the SWF then has
    no forward references (every character is defined before it is used, as the original file does)."""
    first = next(i for i, t in enumerate(swf.tags) if t.code == 2 and struct.unpack_from("<H", t.data, 0)[0] == 542)
    last = swf.sprite(ITEM_ICON_SPRITE)
    block = swf.tags[first : last + 1]
    assert all(t.code in (2, 22, 32, 39, 83) for t in block), "item art block holds more than character definitions"
    del swf.tags[first : last + 1]
    at = swf.sprite(LIST_ICON_SPRITE)
    swf.tags[at:at] = block
    return len(block)


def trophy_tab_icon(swf: Swf, sid: int) -> Tag:
    """Fallback tab icon (no logo file): a sprite with two stopped frames (idle / selected), drawn with the trophy art."""
    art = item_art(swf)[TAB_ICON_ART]

    def layer(depth0, tint):
        out = []
        for p in art:
            q = scaled(p, TAB_ICON_SCALE, TAB_ICON_CENTER)
            q["depth"] = depth0 + (0 if p["depth"] == 1 else 1)
            q["name"], q["flags"] = None, q["flags"] & ~0x20
            q["flags"] |= 0x08
            q["cx"] = cxform_alpha(*tint, 256)
            out.append(build_place2(q))
        return out

    stop = Tag(12, bytes([7, 0]))  # ActionStop
    tags = [stop, *layer(1, TAB_ICON_TINT["idle"]), Tag(1, b""),
            Tag(28, struct.pack("<H", 1)), Tag(28, struct.pack("<H", 2)), *layer(3, TAB_ICON_TINT["selected"]), stop, Tag(1, b""),
            Tag(0, b"")]
    return Tag(39, struct.pack("<HH", sid, 2) + b"".join(t.encode() for t in tags))


# ---- the patch ---------------------------------------------------------------------------------------------------------------


def patch(orig: bytes, logo_tags: bytes | None = None, tile_tags: bytes | None = None, tiles: dict | None = None) -> bytes:
    """The status menu SWF with the sixth tab. logo_tags: pre-encoded tags (bitmaps, shapes and the sprite NEW_ICON_ID) of a
    custom tab icon, or None for the built-in trophy."""
    swf = Swf(orig)
    i_hdr = swf.sprite(638)
    icon_tags = parse_tags(logo_tags, 0) if logo_tags else [trophy_tab_icon(swf, NEW_ICON_ID)]
    swf.tags[i_hdr:i_hdr] = icon_tags
    i_hdr += len(icon_tags)  # the header sprite moved by the inserted tags
    hdr = swf.tags[i_hdr]
    sid, frames = struct.unpack_from("<HH", hdr.data, 0)
    assert sid == 638
    inner = parse_tags(hdr.data, 4)

    placed = {}
    for idx, t in enumerate(inner):
        if t.code == 26:
            p = parse_place2(t)
            placed[p["name"]] = (idx, p)
    nav5_i, nav5 = placed["nav5"]
    icon5_i, icon5 = placed["icon5"]
    nav6 = dict(nav5, depth=33, name="nav6", matrix=dict(nav5["matrix"], tx=nav5["matrix"]["tx"] + SHIFT))
    icon7 = dict(icon5, depth=34, name="icon7", char=NEW_ICON_ID, matrix=dict(icon5["matrix"], tx=icon5["matrix"]["tx"] + SHIFT))
    assert not icon7["flags"] & 0x08, "icon5 unexpectedly has a colour transform"

    # the title plate and the title text move one button to the right
    bk_i, bk = placed["HeaderBkgd"]
    inner[bk_i] = build_place2(dict(bk, matrix=dict(bk["matrix"], tx=bk["matrix"]["tx"] + SHIFT)))
    tab_i = None
    for i, t in enumerate(inner):
        if t.code == 70:  # PlaceObject3: the TabName text (character 636)
            span = place3_matrix_span(t.data)
            if span and struct.unpack_from("<H", t.data, span[0] - 2)[0] == 636:
                start, end, m = span
                inner[i] = Tag(70, t.data[:start] + write_matrix(dict(m, tx=m["tx"] + SHIFT)) + t.data[end:])
                tab_i = i
    assert tab_i is not None, "TabName placement not found"
    inner.insert(icon5_i + 1, build_place2(icon7))
    inner.insert(nav5_i + 1, build_place2(nav6))

    # frame scripts: tabs 1-5 learn about icon7; the sixth frame is tab 5's script turned into the new tab's
    actions = [i for i, t in enumerate(inner) if t.code == 12]
    assert len(actions) == frames == 5, f"header clip: expected 5 frame scripts, found {len(actions)} for {frames} frames"
    script5 = inner[actions[4]].data
    for i in actions:
        inner[i] = Tag(12, patch_header_frame(inner[i].data, False))
    end_tag = inner.pop()
    assert end_tag.code == 0
    inner += [Tag(12, patch_header_frame(script5, True)), Tag(43, b"guide\x00"), Tag(1, b""), end_tag]
    hdr.data = struct.pack("<HH", sid, frames + 1) + b"".join(t.encode() for t in inner)
    hdr.raw = None

    # root frames 3 and 4
    frame = 1
    done = set()
    for t in swf.tags:
        if t.code == 12 and frame in (3, 4) and frame not in done:
            t.data = (patch_root_frame3 if frame == 3 else patch_root_frame4)(t.data)
            t.raw = None
            done.add(frame)
        elif t.code == 1:
            frame += 1
    assert done == {3, 4}, "root frame scripts not found"

    relocate_item_art(swf)
    if tile_tags and tiles:  # the user's icon tiles (bitmaps and the shapes that show them) go in front of the list icon sprite that places them
        swf.tags[swf.sprite(LIST_ICON_SPRITE) : swf.sprite(LIST_ICON_SPRITE)] = parse_tags(tile_tags, 0)
    add_list_icons(swf, tiles if tile_tags else None)
    add_header_icons(swf)
    return swf.to_bytes()


def is_patched(orig: bytes) -> bool:
    """True if these bytes already carry the guide tab (the tab icon sprite exists)."""
    try:
        Swf(orig).sprite(NEW_ICON_ID)
        return True
    except StopIteration:
        return False
