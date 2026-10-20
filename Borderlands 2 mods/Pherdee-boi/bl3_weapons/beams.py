"""BL3-style loot beams: a light pillar and a ground glow on dropped loot, coloured by rarity, plus a
sound when a legendary lands.

Pickups are WillowPickup actors, tracked when they land or come to rest (WillowPickup.Landed /
PickupAtRest), plus one scan per map for loot that was already lying there. Rarity is the item's own
level (WillowWeapon.StaticCalculateWeaponRarityLevel / WillowItem.StaticCalculateItemRarityLevel); its
meaning and colour come from BL2's own table, GD_Globals' GlobalsDefinition.RarityLevelColors:
1 white, 2 green, 3 blue, 4 purple, 5 and 7-10 legendary orange, 6 e-tech magenta, 500 pearlescent cyan,
501 seraph pink, 503 purple-pink, 506 rainbow. (v0.22.3 guessed 500+ = legendary; a legendary is 9.)

Pieces work like the in-world Hyperion shield: StaticMeshComponents on the player pawn with absolute
transforms, each as two back-to-back faces (the material is one-sided), with a MaterialInstanceConstant of
FX_WEP_Trails.Materials.MasterShaders.Mat_Bullet_Trail_MS: TracerColor = rarity colour, TexPrimary /
TexSecondary / TexPan = a BL2 texture (read through one channel, so the hue is TracerColor's).
  * beam: FX_WEP_Trails.Meshes.Mesh_Tracer (X 0..510, Y +-10) upright, turned to the camera each frame;
  * ground glow: BL2's own loot sparkle, fx_shared_items.Particles.Part_LootableLocator (a looping
    lens-flare sprite coloured by its RColor parameter), spawned from WorldInfo.MyEmitterPool, scaled up and
    kept at the item. (v0.22.4-5 tried a flat mesh with the trail material: that material has no
    texture-shaped opacity, so any glow texture came out as a square.)
Drop sound: Actor.PlayAkEvent(AkEvent) on the pickup the first time it lands (a BL2 UI sting; BL2 has no
legendary-drop sound of its own).
"""

import math

import unrealsdk
from mods_base import BoolOption, DropdownOption, SliderOption, get_pc, hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import UObject, WeakPointer

from .core import log

BEAM_MESH, BEAM_LENGTH, BEAM_WIDTH = "FX_WEP_Trails.Meshes.Mesh_Tracer", 510.0, 20.0
GLOW_PARTICLE = "fx_shared_items.Particles.Part_LootableLocator"
GLOW_BASE_SIZE = 3.0  # the locator's sprites start 2-3 units across
STYLES = {
    "Trail": ("FX_WEP_Trails.Materials.MasterShaders.Mat_Bullet_Trail_MS", "TracerColor"),
    "Hex": ("FX_CHAR_Shared_Shield.Materials.Material_ShieldHitHex2", "None"),
}
TEXTURES = {  # Trail style textures (all in Startup.upk, always loaded)
    "Lightning Beam": "FX_CHAR_Shared_Shield.Textures.Tex_Lighting_Beam",
    "Soft Line": "FX_Shared_Gradients.Textures.Horiz_GradientLine_Dif",
    "Digistruct": "FX_IO_Digistruct.Textures.Beam_Wavy_Dif",
    "Vertical Fade": "FX_Shared_Gradients.Textures.VerticalGradient_Dif",
    "Soft Glow": "FX_Shared_Energy.GreyPack.T_Round_Soft_GP_01",
    "Smoke Trail": None,
}
# v0.22.7: user found the beam too strong even at the old lowest setting (20% x 1.2)
TEXTURE_GAIN = {"Lightning Beam": 0.8, "Digistruct": 1.4, "Soft Line": 1.8, "Vertical Fade": 1.8, "Soft Glow": 1.6, "Smoke Trail": 2.5}
SOUNDS = {
    "Mission Reward": "Ake_UI.UI_Mission.Ak_Play_UI_Mission_Reward",
    "Co-op Ding": "Ake_UI.UI_HUD.Ak_Play_UI_Alert_CoOp_Ding",
    "Badass Rank Up": "Ake_UI.UI_BackMenu.Ak_Play_UI_Badass_Rank_Up",
    "Level Up": "Ake_UI.UI_HUD.Ak_Play_UI_Alert_Level_Up",
}
RARITY_NAMES = ["Common", "Uncommon", "Rare", "Epic", "Legendary"]
MAX_BEAMS = 40

# BL2's RarityLevelColors (sRGB) and our tier (0 common .. 4 legendary-class) per rarity name
BL2_COLOURS = {
    "common": (255, 255, 255), "uncommon": (61, 210, 11), "rare": (60, 142, 255), "epic": (168, 63, 229),
    "legendary": (255, 140, 0), "etech": (202, 0, 168), "pearl": (0, 255, 255), "seraph": (255, 154, 184),
    "gemstone": (145, 50, 200), "rainbow": (242, 255, 161),
}
TIERS = {"common": 0, "uncommon": 1, "rare": 2, "epic": 3, "etech": 3, "legendary": 4, "pearl": 4, "seraph": 4, "gemstone": 4, "rainbow": 4}

enabled = BoolOption("Loot Beams", False, description="BL3-style rarity beams on dropped loot. Off by default since 0.64.7 (they can stay behind after loot is picked up).")
min_rarity = DropdownOption("Loot Beam Min Rarity", "Rare", RARITY_NAMES, description="Lowest rarity that gets a beam.")
style = DropdownOption("Loot Beam Style", "Trail", list(STYLES), description="Look of the beam.", on_change=lambda *_: _reset_all())
texture = DropdownOption("Loot Beam Texture", "Lightning Beam", list(TEXTURES), description="Texture of the Trail-style beam.", on_change=lambda *_: _reset_all())
height = SliderOption("Loot Beam Height", 200, 100, 800, 25, True, description="Beam height (Unreal units).")
width = SliderOption("Loot Beam Width", 20, 4, 40, 1, True, description="Beam width (Unreal units).")
brightness = SliderOption("Loot Beam Brightness", 200, 5, 300, 5, True, description="Beam brightness, percent.", on_change=lambda *_: _reset_all())
glow_on = BoolOption("Loot Ground Glow", True, description="A soft rarity-coloured glow under dropped loot.")
glow_size = SliderOption("Loot Ground Glow Size", 20, 20, 200, 5, True, description="Ground glow diameter (Unreal units).")
glow_height = SliderOption("Loot Ground Glow Height", -20, -20, 20, 1, True, description="Ground glow height relative to the item's centre.")
sound_from = DropdownOption("Loot Drop Sound", "Legendary", ["Off", "Epic", "Legendary"], description="Play a sound when loot of this rarity or better lands.")
sound_kind = DropdownOption("Loot Drop Sound Effect", "Badass Rank Up", list(SOUNDS), description="Which BL2 sound plays.")
OPTIONS = [enabled, min_rarity, style, texture, height, width, brightness, glow_on, glow_size, glow_height, sound_from, sound_kind]


def _rarity_name(level: int) -> str | None:
    if 1 <= level <= 4:
        return ["common", "uncommon", "rare", "epic"][level - 1]
    if level == 5 or 7 <= level <= 10:
        return "legendary"
    return {6: "etech", 500: "pearl", 501: "seraph", 503: "gemstone", 506: "rainbow"}.get(level)


def _colour(name: str) -> tuple[float, float, float]:
    """BL2's sRGB rarity colour -> linear, brightest channel 1 (keeps the hue saturated)."""
    lin = [(c / 255) ** 2.2 for c in BL2_COLOURS[name]]
    top = max(lin) or 1.0
    return (lin[0] / top, lin[1] / top, lin[2] / top)


_tracked: dict[int, WeakPointer] = {}  # pickup address -> pickup
_parts: dict[int, list[WeakPointer]] = {}  # pickup address -> [beam face, beam face]
_glows: dict[int, WeakPointer] = {}  # pickup address -> its glow particle component
_spare: dict[str, list[WeakPointer]] = {}  # mesh path -> hidden components to reuse
_levels: dict[int, int] = {}  # pickup address -> rarity level
_sounded: set[int] = set()
_owner = 0
_mics: dict[tuple, WeakPointer] = {}
_scanned_for = 0
_seen_levels: set[int] = set()


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _material(name: str, kind: str) -> UObject | None:
    """kind: 'beam' or 'glow'."""
    key = (kind, style.value, texture.value, name, brightness.value)
    mic = _mics[key]() if key in _mics else None
    if mic is not None:
        return mic
    beam_style = style.value if kind == "beam" else "Trail"
    parent_path, colour_param = STYLES[beam_style]
    parent = _find("Material", parent_path)
    if parent is None:
        log(f"Loot beams: {parent_path} not loaded")
        return None
    mic = unrealsdk.construct_object("MaterialInstanceConstant", parent.Outer, f"BL3W_LootBeam_{len(_mics)}")
    mic.ObjectFlags |= 0x4000  # RF_RootSet: survives map changes
    mic.SetParent(parent)
    r, g, b = _colour(name)
    gain = TEXTURE_GAIN.get(texture.value, 1.0) if kind == "beam" and beam_style == "Trail" else 1.6
    k = brightness.value / 100 * gain
    mic.SetVectorParameterValue(colour_param, unrealsdk.make_struct("LinearColor", R=r * k, G=g * k, B=b * k, A=1.0))
    tex_path = TEXTURES.get(texture.value) if beam_style == "Trail" else None
    tex = _find("Texture2D", tex_path) if tex_path else None
    if tex is not None:
        for p in ("TexPrimary", "TexSecondary", "TexPan"):
            mic.SetTextureParameterValue(p, tex)
        for p, value in (("TexPrimaryTile_X", 1.0), ("TexPrimaryTile_Y", 1.0), ("TexSecondaryTile_X", 1.0),
                         ("PanTexTile_X", 1.0), ("PanTexTile_Y", 1.0), ("TexPrimaryOffset_Y", 0.0), ("TexPanOffset_Y", 0.0)):
            mic.SetScalarParameterValue(p, value)
    _mics[key] = WeakPointer(mic)
    return mic


def _component(pawn: UObject, mesh_path: str) -> UObject | None:
    pool = _spare.setdefault(mesh_path, [])
    while pool:
        comp = pool.pop()()
        if comp is not None:
            return comp
    mesh = _find("StaticMesh", mesh_path)
    if mesh is None:
        return None
    comp = unrealsdk.construct_object("StaticMeshComponent", pawn)
    comp.SetActorCollision(False, False, False)
    comp.SetTraceBlocking(False, False)
    comp.SetBlockRigidBody(False)
    comp.CastShadow = False
    pawn.AttachComponent(comp)
    comp.SetAbsolute(True, True, True)
    comp.SetStaticMesh(mesh, True)
    return comp


def _recycle(addr: int) -> None:
    for k, ref in enumerate(_parts.pop(addr, [])):
        comp = ref()
        if comp is not None:
            comp.SetHidden(True)
            _spare.setdefault(BEAM_MESH, []).append(ref)
    glow = _glows.pop(addr, None)
    psc = glow() if glow is not None else None
    if psc is not None:
        psc.DeactivateSystem()


def _reset_all() -> None:
    for addr in set(_parts) | set(_glows):
        _recycle(addr)


def track(pickup: UObject) -> None:
    if pickup is not None:
        _tracked[pickup._get_address()] = WeakPointer(pickup)


@hook("WillowGame.WillowPickup:PickupAtRest", Type.POST)
def on_rest(obj: UObject, *_: object) -> None:
    track(obj)
    _drop_sound(obj)  # dropped guns are rigid bodies: they come to rest here and never call Landed (v0.22.4)


@hook("WillowGame.WillowPickup:Landed", Type.POST)
def on_landed(obj: UObject, *_: object) -> None:
    track(obj)
    _drop_sound(obj)


def _drop_sound(pickup: UObject) -> None:
    """Once per pickup, the first time it lands (not for loot that was already lying around)."""
    addr = pickup._get_address()
    if addr in _sounded or sound_from.value == "Off" or pickup.Inventory is None:
        return
    _sounded.add(addr)
    name = _rarity_name(_rarity(pickup))
    if name is None or TIERS[name] < (4 if sound_from.value == "Legendary" else 3):
        return
    event = _find("AkEvent", SOUNDS[sound_kind.value])
    if event is None:
        log(f"Loot beams: sound {SOUNDS[sound_kind.value]} not loaded")
        return
    pickup.PlayAkEvent(event)
    log(f"Loot beams: drop sound for {name} ({sound_kind.value})")


def _drop_all() -> None:
    global _owner
    _parts.clear()
    _glows.clear()
    _levels.clear()
    _spare.clear()
    _tracked.clear()
    _owner = 0


def _rarity(pickup: UObject) -> int:
    addr = pickup._get_address()
    if addr in _levels:
        return _levels[addr]
    inv = pickup.Inventory
    level = int(pickup.InventoryRarityLevel)
    try:
        if inv.Class._inherits(unrealsdk.find_class("WillowWeapon")):
            ret = inv.StaticCalculateWeaponRarityLevel(inv.DefinitionData)
        else:
            ret = inv.StaticCalculateItemRarityLevel(inv.DefinitionData)
        level = int(ret[0] if isinstance(ret, tuple) else ret)
    except Exception as e:  # noqa: BLE001
        log(f"Loot beams: rarity of {inv.Class.Name} failed ({e!r}), using the pickup's {level}")
    if level not in _seen_levels:
        _seen_levels.add(level)
        log(f"Loot beams: rarity level {level} = {_rarity_name(level)} ({inv.Class.Name})")
    _levels[addr] = level
    return level


def _wanted(pickup: UObject) -> str | None:
    """Rarity name if this pickup should have a beam."""
    if pickup.Inventory is None or pickup.bHidden or pickup.bDeleteMe:
        return None
    name = _rarity_name(_rarity(pickup))
    if name is None or TIERS[name] < RARITY_NAMES.index(min_rarity.value):
        return None
    return name


def _build(pawn: UObject, name: str) -> list[UObject] | None:
    beam = [_component(pawn, BEAM_MESH), _component(pawn, BEAM_MESH)]
    mat = _material(name, "beam")
    if any(c is None for c in beam) or mat is None:
        return None
    for c in beam:
        c.SetMaterial(0, mat)
    return beam


def _glow(pc: UObject, addr: int, name: str, at) -> None:
    """Keep (or make) the item's ground glow at `at`."""
    ref = _glows.get(addr)
    psc = ref() if ref is not None else None
    if psc is None:
        tmpl = _find("ParticleSystem", GLOW_PARTICLE)
        pool = pc.WorldInfo.MyEmitterPool if pc.WorldInfo is not None else None
        if tmpl is None or pool is None:
            return
        psc = pool.SpawnEmitter(tmpl, at, unrealsdk.make_struct("Rotator"), None, None, 0, False, True)
        if psc is None:
            return
        r, g, b = _colour(name)
        k = brightness.value / 100 * 2.0
        psc.SetVectorParameter("RColor", unrealsdk.make_struct("Vector", X=r * k, Y=g * k, Z=b * k))
        _glows[addr] = WeakPointer(psc)
    psc.SetTranslation(at)
    psc.SetScale(glow_size.value / GLOW_BASE_SIZE)


@hook("WillowGame.WillowGameViewportClient:Tick", hook_identifier="bl3w_loot_beams")
def beams_tick(*_: object) -> None:
    global _owner, _scanned_for
    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if pawn is None or not enabled.value:
        if _parts and pawn is not None:
            _reset_all()
        return
    if _owner != pawn._get_address():
        _drop_all()  # new map / respawn: old components went with the old pawn
        _owner = pawn._get_address()
    if _scanned_for != _owner:
        _scanned_for = _owner
        for pickup in unrealsdk.find_all("WillowPickup", exact=False):
            if pickup.Inventory is not None and pickup.Physics != 2:  # not falling (PHYS_Falling)
                track(pickup)
                _sounded.add(pickup._get_address())  # already lying here: no drop sound

    cam = pc.GetPlayerViewPoint(unrealsdk.make_struct("Vector"), unrealsdk.make_struct("Rotator"))[1]
    live: list[tuple[float, int, UObject, str]] = []
    for addr, ref in list(_tracked.items()):
        pickup = ref()
        name = _wanted(pickup) if pickup is not None else None
        if name is None:
            if pickup is None or pickup.Inventory is None:
                _tracked.pop(addr, None)
                _sounded.discard(addr)
            continue
        loc = pickup.Location
        live.append(((loc.X - cam.X) ** 2 + (loc.Y - cam.Y) ** 2 + (loc.Z - cam.Z) ** 2, addr, pickup, name))
    live.sort(key=lambda t: t[0])
    keep = {addr for _d, addr, _p, _n in live[:MAX_BEAMS]}
    for addr in [a for a in set(_parts) | set(_glows) if a not in keep]:  # picked up / out of range
        _recycle(addr)
        _levels.pop(addr, None)

    beam_scale = unrealsdk.make_struct("Vector", X=height.value / BEAM_LENGTH, Y=width.value / BEAM_WIDTH, Z=1.0)
    for _d, addr, pickup, name in live[:MAX_BEAMS]:
        comps = [r() for r in _parts.get(addr, [])]
        if len(comps) != 2 or any(c is None for c in comps):
            built = _build(pawn, name)
            if built is None:
                return
            comps = built
            _parts[addr] = [WeakPointer(c) for c in comps]
            if len(_parts) == 1:
                log(f"Loot beams: built beam + glow for {name} at z {pickup.Location.Z:.0f}")
        loc = pickup.Location
        yaw = int(math.atan2(cam.Y - loc.Y, cam.X - loc.X) * 32768 / math.pi)
        for k in range(2):
            beam = comps[k]
            beam.SetTranslation(loc)
            beam.SetRotation(unrealsdk.make_struct("Rotator", Pitch=16384, Yaw=yaw + 32768 * k, Roll=0))
            beam.SetScale3D(beam_scale)
            beam.SetHidden(False)
        if glow_on.value:
            _glow(pc, addr, name, unrealsdk.make_struct("Vector", X=loc.X, Y=loc.Y, Z=loc.Z + glow_height.value))
        elif addr in _glows:
            psc = _glows.pop(addr)()
            if psc is not None:
                psc.DeactivateSystem()


def reset() -> None:
    _reset_all()


HOOKS = [on_rest, on_landed, beams_tick]
