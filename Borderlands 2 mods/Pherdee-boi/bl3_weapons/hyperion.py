"""Hyperion: aiming down sights raises a frontal gun shield (BL3 Hyperion trait).

While a Hyperion gun is zoomed (WillowWeapon.ZoomState ZST_ZoomingIn/ZST_Zoomed), hits on the player
coming from within FRONT_ANGLE of where they look are absorbed by the shield's own pool. It breaks at
0, then recharges after a delay. Damage-over-time (status effects) always gets through, as in BL3.

Hook: WillowPlayerPawn.TakeDamage(Damage, InstigatedBy, HitLocation, Momentum, DamageType, ...).
A partly absorbed hit is re-issued with the remainder, like the anointment damage scaling.

Visuals:
  * HUD: BL2-style slanted segmented bar under the crosshair (hud.segmented_bar), SHIELD / % labels,
    white flash on absorbed hits, grey + recharge countdown when broken.
  * The shield itself: a BL3-style translucent hex energy panel over the gun while aiming, drawn on
    the Canvas with BL2's own player-shield hex texture (FX_CHAR_Shared_Shield.Textures.*, always
    loaded) via DrawTile with additive blending: deploy, shimmer + scrolling hexes, flash on absorbed
    hits, flicker when low, burst into fragments on break, fade when lowered.
    (v0.14.0 tried Axton's turret shield particles: they're camera-facing sprites that read as a bubble
    around the player, and their projector glow never despawns.)
"""

import math
import random

import unrealsdk
from mods_base import BoolOption, DropdownOption, SliderOption, get_pc, hook
from unrealsdk.hooks import Block, prevent_hooking_direct_calls
from unrealsdk.unreal import UObject, WeakPointer

from . import hud
from .core import held_weapon, log, active_maker_of as maker_of, now as game_now, remembered

FRONT_ANGLE = 60.0  # degrees either side of the view direction
REGEN_DELAY, BROKEN_DELAY, REGEN_TIME = 2.5, 5.0, 2.0  # seconds
UP_STATES = {"ZST_ZoomingIn", "ZST_Zoomed"}

shield_pct = SliderOption(
    "Hyperion Shield Strength",
    50,
    10,
    200,
    10,
    True,
    description="Hyperion gun shield capacity, as a percent of your max health.",
)
fx_on = BoolOption("Hyperion Shield Visual", True, description="Show an energy shield over the gun while aiming.")
panel_size = SliderOption("Shield Panel Size", 53, 10, 70, 1, True, description="Shield panel width, in percent of the screen width.")
panel_height = SliderOption("Shield Panel Height", 60, 40, 90, 1, True, description="Vertical position of the panel centre, in percent of the screen height.")
panel_opacity = SliderOption("Shield Panel Opacity", 10, 10, 150, 5, True, description="How visible the shield panel is (percent; lower = more see-through).")
WORLD_MATERIALS = {  # label: material path (all in Startup.upk, so always loaded)
    "Hex Hit": "FX_CHAR_Shared_Shield.Materials.Material_ShieldHitHex2",
    "Digistruct Pane": "FX_IO_Digistruct.Materials.Mati_DigiPaneXPlane",
    "Digital Shield": "FX_CHAR_Shared_Shield.Materials.MasterShaders.Mat_Digital_Shield_MS",
    "Shield Distortion": "FX_CHAR_Shared_Shield.Materials.MasterShaders.Mat_Shield_Distortion_MS",
    "Generic Shield": "FX_CHAR_Shared_Shield.Materials.Instances.Digital.Dahl.Basic.MQ.Mati_Shield_Generic",
    "BL3 Pale": "FX_CHAR_Shared_Shield.Materials.Instances.Digital.Dahl.Basic.MQ.Mati_Shield_Generic",  # the same, made pale and see-through (_pale)
}
WORLD_MESHES = {
    "Hex Plate": "FX_WEP_Scopes.Meshes.Mesh_Maliwan_Sniper_Scope_Cover_Hex",
    "Flat Plane": "FX_Shared_Mesh.Meshes.Planes.DecalPlane_LiquidDev",
}
FACINGS = ["Auto", "X", "-X", "Y", "-Y", "Z", "-Z"]
visual_style = DropdownOption("Shield Visual Style", "In World", ["In World", "On Screen"], description="In World: a real shield object in front of you (your gun draws over it). On Screen: a HUD panel.")
world_material = DropdownOption("Shield Material", "BL3 Pale", list(WORLD_MATERIALS), description="Which BL2 shield material the in-world shield uses.")
world_mesh = DropdownOption("Shield Mesh", "Hex Plate", list(WORLD_MESHES), description="Shape of the in-world shield.")
world_facing = DropdownOption("Shield Mesh Facing", "Auto", FACINGS, description="Change if the shield is seen edge-on or from behind.")
world_size = SliderOption("World Shield Size", 70, 10, 150, 1, True, description="Width of the in-world shield (Unreal units).")
world_dist = SliderOption("World Shield Distance", 75, 20, 200, 1, True, description="Distance in front of the camera (Unreal units).")
world_drop = SliderOption("World Shield Height", -10, -40, 20, 1, True, description="Height relative to your eyes (Unreal units).")
world_tint = DropdownOption("Shield Tint", "Blue", ["Light Blue", "Blue", "White"], description="Colour of the in-world Hex Hit shield.")
TINTS = {"Light Blue": (0.55, 0.8, 1.0), "Blue": (0.3, 0.6, 1.0)}
world_cells = DropdownOption("Shield Cells", "6 (BL3)", ["1", "7", "19", "6 (BL3)"], description="Build the in-world shield from this many hex cells (more = sharper). 6 (BL3): three cells each side of the gun, low in the view, like Borderlands 3's.")
OPTIONS = [
    shield_pct, fx_on, visual_style, world_material, world_tint, world_mesh, world_cells, world_facing, world_size, world_dist, world_drop,
    panel_size, panel_height, panel_opacity,
]

# BL3's four Hyperion shields, each its own colour. A gun rolls its shield once (core.remembered), and the item card names it.
# colour: the cells' fill (the material is additive, so these are how bright each channel is); ui: the meter's; line: the outline's and the glow's; html: the card's.
VARIANTS = {
    "Hyperion": {"name": "Hyperion Shield", "text": "Aim down sights: frontal energy shield", "colour": (0.5, 0.85, 1.1), "ui": (82, 210, 232), "line": (175, 218, 255), "html": "#7FD6F0"},
    "Amped": {"name": "Amped Shield", "text": "Damage taken becomes weapon damage for a short time", "colour": (0.85, 0.5, 1.2), "ui": (178, 108, 240), "line": (212, 170, 255), "html": "#B56CF0"},
    "Genesis": {"name": "Genesis Shield", "text": "Damage taken becomes a bullet in the loaded magazine", "colour": (0.4, 1.1, 0.85), "ui": (70, 220, 185), "line": (160, 245, 225), "html": "#46DCB9"},
    "Redirect": {"name": "Redirect Shield", "text": "Damage taken is reflected back at attackers", "colour": (0.8, 0.75, 0.45), "ui": (204, 190, 120), "line": (225, 215, 160), "html": "#CCBE78"},
}
variant_chance = SliderOption("Hyperion Shield Variant Chance", 50, 0, 100, 5, True, description="Percent of Hyperion guns that roll an Amped, Genesis or Redirect shield instead of the plain one (rolled once per gun). 0: every gun has the plain shield.")
OPTIONS += [variant_chance]
AMP_SECONDS, AMP_PER_SHIELD, AMP_MAX = 5.0, 1.0, 1.0  # Amped: a hit worth the whole shield adds this much damage (a share, 1.0 = +100%), for this long; it stacks up to the max
GENESIS_STEP = 0.10  # Genesis: one bullet for a hit, and one more for each of this share of your max health in it

_amp = 0.0  # the Amped bonus now (a share of damage), and when it ends
_amp_until = -1e9
_reflecting = False  # Redirect's own hit on the attacker must not be absorbed again


def variant_of(weapon: UObject | None) -> str:
    """"Hyperion", "Amped", "Genesis" or "Redirect": the shield this gun raises (rolled once per gun)."""
    if weapon is None or maker_of(weapon) != "Hyperion" or weapon.DefinitionData.UniqueId == 0:
        return "Hyperion"
    rolled = remembered("hyshield", weapon, lambda rng: [rng.choice(("Amped", "Genesis", "Redirect")) if rng.random() * 100 < variant_chance.value else "Hyperion"])
    return rolled[0] if rolled and rolled[0] in VARIANTS else "Hyperion"


def amp_multiplier(weapon: UObject | None) -> float:
    """The damage multiplier an Amped shield gives the held gun (1.0 when it isn't one, or the bonus ran out)."""
    global _amp
    if weapon is None or variant_of(weapon) != "Amped":
        return 1.0
    if game_now() > _amp_until:
        _amp = 0.0
        return 1.0
    return 1.0 + _amp


def _on_absorbed(pc: UObject, weapon: UObject | None, args, absorbed: float) -> None:
    """What the gun's shield does with the damage it took."""
    global _amp, _amp_until, _reflecting
    variant = variant_of(weapon)
    if variant == "Amped":
        _amp = min(AMP_MAX, _amp + AMP_PER_SHIELD * absorbed / max(_max_hp(), 1.0))
        _amp_until = game_now() + AMP_SECONDS
    elif variant == "Genesis":
        health = max(pc.Pawn.GetMaxHealth(), 1.0)
        bullets = 1 + int(absorbed / (health * GENESIS_STEP))
        weapon.ReloadCnt = min(int(weapon.ClipSize), int(weapon.ReloadCnt) + bullets)
    elif variant == "Redirect" and not _reflecting:
        attacker = args.InstigatedBy.Pawn if args.InstigatedBy is not None else None
        if attacker is None or attacker._get_address() == pc.Pawn._get_address():
            return
        _reflecting = True
        try:
            attacker.TakeDamage(Damage=absorbed, InstigatedBy=pc, HitLocation=attacker.Location, Momentum=unrealsdk.make_struct("Vector", X=0, Y=0, Z=0), DamageType=args.DamageType, DamageCauser=pc.Pawn)
        except Exception as ex:  # noqa: BLE001 - a reflection that fails must not break the shield
            log(f"Hyperion Redirect: couldn't hit the attacker ({ex!r})")
        finally:
            _reflecting = False


_hp = -1.0  # current shield points (-1 = not initialised)
_last_hit = -1e9
_broken_at = -1e9
_last_tick = game_now()


def _max_hp() -> float:
    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc else None
    return pawn.GetMaxHealth() * shield_pct.value / 100 if pawn else 0.0


def _aiming(weapon: UObject | None) -> bool:
    if weapon is None or maker_of(weapon) != "Hyperion":
        return False
    zoom = weapon.ZoomState
    return getattr(zoom, "name", str(zoom)) in UP_STATES


def shield_up(weapon: UObject | None) -> bool:
    return _aiming(weapon) and _hp > 0


def _from_front(pc: UObject, pawn: UObject, args) -> bool:
    attacker = args.InstigatedBy.Pawn if args.InstigatedBy is not None else None
    src = attacker.Location if attacker is not None else args.HitLocation
    dx, dy = src.X - pawn.Location.X, src.Y - pawn.Location.Y
    if dx == 0 and dy == 0:
        return False
    to_src = math.degrees(math.atan2(dy, dx))
    facing = pc.Rotation.Yaw * 360 / 65536
    diff = (to_src - facing + 180) % 360 - 180
    return abs(diff) <= FRONT_ANGLE


@hook("WillowGame.WillowPlayerPawn:TakeDamage")
def absorb(obj: UObject, args, _ret, func) -> type[Block] | None:
    global _hp, _last_hit, _broken_at
    pc = get_pc(possibly_loading=True)
    if pc is None or pc.Pawn is None or obj._get_address() != pc.Pawn._get_address():
        return None
    if args.DamageType is None or args.DamageType.Name == "WillowDmgSource_StatusEffect":
        return None
    if not shield_up(held_weapon()) or not _from_front(pc, obj, args):
        return None

    absorbed = min(args.Damage, _hp)
    _hp -= absorbed
    _last_hit = game_now()
    _on_absorbed(pc, held_weapon(), args, absorbed)
    _fx_event("hit")
    if _hp <= 0:
        _hp = 0.0
        _broken_at = _last_hit
        _fx_event("break")
    rest = args.Damage - absorbed
    if rest > 0:
        args.Damage = rest
        with prevent_hooking_direct_calls():
            func(args)
    return Block


@hook("WillowGame.WillowGameViewportClient:Tick")
def regen(*_: object) -> None:
    global _hp, _last_tick
    now = game_now()
    dt, _last_tick = now - _last_tick, now
    _fx_tick()
    cap = _max_hp()
    if cap <= 0:
        return
    if _hp < 0:
        _hp = cap
        log(f"Hyperion shield capacity {cap:.0f}")
    if _hp >= cap:
        _hp = cap
        return
    delay = BROKEN_DELAY if _broken_at >= _last_hit else REGEN_DELAY
    if now - _last_hit > delay:
        _hp = min(cap, _hp + cap * dt / REGEN_TIME)


# --------------------------------------------------------------------------- the energy shield panel

HEX_TEXTURES = [  # BL2 player-shield FX textures (tileable, DXT1 -> additive blend)
    "FX_CHAR_Shared_Shield.Textures.Tex_Shield_Hexes_GP",
    "FX_CHAR_Shared_Shield.Textures.BasicHex_Diff",
    "FX_CHAR_Shared_Shield.Textures.Hex_Pattern_GreyPack_Alpha_Dif",
]
BLEND_ADDITIVE = 3  # EBlendMode
PANEL_TINT = (0.35, 0.75, 1.0)  # Hyperion shield blue
DEPLOY_TIME, LOWER_TIME, BREAK_TIME = 0.15, 0.12, 0.45
ROWS = 14  # the hex silhouette is drawn as horizontal slices
HEX_REPEAT = 3.2  # texture repeats across the panel width

_tex: WeakPointer | None = None
_tex_tried = False
_panel_open = 0.0  # 0 closed .. 1 fully deployed
_breaking_at = -1e9
_fragments: list[tuple[float, float, float, float]] = []  # (angle, speed, size, spin) per fragment


def _texture() -> UObject | None:
    global _tex, _tex_tried
    if _tex is not None and _tex() is None:  # unloaded: look it up again
        _tex, _tex_tried = None, False
    if _tex is None and not _tex_tried:
        _tex_tried = True
        for path in HEX_TEXTURES:
            try:
                _tex = WeakPointer(unrealsdk.find_object("Texture2D", path))
                log(f"Hyperion shield panel texture: {path}")
                break
            except ValueError:
                continue
        if _tex is None:
            log("Hyperion shield panel: no hex texture loaded, drawing a plain panel")
    return _tex() if _tex is not None else None


_last_burst = 0.0


def _fx_event(kind: str) -> None:
    """Hit flashes are read from _last_hit in the draw; a break starts the shatter."""
    global _breaking_at, _fragments
    global _last_burst
    now = game_now()
    if kind == "break" or now - _last_burst > 0.15:
        _last_burst = now
        _world_burst(get_pc(possibly_loading=True))
    if kind == "break":
        _breaking_at = game_now()
        rnd = random.Random(int(_breaking_at * 1000))
        _fragments = [(rnd.uniform(0, math.tau), rnd.uniform(0.25, 0.7), rnd.uniform(0.03, 0.07), rnd.uniform(-6, 6)) for _ in range(22)]


def _fx_tick() -> None:
    """Panel open/close is animated in the draw (it needs the frame time); nothing to do per tick."""


def reset() -> None:
    global _panel_open, _breaking_at, _amp
    _panel_open, _breaking_at, _amp = 0.0, -1e9, 0.0
    _world_reset()
    _meter_off()


def _tile(canvas: UObject, tex: UObject | None, x: float, y: float, w: float, h: float, u: float, v: float, ul: float, vl: float, rgb: tuple[float, float, float], a: float) -> None:
    if w <= 0 or h <= 0 or a <= 0:
        return
    canvas.SetPos(x, y, 0)
    if tex is None:
        canvas.SetDrawColor(int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255), int(min(1.0, a) * 90))
        canvas.DrawRect(w, h, canvas.DefaultTexture)
        return
    color = unrealsdk.make_struct("LinearColor", R=rgb[0] * a, G=rgb[1] * a, B=rgb[2] * a, A=1.0)
    canvas.DrawTile(tex, w, h, u, v, ul, vl, color, False, BLEND_ADDITIVE)


def _draw_panel(canvas: UObject, now: float, dt: float, aiming: bool) -> None:
    global _panel_open
    up = aiming and _hp > 0 and fx_on.value
    if up:
        _panel_open = min(1.0, _panel_open + dt / DEPLOY_TIME)
    else:
        _panel_open = max(0.0, _panel_open - dt / LOWER_TIME)
    breaking = now - _breaking_at < BREAK_TIME
    if _panel_open <= 0 and not breaking:
        return
    tex = _texture()
    tw, th = (tex.SizeX, tex.SizeY) if tex is not None else (256, 256)

    cx = canvas.SizeX / 2
    cy = canvas.SizeY * panel_height.value / 100
    full_w = canvas.SizeX * panel_size.value / 100
    full_h = full_w * 0.52

    if breaking:  # shatter: hex fragments fly outward and fade
        t = (now - _breaking_at) / BREAK_TIME
        a = (1 - t) * 1.6
        for ang, speed, size, _spin in _fragments:
            dist = full_w * speed * t
            fs = full_w * size
            fx, fy = cx + math.cos(ang) * dist - fs / 2, cy + math.sin(ang) * dist * 0.6 - fs / 2
            _tile(canvas, tex, fx, fy, fs, fs, (ang * 97) % tw, (ang * 53) % th, tw * size * HEX_REPEAT, th * size * HEX_REPEAT, (1.0, 0.95, 0.85), a)
        if t < 0.25:  # bright burst
            _tile(canvas, tex, cx - full_w / 2, cy - full_h / 2, full_w, full_h, 0, 0, tw * HEX_REPEAT, th * HEX_REPEAT * 0.52, (1.0, 0.9, 0.8), (0.25 - t) * 6)
        return

    # eased open: grows from the centre
    ease = 1 - (1 - _panel_open) ** 3
    w, h = full_w * (0.6 + 0.4 * ease), full_h * ease
    left, top = cx - w / 2, cy - h / 2
    cap = _max_hp()
    frac = _hp / cap if cap > 0 else 0.0
    hit = max(0.0, 1 - (now - _last_hit) / 0.18)  # 1 right at a hit, fades in 0.18 s
    shimmer = 0.85 + 0.15 * math.sin(now * 5.3)
    flicker = 1.0 if frac > 0.3 else (0.55 + 0.45 * (int(now * 18) % 2))
    # v0.14.1 was too opaque (user): base ~halved, hits softer, then scaled by the opacity option
    alpha = ((0.16 + 0.14 * frac) * shimmer * flicker * ease + hit * 0.55) * panel_opacity.value / 100
    tint = tuple(PANEL_TINT[i] + (1 - PANEL_TINT[i]) * hit * 0.8 for i in range(3))
    scroll_u, scroll_v = now * 18.0, now * 7.0  # slow drift of the hex pattern

    # hexagon (pointy left/right) as horizontal slices: width shrinks toward top and bottom
    row_h = h / ROWS
    uv_w = tw * HEX_REPEAT
    uv_per_px = uv_w / full_w
    for r in range(ROWS):
        mid = (r + 0.5) / ROWS
        inset = abs(mid - 0.5) * 2 * 0.22 * w
        rx, rw = left + inset, w - 2 * inset
        ry = top + r * row_h
        u = scroll_u + (rx - (cx - full_w / 2)) * uv_per_px
        v = scroll_v + (ry - (cy - full_h / 2)) * uv_per_px
        _tile(canvas, tex, rx, ry, rw, row_h + 0.5, u, v, rw * uv_per_px, row_h * uv_per_px, tint, alpha)
        edge = max(2.0, canvas.SizeY * 0.003)  # brighter rim at both ends of each slice
        _tile(canvas, None, rx, ry, edge, row_h + 0.5, 0, 0, 0, 0, tint, alpha * 1.8)  # plain rim, not a texel
        _tile(canvas, None, rx + rw - edge, ry, edge, row_h + 0.5, 0, 0, 0, 0, tint, alpha * 1.8)


# --------------------------------------------------------------------------- the in-world shield

HIT_FX = "FX_CHAR_Shared_Shield.Particles.Part_PC_ShieldHit_Matrix"  # player shield hit, always loaded
WORLD_DEPLOY, WORLD_LOWER, WORLD_BREAK = 0.15, 0.1, 0.14  # s

# StaticMeshComponents of the honeycomb, held weakly: they belong to the pawn, which the game
# destroys on quit-to-menu / level change (v0.17.2 crashed touching them on the menu map).
_cells: list[WeakPointer] = []
_comp_owner = 0  # address of the pawn they are attached to
_comp_mesh = ""
_comp_mat = ""
_world_open = 0.0
_world_last = game_now()
_logged_world = False
_mesh_bounds: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {}  # mesh -> (extent, origin)
_calib: dict | None = None  # {"mesh": name, "at": (x, y, z), "frames": n}
_world_error_logged = False
FALLBACK_BOUNDS = ((1.0, 50.0, 50.0), (0.0, 0.0, 0.0))


def _obj(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _axes(pitch: int, yaw: int, roll: int) -> tuple[tuple[float, float, float], ...]:
    """Unreal rotator -> (X, Y, Z) world axes (FRotationMatrix)."""
    p, y, r = (a * math.tau / 65536 for a in (pitch, yaw, roll))
    sp, cp, sy, cy, sr, cr = math.sin(p), math.cos(p), math.sin(y), math.cos(y), math.sin(r), math.cos(r)
    x_axis = (cp * cy, cp * sy, sp)
    y_axis = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    z_axis = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return x_axis, y_axis, z_axis


def _facing_axis(ext: tuple[float, float, float]) -> str:
    if world_facing.value != "Auto":
        return world_facing.value
    return min((("X", ext[0]), ("Y", ext[1]), ("Z", ext[2])), key=lambda t: t[1])[0]


def _measure(comp: UObject, cam) -> bool:
    """BL2's StaticMesh doesn't expose Bounds to script, but the component does (world-space AABB).
    Place the mesh unrotated at scale 1 for a frame (hidden), then read its extent and centre offset.
    Returns True once the current mesh is measured."""
    global _calib
    name = world_mesh.value
    if name in _mesh_bounds:
        return True
    if _calib is None or _calib["mesh"] != name:
        at = (cam.X, cam.Y, cam.Z - 2000.0)
        comp.SetHidden(True)
        comp.SetScale(1.0)
        comp.SetRotation(unrealsdk.make_struct("Rotator", Pitch=0, Yaw=0, Roll=0))
        comp.SetTranslation(unrealsdk.make_struct("Vector", X=at[0], Y=at[1], Z=at[2]))
        _calib = {"mesh": name, "at": at, "frames": 0}
        return False
    _calib["frames"] += 1
    b = comp.Bounds
    ext = (b.BoxExtent.X, b.BoxExtent.Y, b.BoxExtent.Z)
    if max(ext) <= 0.01 and _calib["frames"] < 10:
        return False  # bounds not updated yet
    if max(ext) <= 0.01:
        _mesh_bounds[name] = FALLBACK_BOUNDS
        log(f"Hyperion world shield: couldn't measure {name}, using a 100x100 guess")
    else:
        at = _calib["at"]
        origin = (b.Origin.X - at[0], b.Origin.Y - at[1], b.Origin.Z - at[2])
        _mesh_bounds[name] = (ext, origin)
        log(f"Hyperion world shield: {name} extent {ext[0]:.1f}/{ext[1]:.1f}/{ext[2]:.1f} centre offset {origin[0]:.1f}/{origin[1]:.1f}/{origin[2]:.1f}")
    _calib = None
    return True


def _mesh_rotation(cam_pitch: int, cam_yaw: int, axis: str) -> tuple[int, int, int]:
    """Rotator that points the mesh's flat-face normal (axis) along the view direction."""
    if axis == "X":
        return cam_pitch, cam_yaw, 0
    if axis == "-X":
        return -cam_pitch, cam_yaw + 32768, 0
    if axis == "Y":
        return 0, cam_yaw - 16384, cam_pitch
    if axis == "-Y":
        return 0, cam_yaw + 16384, -cam_pitch
    if axis == "Z":
        return cam_pitch - 16384, cam_yaw, 0
    return cam_pitch + 16384, cam_yaw, 0  # -Z


def _new_cell(pawn: UObject) -> UObject:
    comp = unrealsdk.construct_object("StaticMeshComponent", pawn)
    comp.SetActorCollision(False, False, False)
    comp.SetTraceBlocking(False, False)
    comp.SetBlockRigidBody(False)
    comp.CastShadow = False
    pawn.AttachComponent(comp)
    comp.SetAbsolute(True, True, True)
    comp.SetHidden(True)
    return comp


def _live() -> list[UObject]:
    """The honeycomb cells that still exist; if any is gone (pawn destroyed), forget them all."""
    global _cells
    objs = [w() for w in _cells]
    if any(o is None for o in objs):
        _cells = []
        return []
    return objs


def _ensure_cells(pawn: UObject, count: int) -> list[UObject] | None:
    """Create/refresh the shield cells on this pawn (mesh/material follow the options)."""
    global _cells, _comp_owner, _comp_mesh, _comp_mat
    if _live() and _comp_owner != pawn._get_address():
        _cells = []  # new pawn (level load / respawn): the old cells went with the old pawn
    if not _live():
        _cells = []
        _comp_owner, _comp_mesh, _comp_mat = pawn._get_address(), "", ""
    while len(_cells) < count:
        _cells.append(WeakPointer(_new_cell(pawn)))
        _comp_mesh = _comp_mat = ""  # new cells need mesh + material
    if _comp_mesh != world_mesh.value:
        mesh = _obj("StaticMesh", WORLD_MESHES[world_mesh.value])
        if mesh is None:
            log(f"Hyperion world shield: mesh {WORLD_MESHES[world_mesh.value]} not loaded")
            return None
        for c in _live():
            c.SetStaticMesh(mesh, True)
        _comp_mesh, _comp_mat = world_mesh.value, ""
    variant = variant_of(held_weapon())
    want_mat = f"{world_material.value}|{world_tint.value}|{variant}"
    if _comp_mat != want_mat:
        mat = None
        for cls in ("MaterialInterface", "Material", "MaterialInstanceConstant"):
            mat = _obj(cls, WORLD_MATERIALS[world_material.value])
            if mat is not None:
                break
        if mat is not None and world_tint.value in TINTS and mat.Class.Name == "Material":
            mat = _tinted(mat, TINTS[world_tint.value])
        elif mat is not None and world_material.value == "BL3 Pale":
            mat = _pale(mat, VARIANTS[variant]["colour"])
        if mat is not None:
            for c in _live():
                c.SetMaterial(0, mat)
            log(f"Hyperion world shield: material {world_material.value} ({world_tint.value})")
        _comp_mat = want_mat
    return _live()


_tint_mics: dict[tuple, WeakPointer] = {}

# Borderlands 3's shield is six pale blue hexagons, three each side of the gun, outside the gun's own silhouette. Centres in pixels of a 1920 wide
# screen from its middle (x right, y up), measured from BL3; scaled by the field of view, so they sit the same on the screen whatever the gun's zoom.
BL3_CELLS = ((-490, -212), (-575, -330), (-480, -460), (485, -212), (570, -330), (475, -460))  # (BL3's gun is narrower: these clear BL2's wider ones)
BL3_RADIUS = 90  # of a cell, pixels (the hex plate fills part of its box)
SCOPE_FIT = (0.9, 0.62, 0.85)  # the layout's x, y and the cell size, with a full-screen scope
_aspect = 16 / 9  # of the screen (draw_shield keeps it): BL2's field of view is the vertical one
BL3_COLOUR = (0.5, 0.85, 1.1)


def _pale(parent: UObject, rgb: tuple[float, float, float] = BL3_COLOUR) -> UObject:
    """A MaterialInstanceConstant of the Generic Shield with a pale, see-through fill and no honeycomb inside."""
    key = (parent._get_address(), rgb)
    mic = _tint_mics[key]() if key in _tint_mics else None
    if mic is not None:
        return mic
    mic = unrealsdk.construct_object("MaterialInstanceConstant", parent.Outer, f"BL3W_ShieldPale_{len(_tint_mics)}")
    mic.ObjectFlags |= 0x4000  # RF_RootSet: keep it through map changes
    mic.SetParent(parent)
    colour = unrealsdk.make_struct("LinearColor", R=rgb[0], G=rgb[1], B=rgb[2], A=1.0)
    for name in ("p_GradientColor_01", "p_GradientColor_02"):
        mic.SetVectorParameterValue(name, colour)
    mic.SetScalarParameterValue("p_BaseTexMultiplier", 2.0)  # the fill: 0.9 is only an outline, 2.5 is solid white
    mic.SetScalarParameterValue("p_InternalTexMultiplier", 0.0)  # no honeycomb inside
    _tint_mics[key] = WeakPointer(mic)
    return mic


def _scoped(weapon: UObject | None) -> bool:
    """Does aiming this gun show a full-screen scope (a sight part that is `bScoped`)?"""
    if weapon is None:
        return False
    data = weapon.DefinitionData
    return any(getattr(getattr(data, f, None), "bScoped", False) for f in ("SightPartDefinition", "BodyPartDefinition", "BarrelPartDefinition"))


def _cell_count() -> int:
    return int(world_cells.value.split()[0])


# BL3 also draws a thin pale outline along both sides of the screen, round the shield. Fractions of the screen's width and height for the left
# side, top of the screen first; the right side is its mirror. Drawn as a smooth curve through them.
FRAME = ((0.004, 0.84), (0.003, 0.74), (0.012, 0.70), (0.05, 0.675), (0.13, 0.645), (0.21, 0.615))
FRAME_RGB = (175, 218, 255)
FRAME_LINES = ((-3.0, 0.1), (-2.0, 0.3), (-1.0, 0.65), (0.0, 1.0), (1.0, 0.65), (2.0, 0.3), (3.0, 0.1))  # (pixels along the normal, how strong): a bright core and a soft glow


def _curve(points: tuple[tuple[float, float], ...], steps: int = 2) -> list[tuple[float, float]]:
    """A Catmull-Rom curve through the points, `steps` pieces between each two."""
    p = [points[0], *points, points[-1]]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1:i + 3]
        for n in range(steps):
            t = n / steps
            out.append(tuple(0.5 * (2 * p1[k] + (p2[k] - p0[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t * t + (3 * p1[k] - p0[k] - 3 * p2[k] + p3[k]) * t ** 3) for k in (0, 1)))
    out.append(points[-1])
    return out


FRAME_CURVE = _curve(FRAME)
_frame_colours: dict[tuple, list] = {}  # (strength in twentieths, rgb) -> a colour per glow line
_frame_geometry: tuple[float, float, list] = (0.0, 0.0, [])  # the screen it was laid out for, and each line segment of the outline


def _frame_segments(w: float, h: float) -> list[tuple[float, float, float, float, float, float]]:
    """(x1, y1, x2, y2, normal x, normal y) of each piece of the outline on this screen, both sides."""
    out = []
    for side in (0, 1):
        pts = [(((1 - fx) if side else fx) * w, fy * h) for fx, fy in FRAME_CURVE]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            length = math.hypot(x2 - x1, y2 - y1) or 1.0
            out.append((x1, y1, x2, y2, -(y2 - y1) / length, (x2 - x1) / length))
    return out


def _draw_frame(canvas: UObject, alpha: float, dx: float = 0.0, dy: float = 0.0, rgb: tuple[int, int, int] = FRAME_RGB) -> None:
    """The outline, as 2D lines: a bright core with a soft glow each side of it, offset along the line's normal; (dx, dy) moves it as a piece."""
    global _frame_geometry
    w, h = canvas.SizeX, canvas.SizeY
    if _frame_geometry[:2] != (w, h):
        _frame_geometry = (w, h, _frame_segments(w, h))
    level = round(alpha * 20)
    colours = _frame_colours.get((level, rgb))
    if colours is None:
        colours = _frame_colours[(level, rgb)] = [unrealsdk.make_struct("Color", R=rgb[0], G=rgb[1], B=rgb[2], A=int(230 * fade * level / 20)) for _, fade in FRAME_LINES]
    for x1, y1, x2, y2, nx, ny in _frame_geometry[2]:
        for (offset, _), colour in zip(FRAME_LINES, colours):
            canvas.Draw2DLine(x1 + dx + nx * offset, y1 + dy + ny * offset, x2 + dx + nx * offset, y2 + dy + ny * offset, colour)


# The light BL3's shield throws from the middle of the gun. It is in the world, behind the gun, like the cells: layers of the hex plate, flattened into
# a lens a little under the middle of the view, each a faint additive tint of the shield's colour, so they add up to a soft glow.
GLOW_MESH = "FX_WEP_Scopes.Meshes.Mesh_Maliwan_Sniper_Scope_Cover_Hex"
GLOW_BOUNDS = ((1.1508, 3.5602, 3.2172), (0.0039, -0.001, 0.0002))  # the plate's extent and centre offset (measured), thin along X
GLOW_LAYERS = ((0.64, 0.085, 0.08), (0.50, 0.062, 0.11), (0.37, 0.045, 0.13), (0.26, 0.032, 0.15), (0.17, 0.022, 0.17), (0.11, 0.014, 0.19))  # (width, height as shares of the view, strength)
GLOW_BELOW = 0.60  # of the view's half height, under the middle
_glow: list[WeakPointer] = []
_glow_variant = ""
_glow_mics: dict[str, list[WeakPointer]] = {}


def _glow_live() -> list[UObject]:
    global _glow
    objs = [w() for w in _glow]
    if any(o is None for o in objs):
        _glow = []
        return []
    return objs


def _glow_material(variant: str) -> list[UObject] | None:
    """One faint additive material per glow layer, in the shield's colour (made once per colour)."""
    have = [w() for w in _glow_mics.get(variant, [])]
    if have and all(m is not None for m in have):
        return have
    base = _obj("MaterialInstanceConstant", WORLD_MATERIALS["Generic Shield"])
    if base is None:
        return None
    r, g, b = VARIANTS[variant]["colour"]
    out = []
    for i, (_, _, strength) in enumerate(GLOW_LAYERS):
        mic = unrealsdk.construct_object("MaterialInstanceConstant", base.Outer, f"BL3W_ShieldGlow_{variant}_{i}")
        mic.ObjectFlags |= 0x4000  # RF_RootSet: keep it through map changes
        mic.SetParent(base)
        colour = unrealsdk.make_struct("LinearColor", R=r * strength, G=g * strength, B=b * strength, A=1.0)
        for name in ("p_GradientColor_01", "p_GradientColor_02", "p_InternalTexColor"):
            mic.SetVectorParameterValue(name, colour)
        mic.SetScalarParameterValue("p_BaseTexMultiplier", 1.0)
        mic.SetScalarParameterValue("p_InternalTexMultiplier", 0.0)
        mic.SetScalarParameterValue("p_MaskPower", 0.05)
        mic.SetScalarParameterValue("p_ColorGradientPower", 0.3)
        out.append(mic)
    _glow_mics[variant] = [WeakPointer(m) for m in out]
    return out


def _glow_hide() -> None:
    for c in _glow_live():
        c.SetHidden(True)


def _place_glow(pc: UObject, pawn: UObject, variant: str, cam, cam_pitch: int, cam_yaw: int, ease: float, show: bool) -> None:
    """The glow's layers behind the gun (`show`: false hides them)."""
    global _glow, _glow_variant
    if not show:
        _glow_hide()
        return
    if _glow and not _glow_live():
        _glow = []
    mesh = _obj("StaticMesh", GLOW_MESH)
    if mesh is None:
        return
    fresh = False
    while len(_glow) < len(GLOW_LAYERS):
        _glow.append(WeakPointer(_new_cell(pawn)))
        fresh = True
    cells = _glow_live()
    if fresh or _glow_variant != variant:
        mics = _glow_material(variant)
        if mics is None:
            return
        for c, mic in zip(cells, mics):
            c.SetStaticMesh(mesh, True)
            c.SetMaterial(0, mic)
        _glow_variant = variant
    ext, origin = GLOW_BOUNDS
    axis = "X"
    rot = _mesh_rotation(cam_pitch, cam_yaw, axis)
    axes = dict(zip("XYZ", _axes(*rot)))
    fwd, right, up = _axes(cam_pitch, cam_yaw, 0)
    wide = max("YZ", key=lambda k: abs(sum(a * b for a, b in zip(axes[k], right))))  # the plate's axis that runs across the view
    tall = "Z" if wide == "Y" else "Y"
    d = world_dist.value
    half = math.tan(math.radians(pc.GetFOVAngle()) / 2) * d
    centre = [cam.X + fwd[0] * d - up[0] * GLOW_BELOW * half, cam.Y + fwd[1] * d - up[1] * GLOW_BELOW * half, cam.Z + fwd[2] * d - up[2] * GLOW_BELOW * half]
    rot_struct = unrealsdk.make_struct("Rotator", Pitch=rot[0] & 0xFFFF, Yaw=rot[1] & 0xFFFF, Roll=rot[2] & 0xFFFF)
    grow = 0.3 + 0.7 * ease
    for c, (w_share, h_share, _) in zip(cells, GLOW_LAYERS):
        sw = w_share * half * _aspect / ext["XYZ".index(wide)] * grow
        sh = h_share * half / ext["XYZ".index(tall)] * grow
        scale = {wide: sw, tall: sh, axis: 1.0}
        off = [axes["X"][k] * origin[0] * scale["X"] + axes["Y"][k] * origin[1] * scale["Y"] + axes["Z"][k] * origin[2] * scale["Z"] for k in range(3)]
        c.SetScale3D(unrealsdk.make_struct("Vector", X=scale["X"], Y=scale["Y"], Z=scale["Z"]))
        c.SetRotation(rot_struct)
        c.SetTranslation(unrealsdk.make_struct("Vector", X=centre[0] - off[0], Y=centre[1] - off[1], Z=centre[2] - off[2]))
        c.SetHidden(False)


_anchor: tuple[float, float, float] | None = None  # where the world cells were put last frame (their centre)


def _anchor_offset(canvas: UObject, pc: UObject | None) -> tuple[float, float]:
    """Where the cells' centre of last frame sits on the screen now, from the screen's middle: they go up one frame late, so on a turn they trail the
    view; the outline and the glow follow the same way, to move with them as one piece."""
    if _anchor is None or pc is None:
        return 0.0, 0.0
    pose = _view_pose(pc)
    if pose is None:
        return 0.0, 0.0
    cam, pitch, yaw = pose
    fwd, right, up = _axes(pitch, yaw, 0)
    rel = (_anchor[0] - cam.X, _anchor[1] - cam.Y, _anchor[2] - cam.Z)
    ahead = sum(a * b for a, b in zip(rel, fwd))
    if ahead < 1.0:
        return 0.0, 0.0
    tan_v = math.tan(math.radians(pc.GetFOVAngle()) / 2)
    return (sum(a * b for a, b in zip(rel, right)) / ahead / (tan_v * _aspect)) * canvas.SizeX / 2, -(sum(a * b for a, b in zip(rel, up)) / ahead / tan_v) * canvas.SizeY / 2


def _hex_layout(count: int) -> list[tuple[int, int]]:
    """Axial (q, r) coordinates of a hexagon of hexes: 1, 7 or 19 cells."""
    radius = {1: 0, 7: 1, 19: 2}[count]
    return [
        (q, r)
        for q in range(-radius, radius + 1)
        for r in range(max(-radius, -q - radius), min(radius, -q + radius) + 1)
    ]


def _world_tick(pc: UObject | None, aiming: bool, now: float) -> None:
    """Place and animate the in-world honeycomb shield."""
    global _world_open, _world_last
    dt, _world_last = min(now - _world_last, 0.1), now
    want = (
        fx_on.value and visual_style.value == "In World" and pc is not None and pc.Pawn is not None
        and aiming and _hp > 0
    )
    breaking = now - _breaking_at < WORLD_BREAK
    if want:
        _world_open = min(1.0, _world_open + dt / WORLD_DEPLOY)
    else:
        _world_open = max(0.0, _world_open - dt / WORLD_LOWER)
    if _world_open <= 0 and not breaking:
        for c in _live():
            c.SetHidden(True)
        _glow_hide()
        return
    count = _cell_count()
    cells = _ensure_cells(pc.Pawn, count)
    if not cells:
        return
    pose = _view_pose(pc)
    if pose is None:
        return
    cam, cam_pitch, cam_yaw = pose
    if not _measure(cells[0], cam):
        for c in cells[1:]:
            c.SetHidden(True)
        _glow_hide()
        return
    ext, origin = _mesh_bounds[world_mesh.value]
    axis = _facing_axis(ext)
    flat = axis.strip("-")
    plane = sorted((k for k in "XYZ" if k != flat), key=lambda k: -ext["XYZ".index(k)])
    wide, tall = plane  # flat-top hexes: the wider in-plane axis carries the 1.5x column step
    ew, eh = ext["XYZ".index(wide)] or 1.0, ext["XYZ".index(tall)] or 1.0
    bl3 = count == 6
    scoped = bl3 and _scoped(held_weapon())  # a full-screen scope's black frame would hide the lower cells: they move in, up and shrink to sit in its window
    kx, ky, kr = SCOPE_FIT if scoped else (1.0, 1.0, 1.0)
    layout = BL3_CELLS if bl3 else _hex_layout(count)
    cluster_half = 1.0 if bl3 else ew * (1 + 1.5 * {1: 0, 7: 1, 19: 2}[count])

    ease = 1 - (1 - _world_open) ** 3
    hit = max(0.0, 1 - (now - _last_hit) / 0.1)
    size = world_size.value / (2 * cluster_half) * (0.3 + 0.7 * ease) * (1 + 0.06 * hit)
    if bl3:  # a screen pixel in world units at the shield's distance (the size option scales the lot, 70 being the default)
        unit = math.tan(math.radians(pc.GetFOVAngle()) / 2) * _aspect * world_dist.value / 960 * world_size.value / 70
        size = BL3_RADIUS * kr * unit / ew * (0.3 + 0.7 * ease) * (1 + 0.06 * hit)
    spread = 1.04
    if breaking:
        t = (now - _breaking_at) / WORLD_BREAK
        spread += 2.2 * t  # cells fly apart
    frac = _hp / _max_hp() if _max_hp() > 0 else 0.0

    rot = _mesh_rotation(cam_pitch, cam_yaw, axis)
    axes = dict(zip("XYZ", _axes(*rot)))
    fwd = _axes(cam_pitch, cam_yaw, 0)[0]
    d = world_dist.value
    centre = (cam.X + fwd[0] * d, cam.Y + fwd[1] * d, cam.Z + fwd[2] * d + (0 if bl3 else world_drop.value))
    global _anchor
    _anchor = centre if bl3 else None
    rot_struct = unrealsdk.make_struct("Rotator", Pitch=rot[0] & 0xFFFF, Yaw=rot[1] & 0xFFFF, Roll=rot[2] & 0xFFFF)
    org = [origin[0] * size, origin[1] * size, origin[2] * size]
    org_world = [axes["X"][i] * org[0] + axes["Y"][i] * org[1] + axes["Z"][i] * org[2] for i in range(3)]
    tick = int(now * 12)
    _place_glow(pc, pc.Pawn, variant_of(held_weapon()), cam, cam_pitch, cam_yaw, ease, bl3 and not scoped and not breaking)
    for i, cell in enumerate(cells):
        if i >= len(layout):
            cell.SetHidden(True)
            continue
        if bl3:
            u, v = (c * k * unit * (0.3 + 0.7 * ease) * spread for c, k in zip(layout[i], (kx, ky)))
        else:
            q, r = layout[i]
            u = 1.5 * q * ew * size * spread  # along the wide axis
            v = 2 * eh * (r + q / 2) * size * spread  # along the tall axis
        pos = [centre[k] + axes[wide][k] * u + axes[tall][k] * v - org_world[k] for k in range(3)]
        cell.SetTranslation(unrealsdk.make_struct("Vector", X=pos[0], Y=pos[1], Z=pos[2]))
        cell.SetRotation(rot_struct)
        cell.SetScale(size)
        flicker_off = 0 < frac < 0.3 and (i * 7 + tick * 3) % 5 == 0  # cells drop out at random
        gone = breaking and now - _breaking_at > WORLD_BREAK * 0.85
        cell.SetHidden(bool(flicker_off or gone))


def _view_pose(pc: UObject):
    zero_v = unrealsdk.make_struct("Vector", X=0, Y=0, Z=0)
    zero_r = unrealsdk.make_struct("Rotator", Pitch=0, Yaw=0, Roll=0)
    ret = pc.GetPlayerViewPoint(zero_v, zero_r)
    if not isinstance(ret, tuple) or len(ret) < 3:
        return None
    return ret[1], ret[2].Pitch, ret[2].Yaw


def _world_burst(pc: UObject | None) -> None:
    """Shield-hit matrix effect at the world shield (hit / break)."""
    if pc is None or not _live() or visual_style.value != "In World" or not fx_on.value:
        return
    tmpl = _obj("ParticleSystem", HIT_FX)
    pool = pc.WorldInfo.MyEmitterPool if pc.WorldInfo is not None else None
    if tmpl is None or pool is None:
        return
    pose = _view_pose(pc)
    if pose is None:
        return
    cam, pitch, yaw = pose
    fwd = _axes(pitch, yaw, 0)[0]
    d = world_dist.value
    loc = unrealsdk.make_struct("Vector", X=cam.X + fwd[0] * d, Y=cam.Y + fwd[1] * d, Z=cam.Z + fwd[2] * d + world_drop.value)
    rot = unrealsdk.make_struct("Rotator", Pitch=pitch, Yaw=yaw, Roll=0)
    psc = pool.SpawnEmitter(tmpl, loc, rot, None, None, 0, False, True)
    if psc is not None:
        psc.SetScale(world_size.value / 120)


def _world_reset() -> None:
    global _cells, _world_open, _glow
    pc = get_pc(possibly_loading=True)
    for c in _live() + _glow_live():
        c.SetHidden(True)
        if pc is not None and pc.Pawn is not None and pc.Pawn._get_address() == _comp_owner:
            pc.Pawn.DetachComponent(c)
    _cells, _world_open, _glow = [], 0.0, []


# --------------------------------------------------------------------------- the HUD meter
# Centred under the reticle (user design, v0.16.4): a straight segmented bar in shield blue that empties
# evenly from both ends toward the middle; white flash on absorbed hits, grey while broken.
# Fades in while aiming (or while the shield recovers).
# (v0.16.1-3 also showed the % in a copy of BL2's ammo counter; the user preferred the bar alone.)

SHIELD_BLUE = (90, 172, 255, 235)
HIT_WHITE = (235, 245, 255, 255)
GREY = (140, 146, 158, 200)
FADE_IN, FADE_OUT = 0.12, 0.35
HUD_HEIGHT = 720.0  # BL2's HUD stage height; the meter is laid out in those units like the rest of the HUD

meter_height = SliderOption("Shield Meter Height", 90, 10, 160, 2, True, description="How far below the reticle the shield meter sits (HUD units, 720-high HUD).")
show_percent = BoolOption("Shield Percent", True, description="Show the shield's % under the meter, in the HUD font.")
meter_style = DropdownOption("Shield Meter Style", "BL2 HUD", ["Classic", "BL2 HUD"], description="Classic: a segmented bar with the shield % under it. BL2 HUD: a bar in the look of the game's own shield bar, with the shield points beside it.")
OPTIONS += [meter_height, show_percent, meter_style]
HUD_SHIELD = (82, 210, 232, 240)  # the game's shield bar cyan
def _hud_bar(canvas: UObject, cx: float, top: float, w: float, h: float, frac: float,
             color: tuple[int, int, int, int], backing: tuple[int, int, int, int], f: float) -> None:
    """A bar in the look of BL2's own shield bar: leaning, light on top and darker below, in a thin pale frame; it empties from the right."""
    slant = 0.45
    bands = max(4, round(h / 2))  # a row every two pixels: the lean is smooth, and the calls few
    sw = h * slant
    x = cx - w / 2
    a = color[3]
    tex = canvas.DefaultTexture

    def layer(left: float, y: float, width: float, height: float, rgba: tuple[int, int, int, int]) -> None:
        canvas.SetDrawColor(*rgba)
        row = height / bands
        for i in range(bands):
            canvas.SetPos(left + (bands - 1 - i) * row * slant, y + i * row, 0)
            canvas.DrawRect(width, row + 0.5, tex)

    layer(x - sw - 2 * f, top - 2 * f, w + 4 * f, h + 4 * f, hud.with_alpha((200, 232, 245, 230), a / 255))
    layer(x - sw - 0.6 * f, top - 0.6 * f, w + 1.2 * f, h + 1.2 * f, backing)
    band = h / bands
    for i in range(bands):
        t = i / (bands - 1)
        tone = hud.lerp_color(hud.lerp_color(color[:3] + (a,), (255, 255, 255, a), 0.3), hud.lerp_color(color[:3] + (a,), (0, 0, 0, a), 0.35), t)
        canvas.SetDrawColor(*(int(c) for c in tone))
        canvas.SetPos(x - sw + (bands - 1 - i) * band * slant, top + i * band, 0)
        canvas.DrawRect(w * max(0.0, min(1.0, frac)), band + 0.5, tex)


# % label (v0.21.0, user request): BL2 HUD text in the HUD movie (hud.OutlinedLabel), centred under the
# bar. The HUD stage is 1280x720 with the reticle at its centre.
STAGE_CX, STAGE_CY = 640.0, 360.0
PERCENT_SIZE = 28  # v0.21.1: user asked for double the first 14
HUD_NUMBER_SIZE = 22  # the shield points, in the BL2 HUD style
_label = hud.OutlinedLabel("bl3_shieldpct", width=180.0, height=44.0)
_label_checked = -1e9

_fade = 0.0
_last_draw = game_now()


@hook("WillowGame.WillowGameViewportClient:PostRender")
def draw_shield(_obj: UObject, args, *_: object) -> None:
    global _fade, _last_draw, _aspect
    now = game_now()
    dt, _last_draw = min(now - _last_draw, 0.1), now
    weapon = held_weapon()
    hyperion = weapon is not None and maker_of(weapon) == "Hyperion"
    canvas = args.Canvas
    _aspect = canvas.SizeX / canvas.SizeY
    if visual_style.value == "On Screen" and hyperion:
        _draw_panel(canvas, now, dt, _aiming(weapon))
    global _world_error_logged
    if fx_on.value and visual_style.value == "In World" and _world_open > 0 and _cell_count() == 6 and not _scoped(weapon):  # (a scope's frame takes the outline's place)
        pc = get_pc(possibly_loading=True)
        dx, dy = _anchor_offset(canvas, pc)
        ease = 1 - (1 - _world_open) ** 3
        line = VARIANTS[variant_of(weapon)]["line"]
        _draw_frame(canvas, ease, dx, dy, line)
    try:
        _world_tick(get_pc(possibly_loading=True), hyperion and _aiming(weapon), now)
    except Exception as e:  # noqa: BLE001 - never let the world shield take the meter down
        if not _world_error_logged:
            _world_error_logged = True
            log(f"Hyperion world shield error: {e!r}")

    cap = _max_hp()
    if not hyperion or cap <= 0:
        _fade = 0.0
        _percent(None, 0.0)
        return
    show = _aiming(weapon) or _hp < cap
    _fade = min(1.0, _fade + dt / FADE_IN) if show else max(0.0, _fade - dt / FADE_OUT)
    if _fade <= 0:
        _percent(None, 0.0)
        return

    frac = max(0.0, _hp / cap)
    broken = _hp <= 0 or (_broken_at >= _last_hit and now - _broken_at < BROKEN_DELAY)
    hit_flash = now - _last_hit < 0.12 and not broken
    f = canvas.SizeY / HUD_HEIGHT
    offset = max(10.0, min(HUD_HEIGHT / 2 - 30, float(meter_height.value)))
    if meter_style.value == "BL2 HUD":
        color = HIT_WHITE if hit_flash else (GREY if broken else VARIANTS[variant_of(weapon)]["ui"] + (240,))
        w = 190 * f
        _hud_bar(canvas, canvas.SizeX / 2, canvas.SizeY / 2 + offset * f, w, 12 * f, frac, hud.with_alpha(color, _fade), hud.with_alpha((6, 8, 14, 175), _fade), f)
        _percent(f"{_hp:.0f}", _fade, offset, "#FFFFFF" if not broken else "#8C92A0", dx=-95 + 26, size=HUD_NUMBER_SIZE, lift=-26)
        return
    color = HIT_WHITE if hit_flash else (GREY if broken else SHIELD_BLUE)
    hud.symmetric_bar(
        canvas, canvas.SizeX / 2, canvas.SizeY / 2 + offset * f, 150 * f, 6 * f, frac, hud.with_alpha(color, _fade),
        segments=20, gap=1.5 * f,
        empty=hud.with_alpha((200, 219, 252, 30), _fade), backing=hud.with_alpha((6, 8, 14, 165), _fade),
    )
    _percent(f"{frac * 100:.0f}%", _fade, offset, "#FFFFFF" if not broken else "#8C92A0")


def _percent(text: str | None, alpha: float, offset: float = 0.0, color: str = "#FFFFFF", dx: float = 0.0, size: int = PERCENT_SIZE, lift: float = 6.0) -> None:
    """The shield % in BL2's HUD font, centred just under the meter (dx: HUD units to the right of the middle; lift: below the meter)."""
    global _label_checked
    pc = get_pc(possibly_loading=True)
    movie = pc.myHUD.HUDMovie if pc is not None and pc.myHUD is not None else None
    if movie is None:
        return
    now = game_now()
    if text is None or not show_percent.value:
        if movie.GetVariableObject(_label.box) is not None:
            _label.set_alpha(movie, 0.0)
        return
    if now - _label_checked > 0.5:
        _label_checked = now
        if not _label.ensure(movie):
            return
    _label.place_centered(movie, STAGE_CX + dx, STAGE_CY + offset + lift)
    _label.set_text(movie, text, size=size, color=color)
    _label.set_alpha(movie, alpha)


def _meter_off() -> None:
    """Kept for reset(): the meter has no HUD-movie parts any more."""


HOOKS = [absorb, regen, draw_shield]
