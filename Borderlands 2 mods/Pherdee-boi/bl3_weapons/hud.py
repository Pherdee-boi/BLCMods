"""Small HUD drawing kit on the UE3 Canvas (WillowGameViewportClient.PostRender), in BL2's HUD style.

Canvas calls (Engine.upk): SetPos, SetDrawColor, DrawRect(w, h, Tex), DrawBox(w, h), DrawText(Text, CR,
XScale, YScale), TextSize(Text, out XL, out YL); the font is the game's own UI_Fonts.Font_Hud_Medium.
Everything scales with the screen height (designed at 1080p).
"""

import unrealsdk
from unrealsdk.unreal import UObject, WeakPointer

from .core import log

FONT_PATH = "UI_Fonts.Font_Hud_Medium"
_font: WeakPointer | None = None
_font_failed = False
_text_failed = False


def scale(canvas: UObject) -> float:
    return canvas.SizeY / 1080.0


def font() -> UObject | None:
    global _font, _font_failed
    if _font is not None and _font() is None:
        _font = None
    if _font is None and not _font_failed:
        for cls in ("Font", "MultiFont"):
            try:
                _font = WeakPointer(unrealsdk.find_object(cls, FONT_PATH))
                break
            except ValueError:
                continue
        if _font is None:
            _font_failed = True
            log(f"HUD font {FONT_PATH} not found, using the default canvas font")
    return _font() if _font is not None else None


def rect(canvas: UObject, x: float, y: float, w: float, h: float, rgba: tuple[int, int, int, int]) -> None:
    if w <= 0 or h <= 0:
        return
    canvas.SetPos(x, y, 0)
    canvas.SetDrawColor(*rgba)
    canvas.DrawRect(w, h, canvas.DefaultTexture)


def box(canvas: UObject, x: float, y: float, w: float, h: float, rgba: tuple[int, int, int, int]) -> None:
    canvas.SetPos(x, y, 0)
    canvas.SetDrawColor(*rgba)
    canvas.DrawBox(w, h)


def text_size(canvas: UObject, s: str, size: float) -> tuple[float, float]:
    f = font()
    if f is not None:
        canvas.Font = f
    ret = canvas.TextSize(s, 0.0, 0.0)
    xl, yl = (ret[1], ret[2]) if isinstance(ret, tuple) else (0.0, 0.0)
    return xl * size, yl * size


def text(canvas: UObject, s: str, x: float, y: float, size: float, rgba: tuple[int, int, int, int], align: str = "left") -> None:
    """Draw text with a 1px dark shadow. align: left | right | center (x is that edge/centre)."""
    global _text_failed
    if _text_failed:
        return
    try:
        f = font()
        if f is not None:
            canvas.Font = f
        if align != "left":
            w, _h = text_size(canvas, s, size)
            x -= w if align == "right" else w / 2
        off = max(1.0, size)
        canvas.SetPos(x + off, y + off, 0)
        canvas.SetDrawColor(0, 0, 0, int(rgba[3] * 0.8))
        canvas.DrawText(s, False, size, size)
        canvas.SetPos(x, y, 0)
        canvas.SetDrawColor(*rgba)
        canvas.DrawText(s, False, size, size)
    except Exception as e:  # noqa: BLE001 - bars keep working without labels
        _text_failed = True
        log(f"HUD text disabled: {e!r}")


def lerp_color(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, ...]:
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def with_alpha(rgba: tuple[int, int, int, int], alpha: float) -> tuple[int, int, int, int]:
    return rgba[0], rgba[1], rgba[2], int(rgba[3] * max(0.0, min(1.0, alpha)))


def slanted(canvas: UObject, x: float, y: float, w: float, h: float, rgba: tuple[int, int, int, int], slant: float = 0.45, bands: int = 4) -> None:
    """A parallelogram leaning right (top edge shifted right), drawn as stepped bands (BL2's bar look)."""
    band = h / bands
    for i in range(bands):
        shift = (bands - 1 - i) * band * slant
        rect(canvas, x + shift, y + i * band, w, band + 0.5, rgba)


def segmented_bar(
    canvas: UObject,
    x: float,
    y: float,
    w: float,
    h: float,
    frac: float,
    color_at,
    segments: int = 20,
    gap: float = 2.0,
    empty: tuple[int, int, int, int] = (200, 219, 252, 30),
    backing: tuple[int, int, int, int] | None = (6, 8, 14, 165),
    slant: float = 0.45,
) -> None:
    """BL2-style slanted segmented bar. color_at(i, segments) -> rgba for a filled segment."""
    sw = h * slant
    if backing is not None:
        pad = h * 0.28
        slanted(canvas, x - pad - sw, y - pad, w + 2 * pad, h + 2 * pad, backing, slant)
    seg_w = (w - gap * (segments - 1)) / segments
    filled = max(0.0, min(1.0, frac)) * segments
    for i in range(segments):
        sx = x - sw + i * (seg_w + gap)
        part = max(0.0, min(1.0, filled - i))
        if part <= 0:
            slanted(canvas, sx, y, seg_w, h, empty, slant)
        else:
            slanted(canvas, sx, y, seg_w * part, h, color_at(i, segments), slant)


# --------------------------------------------------------------------------- copies of BL2's ammo counter

AS_TYPE = unrealsdk.find_enum("ASType")


def as_args(*values: object) -> list:
    """Python values -> GFxUI ASValue structs for GFxObject.Invoke."""
    out = []
    for v in values:
        if isinstance(v, str):
            out.append(unrealsdk.make_struct("ASValue", Type=AS_TYPE.AS_String, S=v))
        else:
            out.append(unrealsdk.make_struct("ASValue", Type=AS_TYPE.AS_Number, N=float(v)))
    return out


class CounterCopy:
    """A duplicate of BL2's ammo counter (_root.p1.bullets) in the HUD movie, showing our own text in
    the HUD's real font. The game never updates the copy (see bandit.py for why that matters).
    y_offset moves it relative to the real counter, in HUD-stage units."""

    ORIGINAL = "_root.p1.bullets"

    def __init__(self, name: str) -> None:
        self.name = name
        self.path = "_root.p1." + name
        self.number = self.path + ".bullet_c"
        self.last_html = ""
        self.logged = False

    def ensure(self, movie, y_offset: float | None) -> bool:
        if movie.GetVariableObject(self.path) is None:
            original = movie.GetVariableObject(self.ORIGINAL)
            p1 = movie.GetVariableObject("_root.p1")
            if original is None or p1 is None:
                return False
            original.Invoke("duplicateMovieClip", as_args(self.name, p1.GetNextHighestDepth()))
            if movie.GetVariableObject(self.path) is None:
                if not self.logged:
                    self.logged = True
                    log(f"HUD: duplicating the ammo counter as {self.name} failed")
                return False
            movie.SetVariableBool(self.path + ".bullet_icon._visible", False)
            movie.SetVariableBool(self.path + ".flash._visible", False)
            self.last_html = ""
            self._frame = None
            self.fresh = True  # caller positions a new copy
        else:
            self.fresh = False
        if y_offset is not None:
            movie.SetVariableNumber(self.path + "._y", movie.GetVariableNumber(self.ORIGINAL + "._y") + y_offset)
        return True


    def set_alpha(self, movie, alpha: float) -> None:
        movie.SetVariableNumber(self.path + "._alpha", max(0.0, min(100.0, alpha * 100)))


    def layout(self, movie) -> tuple[tuple[float, float, float, float], tuple[float, float]] | None:
        """((x, y, w, h) of the number box, (stage w, stage h)) in HUD-stage units."""
        g = movie.GetVariableNumber
        x = g("_root.p1._x") + g(self.path + "._x") + g(self.number + "._x")
        y = g("_root.p1._y") + g(self.path + "._y") + g(self.number + "._y")
        w, h = g(self.number + "._width"), g(self.number + "._height")
        sw, sh = g("Stage.width"), g("Stage.height")
        if w <= 0 or h <= 0 or sw <= 0 or sh <= 0:
            return None
        return (x, y, w, h), (sw, sh)

    def remove(self, movie) -> None:
        copy = movie.GetVariableObject(self.path) if movie is not None else None
        if copy is not None:
            copy.RemoveMovieClip()
        self.last_html = ""


def symmetric_bar(
    canvas: UObject,
    cx: float,
    top: float,
    w: float,
    h: float,
    frac: float,
    color: tuple[int, int, int, int],
    segments: int = 20,
    gap: float = 2.0,
    empty: tuple[int, int, int, int] = (200, 219, 252, 30),
    backing: tuple[int, int, int, int] | None = (6, 8, 14, 165),
) -> None:
    """Straight segmented bar centred on cx that empties evenly from both ends toward the middle."""
    x = cx - w / 2
    if backing is not None:
        pad = h * 0.3
        rect(canvas, x - pad, top - pad, w + 2 * pad, h + 2 * pad, backing)
    seg_w = (w - gap * (segments - 1)) / segments
    half = max(0.0, min(1.0, frac)) * w / 2  # filled half-width from the centre
    for i in range(segments):
        sx = x + i * (seg_w + gap)
        hit_l, hit_r = max(sx, cx - half), min(sx + seg_w, cx + half)
        rect(canvas, sx, top, seg_w, h, empty)
        if hit_r > hit_l:
            rect(canvas, hit_l, top, hit_r - hit_l, h, color)


class OutlinedLabel:
    """BL2-style HUD text (white, black outline, $WillowCompact) in the HUD movie, like the mode indicator:
    an empty clip under `parent` holding 8 black offset copies and the main field. Re-created after map
    loads; positioned in HUD-stage units (1280x720 stage)."""

    OUTLINE = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
    FONT = "$WillowCompact"

    def __init__(self, name: str, width: float = 200.0, height: float = 26.0, parent: str = "_root") -> None:
        self.name, self.parent, self.w, self.h = name, parent, width, height
        self.box = f"{parent}.{name}"
        self._html = ""
        self._plain_font = False

    def ensure(self, movie) -> bool:
        if movie.GetVariableObject(self.box + ".main") is not None:
            return True
        parent = movie.GetVariableObject(self.parent)
        if parent is None:
            return False
        if movie.GetVariableObject(self.box) is None:
            parent.CreateEmptyMovieClip(self.name, int(parent.GetNextHighestDepth()))
        box = movie.GetVariableObject(self.box)
        if box is None:
            return False
        for i, (dx, dy) in enumerate(self.OUTLINE):
            box.Invoke("createTextField", as_args(f"o{i}", i + 1, dx, dy, self.w, self.h))
        box.Invoke("createTextField", as_args("main", len(self.OUTLINE) + 1, 0, 0, self.w, self.h))
        if movie.GetVariableObject(self.box + ".main") is None:
            return False
        for path in self._fields():
            for prop, value in (("html", True), ("embedFonts", not self._plain_font), ("selectable", False), ("multiline", False), ("wordWrap", False)):
                movie.SetVariableBool(f"{path}.{prop}", value)
        self._html = ""
        return True

    def _fields(self) -> list[str]:
        return [self.box + ".main"] + [f"{self.box}.o{i}" for i in range(len(self.OUTLINE))]

    def place_centered(self, movie, x: float, y: float) -> None:
        movie.SetVariableNumber(self.box + "._x", x - self.w / 2)
        movie.SetVariableNumber(self.box + "._y", y)

    def set_alpha(self, movie, alpha: float) -> None:
        movie.SetVariableNumber(self.box + "._alpha", max(0.0, min(1.0, alpha)) * 100)

    def set_text(self, movie, text: str, size: int = 14, color: str = "#FFFFFF", align: str = "center") -> None:
        html = f'<p align="{align}"><font face="{self.FONT}" size="{size}" color="{color}">{text}</font></p>'
        if html == self._html:
            return
        self._html = html
        movie.SetVariableString(self.box + ".main.htmlText", html)
        outline = html.replace(f'color="{color}"', 'color="#000000"')
        for i in range(len(self.OUTLINE)):
            movie.SetVariableString(f"{self.box}.o{i}.htmlText", outline)
        if not self._plain_font and movie.GetVariableNumber(self.box + ".main.textWidth") <= 0:
            self._plain_font = True  # embedded font didn't render in this field
            for path in self._fields():
                movie.SetVariableBool(path + ".embedFonts", False)
            self._html = ""
            log(f"{self.name}: {self.FONT} didn't render, using a plain font")
