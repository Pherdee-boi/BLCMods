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
from .core import held_weapon, log, maker_of, now as game_now

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
}
WORLD_MESHES = {
    "Hex Plate": "FX_WEP_Scopes.Meshes.Mesh_Maliwan_Sniper_Scope_Cover_Hex",
    "Flat Plane": "FX_Shared_Mesh.Meshes.Planes.DecalPlane_LiquidDev",
}
FACINGS = ["Auto", "X", "-X", "Y", "-Y", "Z", "-Z"]
visual_style = DropdownOption("Shield Visual Style", "In World", ["In World", "On Screen"], description="In World: a real shield object in front of you (your gun draws over it). On Screen: a HUD panel.")
world_material = DropdownOption("Shield Material", "Hex Hit", list(WORLD_MATERIALS), description="Which BL2 shield material the in-world shield uses.")
world_mesh = DropdownOption("Shield Mesh", "Hex Plate", list(WORLD_MESHES), description="Shape of the in-world shield.")
world_facing = DropdownOption("Shield Mesh Facing", "Auto", FACINGS, description="Change if the shield is seen edge-on or from behind.")
world_size = SliderOption("World Shield Size", 70, 10, 150, 1, True, description="Width of the in-world shield (Unreal units).")
world_dist = SliderOption("World Shield Distance", 75, 20, 200, 1, True, description="Distance in front of the camera (Unreal units).")
world_drop = SliderOption("World Shield Height", -10, -40, 20, 1, True, description="Height relative to your eyes (Unreal units).")
world_tint = DropdownOption("Shield Tint", "Blue", ["Light Blue", "Blue", "White"], description="Colour of the in-world Hex Hit shield.")
TINTS = {"Light Blue": (0.55, 0.8, 1.0), "Blue": (0.3, 0.6, 1.0)}
world_cells = DropdownOption("Shield Cells", "19", ["1", "7", "19"], description="Build the in-world shield from this many hex cells (more = sharper).")
OPTIONS = [
    shield_pct, fx_on, visual_style, world_material, world_tint, world_mesh, world_cells, world_facing, world_size, world_dist, world_drop,
    panel_size, panel_height, panel_opacity,
]

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
    global _panel_open, _breaking_at
    _panel_open, _breaking_at = 0.0, -1e9
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
    want_mat = f"{world_material.value}|{world_tint.value}"
    if _comp_mat != want_mat:
        mat = None
        for cls in ("MaterialInterface", "Material", "MaterialInstanceConstant"):
            mat = _obj(cls, WORLD_MATERIALS[world_material.value])
            if mat is not None:
                break
        if mat is not None and world_tint.value in TINTS and mat.Class.Name == "Material":
            mat = _tinted(mat, TINTS[world_tint.value])
        if mat is not None:
            for c in _live():
                c.SetMaterial(0, mat)
            log(f"Hyperion world shield: material {world_material.value} ({world_tint.value})")
        _comp_mat = want_mat
    return _live()


_tint_mics: dict[tuple, WeakPointer] = {}


def _tinted(parent: UObject, rgb: tuple[float, float, float]) -> UObject:
    """A MaterialInstanceConstant of `parent` with its colour parameter set. Material_ShieldHitHex2 has a
    single VectorParameter (default white) whose ParameterName is blank in the cooked data, i.e. 'None'."""
    key = (parent._get_address(), rgb)
    mic = _tint_mics[key]() if key in _tint_mics else None
    if mic is not None:
        return mic
    mic = unrealsdk.construct_object("MaterialInstanceConstant", parent.Outer, f"BL3W_ShieldTint_{len(_tint_mics)}")
    mic.ObjectFlags |= 0x4000  # RF_RootSet: keep it through map changes
    mic.SetParent(parent)
    colour = unrealsdk.make_struct("LinearColor", R=rgb[0], G=rgb[1], B=rgb[2], A=1.0)
    for name in ("None", "Color", "Colour", "Tint"):
        mic.SetVectorParameterValue(name, colour)
    _tint_mics[key] = WeakPointer(mic)
    log(f"Hyperion world shield: tint {rgb} on {parent.Name}")
    return mic


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
        return
    count = int(world_cells.value)
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
        return
    ext, origin = _mesh_bounds[world_mesh.value]
    axis = _facing_axis(ext)
    flat = axis.strip("-")
    plane = sorted((k for k in "XYZ" if k != flat), key=lambda k: -ext["XYZ".index(k)])
    wide, tall = plane  # flat-top hexes: the wider in-plane axis carries the 1.5x column step
    ew, eh = ext["XYZ".index(wide)] or 1.0, ext["XYZ".index(tall)] or 1.0
    layout = _hex_layout(count)
    rings = {1: 0, 7: 1, 19: 2}[count]
    cluster_half = ew * (1 + 1.5 * rings)

    ease = 1 - (1 - _world_open) ** 3
    hit = max(0.0, 1 - (now - _last_hit) / 0.1)
    size = world_size.value / (2 * cluster_half) * (0.3 + 0.7 * ease) * (1 + 0.06 * hit)
    spread = 1.04
    if breaking:
        t = (now - _breaking_at) / WORLD_BREAK
        spread += 2.2 * t  # cells fly apart
    frac = _hp / _max_hp() if _max_hp() > 0 else 0.0

    rot = _mesh_rotation(cam_pitch, cam_yaw, axis)
    axes = dict(zip("XYZ", _axes(*rot)))
    fwd = _axes(cam_pitch, cam_yaw, 0)[0]
    d = world_dist.value
    centre = (cam.X + fwd[0] * d, cam.Y + fwd[1] * d, cam.Z + fwd[2] * d + world_drop.value)
    rot_struct = unrealsdk.make_struct("Rotator", Pitch=rot[0] & 0xFFFF, Yaw=rot[1] & 0xFFFF, Roll=rot[2] & 0xFFFF)
    org = [origin[0] * size, origin[1] * size, origin[2] * size]
    org_world = [axes["X"][i] * org[0] + axes["Y"][i] * org[1] + axes["Z"][i] * org[2] for i in range(3)]
    tick = int(now * 12)
    for i, cell in enumerate(cells):
        if i >= len(layout):
            cell.SetHidden(True)
            continue
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
    global _cells, _world_open
    pc = get_pc(possibly_loading=True)
    for c in _live():
        c.SetHidden(True)
        if pc is not None and pc.Pawn is not None and pc.Pawn._get_address() == _comp_owner:
            pc.Pawn.DetachComponent(c)
    _cells, _world_open = [], 0.0


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
OPTIONS += [meter_height, show_percent]
# % label (v0.21.0, user request): BL2 HUD text in the HUD movie (hud.OutlinedLabel), centred under the
# bar. The HUD stage is 1280x720 with the reticle at its centre.
STAGE_CX, STAGE_CY = 640.0, 360.0
PERCENT_SIZE = 28  # v0.21.1: user asked for double the first 14
_label = hud.OutlinedLabel("bl3_shieldpct", width=180.0, height=44.0)
_label_checked = -1e9

_fade = 0.0
_last_draw = game_now()


@hook("WillowGame.WillowGameViewportClient:PostRender")
def draw_shield(_obj: UObject, args, *_: object) -> None:
    global _fade, _last_draw
    now = game_now()
    dt, _last_draw = min(now - _last_draw, 0.1), now
    weapon = held_weapon()
    hyperion = weapon is not None and maker_of(weapon) == "Hyperion"
    canvas = args.Canvas
    if visual_style.value == "On Screen" and hyperion:
        _draw_panel(canvas, now, dt, _aiming(weapon))
    global _world_error_logged
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
    color = HIT_WHITE if hit_flash else (GREY if broken else SHIELD_BLUE)
    hud.symmetric_bar(
        canvas, canvas.SizeX / 2, canvas.SizeY / 2 + offset * f, 150 * f, 6 * f, frac, hud.with_alpha(color, _fade),
        segments=20, gap=1.5 * f,
        empty=hud.with_alpha((200, 219, 252, 30), _fade), backing=hud.with_alpha((6, 8, 14, 165), _fade),
    )
    _percent(f"{frac * 100:.0f}%", _fade, offset, "#FFFFFF" if not broken else "#8C92A0")


def _percent(text: str | None, alpha: float, offset: float = 0.0, color: str = "#FFFFFF") -> None:
    """The shield % in BL2's HUD font, centred just under the meter."""
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
    _label.place_centered(movie, STAGE_CX, STAGE_CY + offset + 6)
    _label.set_text(movie, text, size=PERCENT_SIZE, color=color)
    _label.set_alpha(movie, alpha)


def _meter_off() -> None:
    """Kept for reset(): the meter has no HUD-movie parts any more."""


HOOKS = [absorb, regen, draw_shield]
