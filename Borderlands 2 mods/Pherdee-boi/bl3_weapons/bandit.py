"""Bandit -> BL3 Children of Vladof: no reloads, every shot builds heat; at full heat the gun locks
until it has fully vented. A heat gauge is drawn under the crosshair.

Hooks (signatures read from WillowGame.upk / Engine.upk):
  WillowWeapon.ConsumeAmmo(byte FireModeNum)   once per shot -> add heat, keep the magazine full
  WillowWeapon.BeginFire(byte FireModeNum)     blocked while overheated
  WillowWeapon.ShouldRefire() -> bool          False while overheated (stops automatic fire)
  WillowWeapon.StartReload / ReloadDone        manual vent (v0.38.0, below)

Manual vent (v0.38.0): the magazine is kept full, so BL2 would never start a reload. Once the heat is at or above
"COV Reload To Vent From Heat %" (and there is reserve ammo) the magazine is kept ONE round short, which makes the
reload key work: BL2 plays its own reload, and when it is done the heat is gone (it takes that one round from the
reserve, so the reserve number on the HUD is meaningful). Below that heat the magazine is full again and the key
does nothing, as before.

Slow guns (v0.38.0): heat used to be a fixed amount per shot against a fixed cool-down delay after each shot, so a
gun firing once every 0.8 s (a shotgun) cooled more than each shot added and never overheated. Now a shot adds at
least (time since the previous shot) / "COV Slow Gun Overheat Time", and the cool-down only starts after a pause a
little longer than the gun's own shot interval. Fast guns are unaffected: their per-shot heat is the larger of the two.
"""


import unrealsdk

from mods_base import SliderOption, get_pc, hook
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import UObject

from . import hud
from .core import held_weapon, log, maker_of, now as game_now

AS_TYPE = unrealsdk.find_enum("ASType")

shots_option = SliderOption(
    "COV Shots To Overheat",
    35,
    25,
    200,
    5,
    True,
    description="Percent of the gun's magazine you can fire in one burst before a Bandit (COV) gun overheats.",
)
vent_option = SliderOption(
    "COV Reload To Vent From Heat %",
    50,
    20,
    100,
    5,
    True,
    description="Reload (uses 1 reserve round) cools a Bandit gun instantly once its heat is at least this high. 100 = only when overheated.",
)
slow_option = SliderOption(
    "COV Slow Gun Overheat Time",
    3,
    3,
    15,
    1,
    True,
    description="Seconds of steady fire for a slow Bandit gun (shotguns, snipers) to overheat, however few shots that is.",
)
OPTIONS = [shots_option, vent_option, slow_option]
VENT_DELAY = 0.4  # s after the last shot before heat starts dropping (fast guns)
VENT_TIME = 2.5  # s to vent from full when not overheated
LOCK_TIME = 3.5  # s to vent from full after an overheat (gun locked meanwhile)
SUSTAIN_GAP = 1.5  # s: shots closer than this are one burst (their gap is the gun's shot interval)
GAP_MARGIN = 1.25  # the cool-down waits this many shot intervals after the last shot
RELOAD_VENT_TIME = 1.0  # s: the gauge drains this fast while the vent reload plays
RELOAD_MAX = 5.0  # s: a vent reload that never finished (weapon swap) stops counting after this

# This session only: UniqueId -> heat 0..1 / locked flag / last shot time / shot interval / reload start time
_heat: dict[int, float] = {}
_locked: set[int] = set()
_last_shot: dict[int, float] = {}
_interval: dict[int, float] = {}
_reloading: dict[int, float] = {}
_last_tick = game_now()


def _is_cov(weapon: UObject | None) -> bool:
    return weapon is not None and maker_of(weapon) == "Bandit"


def _is_held(obj: UObject) -> bool:
    held = held_weapon()
    return held is not None and held._get_address() == obj._get_address()


def _shots_to_overheat(weapon: UObject) -> int:
    return max(8, round(weapon.ClipSize * shots_option.value / 100))


@hook("WillowGame.WillowWeapon:ConsumeAmmo", Type.POST)
def on_shot(obj: UObject, *_: object) -> None:
    if not _is_cov(obj) or not _is_held(obj):
        return
    uid = obj.DefinitionData.UniqueId
    now = game_now()
    previous = _last_shot.get(uid)
    if previous is not None and now - previous <= SUSTAIN_GAP:
        _interval[uid] = now - previous  # kept after the burst: the next one's first shot already knows it
    gain = max(1 / _shots_to_overheat(obj), _interval.get(uid, 0.0) / slow_option.value)
    heat = _heat.get(uid, 0.0) + gain
    _last_shot[uid] = now
    if heat >= 1:
        heat = 1.0
        _locked.add(uid)
        log(f"COV #{uid}: overheated")  # the gauge shows OVERHEATED; no popup
    _heat[uid] = heat
    obj.ReloadCnt = _magazine(obj, heat)


def _magazine(weapon: UObject, heat: float) -> int:
    """Rounds the magazine shows: full, or one short while the gun is hot enough to vent so that BL2 accepts a reload
    (a one-round magazine can't be one short without being empty, and no reserve means no reload to offer)."""
    clip = int(weapon.ClipSize)
    if clip >= 2 and heat >= vent_option.value / 100 and weapon.GetAmmoCount() > 0:
        return clip - 1
    return clip


@hook("WillowGame.WillowWeapon:StartReload", Type.POST, hook_identifier="bl3w_cov_reload_start")
def on_reload_start(obj: UObject, *_: object) -> None:
    if _is_cov(obj):
        _reloading[obj.DefinitionData.UniqueId] = game_now()


@hook("WillowGame.WillowWeapon:ReloadDone", Type.POST, hook_identifier="bl3w_cov_reload_done")
def on_reload_done(obj: UObject, *_: object) -> None:
    if not _is_cov(obj):
        return
    uid = obj.DefinitionData.UniqueId
    if _reloading.pop(uid, None) is not None or uid in _heat:
        log(f"COV #{uid}: vented by reload ({_heat.get(uid, 0.0):.0%} heat)")
    _heat.pop(uid, None)
    _locked.discard(uid)
    obj.ReloadCnt = obj.ClipSize


@hook("WillowGame.WillowWeapon:BeginFire")
def block_fire(obj: UObject, *_: object) -> type[Block] | None:
    if _is_cov(obj) and obj.DefinitionData.UniqueId in _locked and _is_held(obj):
        return Block
    return None


@hook("WillowGame.WillowWeapon:ShouldRefire")
def block_refire(obj: UObject, *_: object) -> tuple[type[Block], bool] | None:
    if _is_cov(obj) and obj.DefinitionData.UniqueId in _locked and _is_held(obj):
        return Block, False
    return None


@hook("WillowGame.WillowGameViewportClient:Tick")
def vent(*_: object) -> None:
    global _last_tick
    now = game_now()
    dt, _last_tick = now - _last_tick, now
    for uid, started in list(_reloading.items()):
        if now - started > RELOAD_MAX:
            _reloading.pop(uid)
    for uid, heat in list(_heat.items()):
        if uid in _reloading:
            heat -= dt / RELOAD_VENT_TIME  # the vent reload: ReloadDone clears the rest
        elif uid in _locked:
            heat -= dt / LOCK_TIME
        elif now - _last_shot.get(uid, 0) > max(VENT_DELAY, GAP_MARGIN * _interval.get(uid, 0.0)):
            heat -= dt / VENT_TIME
        if heat <= 0:
            _heat.pop(uid)
            _locked.discard(uid)
        else:
            _heat[uid] = heat


# Heat gauge (v5): a COPY of BL2's ammo counter shows the heat. The game rewrites the real ammo number
# natively on every shot (no hookable script call), which flickered through any re-write (v0.12.2-4).
# So while a COV gun is held: _root.p1.bullets is duplicated (AS2 duplicateMovieClip via
# GFxObject.Invoke), the original is made invisible with _alpha = 0 (the HUD scripts toggle _visible,
# never _alpha), and the heat is written only into the copy, which the game never touches.
# The segmented heat bar is drawn on the Canvas under the copy's number. (v0.13.1 tried filling the
# copy's own sloped tick bar - frames 1-101 of the clip - with a whole-counter tint; the user preferred
# this version.)
SEGMENTS = 20
YELLOW, ORANGE, RED = (255, 214, 70, 235), (255, 138, 28, 240), (255, 42, 18, 250)
WARN_AT = 0.8
SLANT = 0.45  # horizontal shift per pixel of height, like BL2's slanted bars
BANDS = 4
ORIGINAL = "_root.p1.bullets"
COPY_NAME = "bl3_heatcounter"
COPY = "_root.p1." + COPY_NAME
NUMBER = COPY + ".bullet_c"
CHECK_EVERY = 0.5  # s: re-read layout / recreate the copy after a map load rebuilt the HUD

_active = False
_last_text = ""
_layout: tuple[float, float, float, float] | None = None  # number box in stage units: x, y, w, h
_stage: tuple[float, float] = (1280.0, 720.0)
_checked_at = -1e9
_logged = False


def _segment_color(pos: float) -> tuple[int, int, int, int]:
    """Colour of a segment by its position along the bar (0..1)."""
    return hud.lerp_color(YELLOW, ORANGE, pos / 0.6) if pos < 0.6 else hud.lerp_color(ORANGE, RED, (pos - 0.6) / 0.4)


def _movie():
    pc = get_pc(possibly_loading=True)
    hud_actor = pc.myHUD if pc is not None else None
    return hud_actor.HUDMovie if hud_actor is not None else None


def _as_args(*values: object) -> list:
    """Python values -> GFxUI ASValue structs for GFxObject.Invoke."""
    out = []
    for v in values:
        if isinstance(v, str):
            out.append(unrealsdk.make_struct("ASValue", Type=AS_TYPE.AS_String, S=v))
        else:
            out.append(unrealsdk.make_struct("ASValue", Type=AS_TYPE.AS_Number, N=float(v)))
    return out


def _ensure_copy(movie) -> bool:
    """Make sure the heat counter copy exists and the original is hidden. True if the copy is usable."""
    global _last_text, _logged
    if movie.GetVariableObject(COPY) is None:
        original = movie.GetVariableObject(ORIGINAL)
        p1 = movie.GetVariableObject("_root.p1")
        if original is None or p1 is None:
            return False
        original.Invoke("duplicateMovieClip", _as_args(COPY_NAME, p1.GetNextHighestDepth()))
        if movie.GetVariableObject(COPY) is None:
            if not _logged:
                _logged = True
                log("HUD: duplicateMovieClip of the ammo counter failed")
            return False
        movie.SetVariableBool(COPY + ".bullet_icon._visible", False)  # the copy has no live ammo icon
        movie.SetVariableBool(COPY + ".flash._visible", False)
        _last_text = ""
        if not _logged:
            _logged = True
            log("HUD: heat counter copy created")
    movie.SetVariableNumber(ORIGINAL + "._alpha", 0)
    return True


def _read_layout(movie) -> None:
    """Number box of the copy in HUD-stage units (p1 + copy + bullet_c)."""
    global _layout, _stage
    try:
        g = movie.GetVariableNumber
        x = g("_root.p1._x") + g(COPY + "._x") + g(NUMBER + "._x")
        y = g("_root.p1._y") + g(COPY + "._y") + g(NUMBER + "._y")
        w, h = g(NUMBER + "._width"), g(NUMBER + "._height")
        sw, sh = g("Stage.width"), g("Stage.height")
        if w > 0 and h > 0 and sw > 0 and sh > 0:
            _layout, _stage = (x, y, w, h), (sw, sh)
    except Exception as e:  # noqa: BLE001 - fall back to the estimate in draw_gauge
        log(f"HUD layout read failed: {e!r}")


def _set_number(movie, html: str) -> None:
    global _last_text
    if html != _last_text:
        _last_text = html
        movie.SetVariableString(NUMBER + ".htmlText", html)


RESERVE_SIZE = 16  # font size of the reserve count next to the heat (the heat keeps the counter's own size)
RESERVE_COLOR, RESERVE_EMPTY = "#C8D0E0", "#FF4628"


def _number_html(heat: float, locked: bool, now: float, reserve: int, can_vent: bool) -> str:
    """Heat (percent, or the seconds left of a lock) and the reserve ammo: '47%  / 312'. `can_vent`: the gun is hot
    enough for the reload key to cool it, so the reserve count lights up (a reload costs one round from it)."""
    if locked:
        color = "#FF4628" if int(now * 7) % 2 == 0 else "#FFBEA0"
        main = f'<font color="{color}">{heat * LOCK_TIME:.1f}s</font>'
    else:
        plain = f"{int(heat * 100 + 0.5)}%"
        main = f'<font color="#FF8A1C">{plain}</font>' if heat >= WARN_AT else plain
    if reserve <= 0:
        shade = RESERVE_EMPTY
    else:
        shade = "#78E678" if can_vent else RESERVE_COLOR
    return f'<p align="right">{main}<font size="{RESERVE_SIZE}" color="{shade}">  / {reserve}</font></p>'


def _take_over(on: bool) -> None:
    """Swap in the heat counter copy, or put BL2's ammo counter back."""
    global _active, _last_text, _checked_at
    if on == _active:
        return
    _active = on
    _last_text = ""
    _checked_at = -1e9
    if not on:
        movie = _movie()
        if movie is not None:
            copy = movie.GetVariableObject(COPY)
            if copy is not None:
                copy.RemoveMovieClip()
            movie.SetVariableNumber(ORIGINAL + "._alpha", 100)
            movie.UpdateAmmo()


def restore_ammo() -> None:
    _take_over(False)


def _slanted(canvas: UObject, x: float, y: float, w: float, h: float, rgba: tuple[int, int, int, int]) -> None:
    """A parallelogram leaning right (top edge shifted right), drawn as stepped bands."""
    band = h / BANDS
    for i in range(BANDS):
        shift = (BANDS - 1 - i) * band * SLANT
        hud.rect(canvas, x + shift, y + i * band, w, band + 0.5, rgba)


@hook("WillowGame.WillowGameViewportClient:PostRender")
def draw_gauge(_obj: UObject, args: object, *_: object) -> None:
    global _checked_at
    weapon = held_weapon()
    cov = _is_cov(weapon)
    _take_over(cov)
    if not cov:
        return
    movie = _movie()
    if movie is None:
        return
    now = game_now()
    if now - _checked_at > CHECK_EVERY:
        _checked_at = now
        if not _ensure_copy(movie):
            return
        _read_layout(movie)
    uid = weapon.DefinitionData.UniqueId
    heat, locked = _heat.get(uid, 0.0), uid in _locked
    flash = int(now * 7) % 2 == 0

    # the magazine is one round short only while the gun is hot enough to vent (see _magazine); not mid-reload
    if str(weapon.GetStateName()) != "WeaponReloading" and uid not in _reloading:
        wanted = _magazine(weapon, heat)
        if int(weapon.ReloadCnt) != wanted:
            weapon.ReloadCnt = wanted
    reserve = int(weapon.GetAmmoCount())
    can_vent = heat >= vent_option.value / 100 and reserve > 0 and int(weapon.ClipSize) >= 2

    # the number: in the copy of BL2's ammo counter
    _set_number(movie, _number_html(heat, locked, now, reserve, can_vent))

    # the bar, right under that number (stage units -> screen)
    canvas = args.Canvas
    sw, sh = _stage
    f = canvas.SizeY / sh
    off_x = (canvas.SizeX - sw * f) / 2
    if _layout is not None:
        nx, ny, nw, nh = _layout
        right = off_x + (nx + nw - 6) * f  # right edge of the right-aligned digits
        top = (ny + nh - 2) * f
        bar_w = nw * 0.82 * f
    else:  # estimate (1080p-tuned) until the layout is known
        s = hud.scale(canvas)
        right, top, bar_w = canvas.SizeX - 150 * s, canvas.SizeY - 60 * s, 250 * s
    bar_h, gap = 9 * f, 1.6 * f
    x = right - bar_w
    slant_w = bar_h * SLANT
    pad = 2.5 * f
    _slanted(canvas, x - pad - slant_w, top - pad, bar_w + 2 * pad, bar_h + 2 * pad, (6, 8, 14, 165))
    pulse = 0.65 + 0.35 * abs(((now * 3.0) % 2.0) - 1.0)
    seg_w = (bar_w - gap * (SEGMENTS - 1)) / SEGMENTS
    filled = heat * SEGMENTS
    for i in range(SEGMENTS):
        sx = x - slant_w + i * (seg_w + gap)
        part = max(0.0, min(1.0, filled - i))
        if part <= 0:
            _slanted(canvas, sx, top, seg_w, bar_h, (200, 219, 252, 30))
            continue
        if locked:
            color = (255, 235, 220, 255) if flash else RED
        else:
            color = _segment_color(i / (SEGMENTS - 1))
            if heat >= WARN_AT:
                color = hud.with_alpha(color, pulse)
        _slanted(canvas, sx, top, seg_w * part, bar_h, color)


HOOKS = [on_shot, on_reload_start, on_reload_done, block_fire, block_refire, vent, draw_gauge]
