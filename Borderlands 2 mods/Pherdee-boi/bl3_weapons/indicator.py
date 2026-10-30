"""Weapon-mode indicator: a small label just under BL2's ammo counter showing the held gun's BL3 mode.

  Maliwan  INCENDIARY / shock     current element bright in its colour, the alternate dimmed
  Vladof   GRENADE LAUNCHER READY | ACTIVE | 12s
  Torgue   STICKY | IMPACT        (uniques with their own shot: nothing)
  Dahl     FULL-AUTO | 3-ROUND BURST | SEMI-AUTO
  Tediore  THROW: TURRET

The label lives in BL2's HUD movie itself: an empty clip (_root.p1.bl3_modebox) holding text fields made
with AS2 createTextField (via GFxObject.Invoke) in the HUD's own font ($WillowCompact, imported from
gfxfontlib.swf): eight black copies offset by one unit around a white one = BL2's white-with-black-outline
HUD text. Placed right under the ammo number (_root.p1.bullets.bullet_c), right-aligned with it. Quiet while such a gun is
held; it pops for a moment when the mode key is used. Mode changes (Maliwan element, Torgue sticky/impact, Vladof
underbarrel) also play a quick flick of the first-person gun (Pawn.Arms.SkeletalMesh RotOrigin/Origin) and flash the label in the new colour.
(v0.17.0 drew it on the Canvas in Font_Hud_Medium; the user wanted the HUD's real font.)
"""

import math

from mods_base import DropdownOption, get_pc, hook
from unrealsdk.unreal import UObject

from . import dahl, hud, maliwan, tediore, torgue, vladof
from .core import held_frame, log, active_maker_of as maker_of, now as game_now, state

mode_option = DropdownOption(
    "Mode Indicator",
    "Always",
    ["Always", "Only On Change", "Off"],
    description="Show the held gun's BL3 mode under the ammo counter.",
)

BOX_NAME = "bl3_modebox"
BOX = "_root.p1." + BOX_NAME
MAIN = BOX + ".main"
OUTLINE_OFFSETS = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
FONT = "$WillowCompact"
FIELD_W, FIELD_H = 320.0, 26.0  # HUD-stage units
FONT_SIZE, POP_SIZE = 14, 16
HUD_TEXT, DIM = "#FFFFFF", "#9AA2B4"  # BL2 HUD text is white with a black outline
READY, ACTIVE = "#78E678", "#FF9628"
ELEMENT_COLORS = {"Fire": "#FF7828", "Shock": "#5AAAFF", "Corrosive": "#82DC3C", "Slag": "#BE6EE6", "Cryo": "#4FE3FF", "Radiation": "#D6E719"}
QUIET, POP_TIME, SETTLE_TIME = 0.6, 1.4, 0.5
CHANGE_SHOW = 2.5
CHECK_EVERY = 0.5

_changed_at = -1e9
_checked_at = -1e9
_field_ok = False
_font_checked = False
_last_html = ""
_last_alpha = None  # the alpha last written to the HUD box (None = write it again)
_flick_at = -1e9
_flick_color = ""
_arms_rest = None  # (mesh address, (pitch, yaw, roll), (x, y, z)) while a flick plays


def bump() -> None:
    """Call when the mode changes: the indicator pops."""
    global _changed_at
    _changed_at = game_now()


def flick(element: str | None = None) -> None:
    """Mode change: quick gun flick (Maliwan, Torgue, Vladof); a Maliwan element also flashes its colour."""
    global _flick_at, _flick_color
    _flick_at = game_now()
    _flick_color = ELEMENT_COLORS.get(element, HUD_TEXT)


def _segments(weapon: UObject) -> list[tuple[str, str]] | None:
    maker = maker_of(weapon)
    uid = weapon.DefinitionData.UniqueId
    if maker == "Maliwan":
        current = maliwan._element_of(weapon.DefinitionData.ElementalPartDefinition)
        if current is None:
            return None
        color = ELEMENT_COLORS[current]
        if game_now() - _flick_at < 0.35:
            color = "#FFFFFF" if game_now() - _flick_at < 0.12 else _flick_color
        segs = [(maliwan.NICE[current].upper(), color)]
        pair = state.value.get(f"maliwan:{uid}")
        if pair and current in pair:
            other = pair[1] if current == pair[0] else pair[0]
            segs += [(" / ", DIM), (maliwan.NICE[other].lower(), DIM)]
        return segs
    if maker == "Vladof":
        if vladof.gun_type(weapon) is None or weapon.DefinitionData.BarrelPartDefinition is None:
            return None
        plan = vladof._plan(weapon)
        if plan is None:
            return None
        kind = plan[1].upper()
        if vladof._is_deployed(weapon):
            if vladof.is_toggle(weapon):
                return [(kind + "  ", HUD_TEXT), ("DEPLOYED", ACTIVE)]
            timed = vladof.time_left(weapon)
            if timed is not None:
                return [(kind + "  ", HUD_TEXT), (f"{timed:.0f}s", ACTIVE)]
            left, total = vladof.shots_left(weapon)
            return [(kind + "  ", HUD_TEXT), (f"{left}/{total}", ACTIVE)]
        left = vladof._ready_at.get(uid, 0) - game_now()
        if left > 0:
            return [(kind + "  ", HUD_TEXT), (f"{left:.0f}s", DIM)]
        return [(kind + "  ", HUD_TEXT), ("READY", READY)]
    if maker == "Torgue":
        if torgue.special_fire(weapon) is not None:
            return None
        if not torgue._is_sticky(weapon):
            return [("IMPACT", HUD_TEXT)]
        n = torgue.stuck_count()
        return [("STICKY", HUD_TEXT)] + ([(f"  x{n}", ACTIVE)] if n else [])
    if maker == "Dahl":
        return [(dahl._label(weapon, dahl.mode_of(weapon)), HUD_TEXT)]
    if maker == "Tediore":
        return [("THROW: ", DIM), (tediore.throw_of(weapon).upper(), HUD_TEXT)]
    return None


def _movie():
    pc = get_pc(possibly_loading=True)
    return pc.myHUD.HUDMovie if pc is not None and pc.myHUD is not None else None


def _make_field(box, name: str, depth: int, x: float, y: float) -> None:
    box.Invoke("createTextField", hud.as_args(name, depth, x, y, FIELD_W, FIELD_H))


def _ensure_field(movie) -> bool:
    """Create the outlined label in the HUD movie (again after a map load) and keep it under the ammo
    number."""
    global _field_ok, _last_html, _font_checked, _last_alpha
    g = movie.GetVariableNumber
    if movie.GetVariableObject(MAIN) is None:
        p1 = movie.GetVariableObject("_root.p1")
        if p1 is None:
            return False
        if movie.GetVariableObject(BOX) is None:
            p1.CreateEmptyMovieClip(BOX_NAME, int(p1.GetNextHighestDepth()))
        box = movie.GetVariableObject(BOX)
        if box is None:
            if not _field_ok:
                log("Mode indicator: CreateEmptyMovieClip failed")
            return False
        for i, (dx, dy) in enumerate(OUTLINE_OFFSETS):  # black copies first (lower depths) ...
            _make_field(box, f"o{i}", i + 1, dx, dy)
        _make_field(box, "main", len(OUTLINE_OFFSETS) + 1, 0, 0)  # ... white text on top
        if movie.GetVariableObject(MAIN) is None:
            if not _field_ok:
                log("Mode indicator: createTextField failed")
            return False
        for path in [MAIN] + [f"{BOX}.o{i}" for i in range(len(OUTLINE_OFFSETS))]:
            for prop, value in (("html", True), ("embedFonts", True), ("selectable", False), ("multiline", False), ("wordWrap", False)):
                movie.SetVariableBool(f"{path}.{prop}", value)
        _last_html, _font_checked, _last_alpha = "", False, None
    num = "_root.p1.bullets.bullet_c"
    right = g("_root.p1.bullets._x") + g(num + "._x") + g(num + "._width") - 4
    below = g("_root.p1.bullets._y") + g(num + "._y") + g(num + "._height") - 6
    movie.SetVariableNumber(BOX + "._x", right - FIELD_W)
    movie.SetVariableNumber(BOX + "._y", below)
    _field_ok = True
    return True


def _html(segs: list[tuple[str, str]], size: int, color: str | None = None) -> str:
    """color forces one colour for every segment (the black outline copies)."""
    spans = "".join(f'<font color="{color or c}">{t}</font>' for t, c in segs)
    return f'<p align="right"><font face="{FONT}" size="{size}">{spans}</font></p>'


def _arms_flick(now: float) -> None:
    """Quick twist-and-dip of the first-person gun after a Maliwan element change (~0.32 s)."""
    global _arms_rest
    if _arms_rest is None and not 0 <= now - _flick_at < 0.32:
        return  # no flick playing and none to put back: the arms are not even looked up
    pc = get_pc(possibly_loading=True)
    arms = getattr(pc.Pawn, "Arms", None) if pc is not None and pc.Pawn is not None else None  # a vehicle has no arms
    mesh = arms.SkeletalMesh if arms is not None else None
    if mesh is None:
        return
    t = (now - _flick_at) / 0.32
    if 0 <= t < 1:
        if _arms_rest is None or _arms_rest[0] != mesh._get_address():
            r, o = mesh.RotOrigin, mesh.Origin
            _arms_rest = (mesh._get_address(), (r.Pitch, r.Yaw, r.Roll), (o.X, o.Y, o.Z))
        k = math.sin(math.pi * t)
        (rp, ry, rr), (ox, oy, oz) = _arms_rest[1], _arms_rest[2]
        mesh.RotOrigin.Pitch = int(rp + 1100 * k)
        mesh.RotOrigin.Roll = int(rr + 3600 * k * (1 - t))
        mesh.Origin.Z = oz - 3.0 * k
    elif _arms_rest is not None:
        (rp, ry, rr), (ox, oy, oz) = _arms_rest[1], _arms_rest[2]
        if _arms_rest[0] == mesh._get_address():
            mesh.RotOrigin.Pitch, mesh.RotOrigin.Yaw, mesh.RotOrigin.Roll = rp, ry, rr
            mesh.Origin.X, mesh.Origin.Y, mesh.Origin.Z = ox, oy, oz
        _arms_rest = None


@hook("WillowGame.WillowGameViewportClient:PostRender", hook_identifier="bl3w_mode_indicator")
def draw_indicator(*_: object) -> None:
    global _checked_at, _last_html, _font_checked, _last_alpha
    now = game_now()
    _arms_flick(now)
    movie = _movie()
    if movie is None:
        return
    if now - _checked_at > CHECK_EVERY or not _field_ok:
        _checked_at = now
        if not _ensure_field(movie):
            return
    weapon = held_frame()
    segs = _segments(weapon) if weapon is not None and mode_option.value != "Off" else None
    since = now - _changed_at
    if not segs:
        alpha = 0.0
    elif mode_option.value == "Only On Change":
        alpha = 0.0 if since > CHANGE_SHOW + SETTLE_TIME else (1.0 if since < CHANGE_SHOW else 1 - (since - CHANGE_SHOW) / SETTLE_TIME)
    elif since < POP_TIME:
        alpha = 1.0
    else:
        alpha = max(QUIET, 1 - (since - POP_TIME) / SETTLE_TIME * (1 - QUIET))
    if alpha != _last_alpha:
        _last_alpha = alpha
        movie.SetVariableNumber(BOX + "._alpha", alpha * 100)
    if not segs:
        return
    size = POP_SIZE if since < 0.2 else FONT_SIZE
    html = _html(segs, size)
    if html != _last_html:
        _last_html = html
        movie.SetVariableString(MAIN + ".htmlText", html)
        outline = _html(segs, size, "#000000")
        for i in range(len(OUTLINE_OFFSETS)):
            movie.SetVariableString(f"{BOX}.o{i}.htmlText", outline)
        if not _font_checked:
            _font_checked = True
            if movie.GetVariableNumber(MAIN + ".textWidth") <= 0:  # embedded font missing: use a plain font
                for path in [MAIN] + [f"{BOX}.o{i}" for i in range(len(OUTLINE_OFFSETS))]:
                    movie.SetVariableBool(path + ".embedFonts", False)
                _last_html = ""
                log(f"Mode indicator: {FONT} didn't render in the new field, using a plain font")


def reset() -> None:
    global _last_alpha
    _last_alpha = None
    movie = _movie()
    box = movie.GetVariableObject(BOX) if movie is not None else None
    if box is not None:
        movie.SetVariableNumber(BOX + "._alpha", 0)


HOOKS = [draw_indicator]
OPTIONS = [mode_option]
