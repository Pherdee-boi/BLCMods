"""The particle effects' materials in BL2 (RESEARCH.md §7, "Her particles"): one library, one compile queue.

The builds keep TPS's particle systems: the Baroness's in her streaming package, TPS's ice effects in
Crocus_IceFX_SF. Systems, emitters, LOD levels and modules are tagged properties in Engine classes BL2 has, so they
load as they are. Their materials can't load. TPS's own are compiled for TPS's renderer; the shared effects' materials
are copies BL2 can't draw either, or imports BL2 only has in some maps. So every emitter gets its material here, by
the name of the TPS material it had. The materials are compiled at runtime (shaders.py) and drawn from the textures
the builds carry; the looks whose TPS textures they don't carry are procedural.

What is here, and what the features bring:
- `LOOKS`, the library: TPS material -> how it is drawn. TPS's shared materials are listed here, whichever feature
  uses them (the ice effects and her skills share most); a feature adds the materials of its own package
  (baroness/emitters.py).
- `EMITTERS`, filled by the features: particle system -> the TPS material of each of its emitters, in order (read
  with tools/fxscan.py). Hers: baroness/emitters.py. TPS's frozen-enemy effects and cryo explosions:
  cryo/frozenfx.py.
- `EXTRA_SPECS`: other modules' runtime materials, compiled in the same queue (the frost layer, cryo/frostlayer.py).

Where TPS's own compiled shader has been ported (tpsports.py), the emitter draws with it: a few shared materials by
blend mode (`PORT_SPECS`, by tpsports.GROUPS), each TPS material an instance with its own textures and constants.
That needs every texture of the port loaded; until then (and for the materials not ported yet) the first-pass looks
stand in. Compiling is the cost: a sprite material that also draws flipbooks (SubUV) compiles 48 shaders, about 2.4 s.
So the first-pass sprites share two materials too, one additive and one translucent, each look an instance of them
(`Look`, its textures and scalars). They compile one per engine tick at the main menu; until an emitter's material is
ready, the emitter stays off, never drawn with BL2's default. A material whose textures aren't loaded can't build and
costs no turn: its emitters stay off (one such, waiting its turn forever, once kept every material after it from
compiling: TPS's icicles, until 0.3.1). So every texture any of them uses is rooted at setup (`keep_textures`), before
the garbage collector can take one nothing else references.

Two things a look can ask for. `choose`: the material is picked each time materials are assigned (one that has a
stand-in until a texture is loaded). `language_art`: an effect that carries one emitter per language (TPS's language
picks one; nothing in the data says how) names them by a prefix (`LANGUAGE_EMITTERS`) and a language suffix; only the
emitter for BL2's language is kept on, drawn with that language's art.

The ice explosions share some materials with BL2's own explosions, which their package imports from BL2 (`OWN`).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import unrealsdk
from mods_base import get_pc, hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

from . import maps, shaders, tpsports

SNOW_DUST = tpsports.carried("FX_ENV_Weather.Textures.Snow_Dust_Dif")
SPRITE_USAGE = ("bUsedWithParticleSprites", "bUsedWithParticleSubUV")
MESH_USAGE = ("bUsedWithParticleSystem",)

_COMMON = """float4 vc = Parameters.VertexColor;
float2 uv0 = Parameters.TexCoords[0];
float2 uv1 = Parameters.TexCoords[1];
float mixed = Parameters.TexCoords[2].x;
float2 cells = max(float2(CellsX, CellsY), 1.0);
float2 cell = floor(uv0 * cells);
float2 p = frac(uv0 * cells) * 2.0 - 1.0;
float2 gx = ddx(uv0) * cells, gy = ddy(uv0) * cells;
float r = length(p);
float seed = frac(sin(dot(cell, float2(12.9898, 78.233))) * 43758.5453);
"""
_PARAMS = dict(scalars=(("Look", 0.0), ("CellsX", 1.0), ("CellsY", 1.0), ("Boost", 1.0), ("Fade", 0.0)),
               vectors=(("Tint", (1.0, 1.0, 1.0, 1.0)),), usage=SPRITE_USAGE, coordinates=3, two_sided=True,
               scene_depth=True)

# Additive sprites. Looks: 0 textured frames, 1 six-armed ice crystal, 2 soft flash, 3 cross flare, 4 ring,
# 5 spikes, 6 soft orb.
SPRITE_ADD = shaders.Shader(
    name="SpriteAdd", blend=shaders.ADDITIVE,
    textures=(("Tex0", tpsports.carried("FX_Shared_Dirt.Textures.Tex_Dustmotes2")),),
    code=_COMMON + """float3 c = 0;
if (Look < 0.5) {
    c = lerp(tex2D(TEX(0), uv0), tex2D(TEX(0), uv1), mixed).rgb;
} else if (Look < 1.5) {
    float a = atan2(p.y, p.x) + seed * 6.2832;
    c = saturate((pow(abs(cos(a * 3.0)), 16.0) * 1.4 + exp(-r * r * 9.0) * 0.8) * saturate(1.0 - r)).xxx;
} else if (Look < 2.5) {
    c = (exp(-r * r * 7.0) + 0.3 * exp(-r * r * 2.0)).xxx;
} else if (Look < 3.5) {
    float streak = pow(saturate(1.0 - abs(p.y) * 8.0), 3.0) + pow(saturate(1.0 - abs(p.x) * 8.0), 3.0);
    c = (exp(-r * r * 10.0) + streak * saturate(1.0 - r) * 0.6).xxx;
} else if (Look < 4.5) {
    c = (exp(-pow((r - 0.75) / 0.09, 2.0)) * step(r, 1.0)).xxx;
} else if (Look < 5.5) {
    c = (pow(abs(cos(atan2(p.y, p.x) * 4.0)), 40.0) * saturate(1.0 - r) + exp(-r * r * 12.0) * 0.5).xxx;
} else {
    c = (exp(-r * r * 4.0) * saturate(1.0 - r)).xxx;
}
float3 rgb = c * Tint.rgb * vc.rgb * vc.a * Boost;
if (Fade > 0.0) rgb *= DepthBiasedAlpha(Parameters, 1.0, 0.0, Fade);
return float4(rgb, 1.0);
""",
    **_PARAMS,
)

# Translucent sprites. Looks: 0 a soft puff (one of TEX0's 2x2 puffs, whatever the emitter's own sheet), 1 textured
# frames (colour and opacity from TEX0), 2 Whiteout's frost splats (TEX0's frame dissolved by TEX1 as it fades),
# 3 frames with colour in TEX0 and opacity in TEX1, 4 an ice chunk, 5 a darkening flash (TPS's modulate flashes).
# Looks 0 and 2 read a texture at frac() of the sprite's coordinate, which jumps at the sprite's edge: left to pick
# its own mip there, the hardware took the jump for a huge step and read the smallest mip (the whole sheet's
# average), a dotted line around every puff, plain against a dark map (the user, 2026-10-03). They read with the
# coordinate's own slope (`gx`, `gy`), which has no jump; and a puff fades out over the last eighth before its
# cell's border, where the sheet's neighbouring puff (or its far edge, wrapped) would still draw a faint line.
# Without TPS's copies of the two textures (and BL2's own where no map has them) the shader would not build and radiation's gas, bubbles and smoke, which are
# looks of it and bring their own Tex0 (look 1 never reads Tex1), would stay off: a plain white square stands in.
WHITE = "|EngineResources.WhiteSquareTexture"
SPRITE_TRAN = shaders.Shader(
    name="SpriteTran", blend=shaders.TRANSLUCENT,
    textures=(("Tex0", SNOW_DUST + WHITE), ("Tex1", tpsports.carried("FX_ENV_Weather.Textures.Snow_Dissipate_Mask") + WHITE)),
    code=_COMMON + """float3 rgb = vc.rgb * Tint.rgb;
float a = 0;
if (Look < 0.5) {
    a = tex2Dgrad(TEX(0), (frac(uv0 * cells) + fmod(cell, 2.0)) * 0.5, gx * 0.5, gy * 0.5).r
      * saturate((1.0 - max(abs(p.x), abs(p.y))) * 8.0);
} else if (Look < 1.5) {
    float4 t = lerp(tex2D(TEX(0), uv0), tex2D(TEX(0), uv1), mixed);
    rgb *= t.rgb;
    a = max(t.r, max(t.g, t.b));
} else if (Look < 2.5) {
    float m = tex2Dgrad(TEX(1), frac(uv0 * cells), gx, gy).r;
    a = tex2D(TEX(0), uv0).r * saturate((m - (1.0 - saturate(vc.a / 0.35))) * 4.0) * 0.85;
    vc.a = 1.0;
} else if (Look < 3.5) {
    rgb *= lerp(tex2D(TEX(0), uv0), tex2D(TEX(0), uv1), mixed).rgb;
    a = lerp(tex2D(TEX(1), uv0).r, tex2D(TEX(1), uv1).r, mixed);
} else if (Look < 4.5) {
    float s = sin(seed * 6.2832);
    float k = cos(seed * 6.2832);
    float2 q = float2(p.x * k - p.y * s, p.x * s + p.y * k);
    float d = abs(q.x) * (1.4 + seed) + abs(q.y);
    a = saturate((0.9 - d) * 12.0);
    rgb *= float3(0.75, 0.9, 1.0) * (q.x > 0.0 ? 1.0 : 0.65) + pow(saturate(1.0 - d), 6.0) * 0.6;
} else {
    rgb = 0;
    a = saturate(1.0 - r);
    a *= a;
}
a *= vc.a * Boost;
if (Fade > 0.0) a = DepthBiasedAlpha(Parameters, a, 0.0, Fade);
return float4(rgb, saturate(a));
""",
    **_PARAMS,
)

# TPS's Mat_IceChunk on the frozen-enemy effects' icicles and chunks (mesh emitters), first pass: pale ice whose rim
# (where the surface turns from the camera) brightens toward white and thickens, fading with the particle's opacity
# (TPS's colours for them are white to 1.25: they tint nothing; seen against a frozen enemy, 2026-10-03).
ICE_CHUNK = shaders.Shader(
    name="IceChunkMesh", blend=shaders.TRANSLUCENT, mesh_color=True, usage=MESH_USAGE,
    code="""float facing = saturate(abs(normalize(Parameters.TangentCameraVector).z));
float rim = (1.0 - facing) * (1.0 - facing);
float3 ice = lerp(float3(0.62, 0.86, 1.0), float3(1.0, 1.0, 1.0), rim);
return float4(min(1.25 * ice, 4.0), saturate(0.55 + 0.45 * rim) * MeshColor.a);
""",
)


# Magic Bullet's nova rings, TPS's Mat_Distortion_Multiring_Trans ported: nothing in the base pass; the ring texture's
# red and green, times the particle's alpha, refract what's behind (the distortion pass).
DISTORT_RINGS = shaders.Shader(
    name="DistortRingsTPS", blend=shaders.TRANSLUCENT, usage=SPRITE_USAGE,
    textures=(("Rings", "FX_CREA_Skag.Textures.Tex_Distortionrings"),),
    code="return float4(0.0, 0.0, 0.0, 0.0);\n",
    distortion="return float4(tex2D(TEX(0), Parameters.TexCoords[0]).rg * Parameters.VertexColor.a, 0.0, 0.0);\n",
)


# TPS's own sprite materials, ported from their compiled shaders (tpsports.py): three shared materials by blend mode,
# each TPS material an instance of one. Their textures' defaults are engine textures every map has; each instance
# sets its own.
_PORT_DEFAULT_TEXTURE = "EngineResources.WhiteSquareTexture"
_PORT_COMMON = dict(textures=tuple((f"Tex{i}", _PORT_DEFAULT_TEXTURE) for i in range(4)),
                    vectors=(("P0", (0.0, 0.0, 0.0, 0.0)), ("P1", (0.0, 0.0, 0.0, 0.0)),
                             ("SkyOverride", (1.0, 1.0, 1.0, 0.0))),
                    scalars=(("Look", 0.0),), time=True, coordinates=3, two_sided=True, scene_depth=True)
_BLENDS = {"additive": shaders.ADDITIVE, "translucent": shaders.TRANSLUCENT, "modulate": shaders.MODULATE}
PORT_SPECS = {group: shaders.Shader(name=f"TPS{group.capitalize()}", blend=_BLENDS[blend], dynamic=dynamic,
                                    code=tpsports.group_code(group), usage=SPRITE_USAGE, **_PORT_COMMON)
              for group, (blend, dynamic) in tpsports.GROUPS.items()}


@dataclass(frozen=True)
class Look:
    """How one TPS material is drawn: a material, and for the sprite materials an instance's values."""

    shader: shaders.Shader
    look: float = 0.0
    textures: dict[str, str] = field(default_factory=dict)
    boost: float = 1.0
    fade: float = 0.0
    tint: tuple[float, float, float] = (1.0, 1.0, 1.0)
    choose: Callable[[], shaders.Shader] | None = None      # picks the material at each apply (see the module notes)
    language_art: tuple[str, str] | None = None             # (texture parameter, texture path with {} for the language)


def add(look: float, boost: float = 1.0, **kw: Any) -> Look:
    """A look of the additive first-pass sprite material."""
    return Look(SPRITE_ADD, look, boost=boost, **kw)


def tran(look: float, boost: float = 1.0, **kw: Any) -> Look:
    """A look of the translucent first-pass sprite material."""
    return Look(SPRITE_TRAN, look, boost=boost, **kw)


ICY = (0.85, 0.95, 1.1)
# TPS material -> its look here (None: the emitter stays off). TPS's shared materials; a feature adds its package's own.
LOOKS: dict[str, Look | None] = {
    # snow and frost
    "Mat_IceP_Snow": tran(0, textures={"Tex0": SNOW_DUST}, fade=96.0, tint=(0.95, 0.98, 1.05)),
    "Mat_IceP_Snow_SubUV2x2_01": tran(1, textures={"Tex0": SNOW_DUST}, fade=96.0, tint=(1.0, 1.1, 1.2)),
    "Mati_Snow_Flakes": tran(1, textures={"Tex0": tpsports.carried("FX_ENV_Weather.Textures.Snow_Flakes_Dif")}),
    "Mat_Snow_Screen_Dissipate": tran(2, textures={"Tex0": tpsports.carried("FX_ENV_Weather.Textures.Snow_FlakesScreen_Dif"),
                                                   "Tex1": tpsports.carried("FX_ENV_Weather.Textures.Snow_Dissipate_Mask")},
                                      tint=(0.9, 0.95, 1.0)),
    "Mat_GreySmoke": tran(0, textures={"Tex0": SNOW_DUST}),
    "Mat_CloudsA": tran(3, textures={"Tex0": tpsports.carried("FX_ENV_VOG.Textures.CloudsA_Dif2"), "Tex1": tpsports.carried("FX_ENV_VOG.Textures.CloudsA_Alpha")},
                        fade=128.0),
    "Mat_IceChunk2": tran(4),
    # TPS's frozen-enemy effects (frozenfx.py)
    "Mat_IceChunk": Look(ICE_CHUNK),
    "Mat_SimpleSnow": tran(0, textures={"Tex0": SNOW_DUST}, fade=48.0, tint=(0.95, 0.98, 1.05)),
    "Mat_IceSpecs": add(0, 3.0, textures={"Tex0": tpsports.carried("FX_Shared_Dirt.Textures.Tex_Dustmotes2")}),
    "DefaultParticle": None,          # the trace that places ground icicles, whose mesh isn't carried
    "Mati_Dirtkick": tran(0, textures={"Tex0": SNOW_DUST}, fade=32.0, tint=(0.45, 0.4, 0.35)),   # TPS's dirt clods
    # flashes, smoke and rings, drawn procedurally where they aren't ported
    "Mat_Emp_Trans": tran(0, textures={"Tex0": SNOW_DUST}, fade=64.0, tint=ICY),
    "Mat_AcidSprayGoop": tran(0, textures={"Tex0": SNOW_DUST}, fade=64.0, tint=ICY),
    "Mat_ExplosionFlashModulate": tran(5, 0.5),
    # the cryo area-effect grenade's sprays (TPS's water materials, which BL2 lacks): first pass, an icy mist
    "Mat_WaterSpringDistorted": tran(0, textures={"Tex0": SNOW_DUST}, fade=48.0, tint=ICY),
    "Mat_Water_INST02": tran(0, textures={"Tex0": SNOW_DUST}, fade=48.0, tint=(0.95, 0.98, 1.05)),
    "Mat_SirenGlowMOD": tran(5, 0.4),
    "Mati_Explosive_Spikes": add(5, 1.5),
    "Mat_Ring_MH_NonMesh": add(4, 1.5),
    "Mat_Distortion_Multiring_Trans": Look(DISTORT_RINGS),   # TPS's: a refraction
    "Mat_Lens_Flare_PD": add(2, 1.5),
    "Mat_Hologram_Flare": add(3, 1.5),
    "Mat_Explosion_Flash_Add_Procedural": add(2, 1.5),
    "Mat_Assassin_Dash_Impact_Flash": add(2, 1.2),
    "Mat_TransfusionOrb_Suck": add(6, 1.2),
    "Mat_Distortion_Wash": None,      # a refraction BL2's translucency can't do here
}
OWN = "BL2's own"                     # an emitter whose material is BL2's own, imported: drawn as it is
# particle system (its full path) -> the TPS material of each of its emitters, in order. The features fill it in.
EMITTERS: dict[str, tuple[str, ...]] = {}
# TPS's language suffixes (per-language emitters and art; the first is the art a look names itself), the emitter name
# prefixes they follow (a feature's), and BL2's language -> its suffix (anything else gets English)
LANGUAGE_SUFFIXES = ("Eng", "DEU", "ESN", "FRA", "ITA", "JPN", "RUS")
LANGUAGE_EMITTERS: list[str] = []
LANGUAGES = {"INT": "Eng", "DEU": "DEU", "ESN": "ESN", "ESM": "ESN", "FRA": "FRA", "ITA": "ITA", "JPN": "JPN",
             "RUS": "RUS"}
_RF_ROOTSET = 0x4000
EXTRA_SPECS: list[shaders.Shader] = []      # other modules' runtime materials, compiled in this queue

_enabled: dict[str, bool] = {}      # each LOD level's own bEnabled, as loaded (by path)
_unbuildable: set[str] = set()      # specs whose textures aren't loaded (said once)
_instances: dict[tuple, UObject] = {}
_pending = False
_state: list[str] = []


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] particles: {msg}")


def _find(cls: str, path: str) -> Any:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _root(obj: UObject) -> UObject:
    flags = int(obj.ObjectFlags)
    if not flags & _RF_ROOTSET:
        obj.ObjectFlags = flags | _RF_ROOTSET
    return obj


def language() -> str:
    try:
        code = str(get_pc().GetLanguage()).upper()
    except Exception:  # noqa: BLE001 - no controller yet, or no such function: English
        code = "INT"
    return LANGUAGES.get(code, "Eng")


def textures() -> list[str]:
    """Every texture the looks, their materials and the ports use (they must be loaded, and rooted: nothing in the
    packages references them any more, and one collected before its material compiles is gone for the session)."""
    specs = {SPRITE_ADD, SPRITE_TRAN, *EXTRA_SPECS}
    specs.update(look.shader for look in LOOKS.values() if look is not None)
    paths = {path for spec in specs for _p, path in spec.textures}
    for key, look in LOOKS.items():
        if look is not None:
            paths.update(look.textures.values())
            if look.language_art is not None:
                paths.update(look.language_art[1].format(lang) for lang in LANGUAGE_SUFFIXES[1:])
        resolved = tpsports.resolve(key)
        if resolved is not None:
            paths.update(resolved[1])
    return sorted(paths)


def keep_textures() -> list[str]:
    kept = []
    for path in textures():
        texture = shaders.find_texture(path)
        if texture is not None:
            _root(texture)
            kept.append(texture.Name)
    return kept


def _missing(spec: shaders.Shader) -> list[str]:
    """The spec's textures that aren't loaded: then it can't build (said once)."""
    missing = [path for _p, path in spec.textures if shaders.find_texture(path) is None]
    if missing and spec.name not in _unbuildable:
        _unbuildable.add(spec.name)
        _log(f"{spec.name}: textures not loaded {missing}: what draws with it stays off")
    return missing


def _port(key: str) -> tuple[shaders.Shader, tuple] | None:
    """TPS's own material for this emitter, ported (tpsports.py), if there is a port, its textures are loaded and its
    group's material compiled (where it didn't, the first-pass look stands in)."""
    resolved = tpsports.resolve(key)
    if resolved is None or any(shaders.find_texture(path) is None for path in resolved[1]):
        return None
    spec = PORT_SPECS[resolved[0].group]
    return None if shaders.failed(spec.name) else (spec, resolved)


def _port_material(key: str, resolved: tuple) -> tuple[UObject | None, bool]:
    port, textures, p0, p1 = resolved
    spec = PORT_SPECS[port.group]
    if shaders.material(spec) is None:
        return None, not shaders.available() or shaders.failed(spec.name)
    name = f"Mati_Port_{key}"
    if name not in _instances:
        found = [_root(shaders.find_texture(path)) for path in textures]
        if port.cap64:                    # the mip that is 64x64 and the size, of the copy found (see tpsports)
            size = float(max(found[0].SizeX, found[0].SizeY))
            p1 = (max(math.log2(size) - 6.0, 0.0), size, p1[2], p1[3])
        mic = shaders.instance(spec, name, textures={f"Tex{i}": t for i, t in enumerate(found)},
                               vectors={"P0": p0, "P1": p1}, scalars={"Look": float(tpsports.look_of(port))})
        if mic is None:
            return None, True
        _instances[name] = mic
    return _instances[name], True


def _material(key: str, look: Look, required: UObject, variant: str = "") -> tuple[UObject | None, bool]:
    """The material for an emitter of this look (built once per look and sheet), and whether that's final (no runtime
    materials, or the compile failed: the emitter stays off for good)."""
    parent = shaders.material(look.shader)
    if parent is None:
        return None, not shaders.available() or shaders.failed(look.shader.name)
    if look.shader in (SPRITE_ADD, SPRITE_TRAN):
        cells = (int(required.SubImages_Horizontal) or 1, int(required.SubImages_Vertical) or 1)
        if str(getattr(required.InterpolationMethod, "name", required.InterpolationMethod)) == "PSUVIM_None":
            cells = (1, 1)       # the sheet's cells aren't used: each sprite spans the whole texture
        name = f"Mati_{key}_{cells[0]}x{cells[1]}"
        if name not in _instances:
            mic = shaders.instance(
                look.shader, name,
                textures={p: _root(t) for p, path in look.textures.items() if (t := shaders.find_texture(path))},
                vectors={"Tint": (*look.tint, 1.0)},
                scalars={"Look": look.look, "CellsX": float(cells[0]), "CellsY": float(cells[1]), "Boost": look.boost,
                         "Fade": look.fade})
            if mic is None:
                return None, True
            _instances[name] = mic
        return _instances[name], True
    if variant:          # a language's copy (the Contract's scroll): the art of that language
        name = f"Mati_{key}_{variant}"
        if name not in _instances:
            parameter, path = look.language_art
            art = _find("Texture2D", path.format(variant))
            mic = shaders.instance(look.shader, name, textures={parameter: _root(art)} if art else {})
            if mic is None:
                return None, True
            _instances[name] = mic
        return _instances[name], True
    return parent, True


def apply(compile_budget: int = 1) -> list[str]:
    """Give the particle systems' emitters their materials. Compiles at most `compile_budget` new materials (each is a
    hitch of up to 2.4 s); the rest wait for the next call. Idempotent; the lines say what changed."""
    global _pending  # noqa: PLW0603
    keep_textures()
    lang = language()
    lines = []
    pending = False
    for system_name, keys in EMITTERS.items():
        system = _find("ParticleSystem", system_name)
        if system is None:
            continue                              # an older build: particles cut (or no ice effects package)
        _root(system)
        for index, emitter in enumerate(system.Emitters):
            if emitter is None or index >= len(keys):
                continue
            key = keys[index]
            if key == OWN:
                continue                          # BL2's own material: nothing to give it
            look = LOOKS.get(key)
            if look is not None and look.choose is not None:
                look = Look(look.choose())            # TPS's own where its pattern texture is loaded (the Diadem)
            name = str(emitter.EmitterName)
            variant = ""
            prefix = next((p for p in LANGUAGE_EMITTERS if name.startswith(p)), None)
            if prefix is not None:
                suffix = name.removeprefix(prefix)
                if suffix != lang:
                    look = None                   # another language's copy
                elif look is not None and look.language_art is not None and suffix != LANGUAGE_SUFFIXES[0]:
                    variant = suffix
            material, final = None, True
            port = _port(key) if look is not None and not variant else None
            if look is not None:
                spec = port[0] if port is not None else look.shader
                compiled = spec.name in shaders.compiled_names()
                if not compiled and _missing(spec):
                    material, final = None, True          # it can never build: no turn spent on it
                elif not compiled and compile_budget <= 0:
                    material, final = None, False
                else:
                    if not compiled:
                        compile_budget -= 1
                    if port is not None:
                        material, final = _port_material(key, port[1])
                        if material is None and shaders.failed(port[0].name):     # its group didn't compile
                            material, final = _material(key, look, emitter.LODLevels[0].RequiredModule, variant)
                    else:
                        material, final = _material(key, look, emitter.LODLevels[0].RequiredModule, variant)
            pending = pending or not final
            for lod in emitter.LODLevels:
                if lod is None:
                    continue
                path = lod._path_name()
                own = _enabled.setdefault(path, bool(lod.bEnabled))
                typedata = lod.TypeDataModule
                is_mesh = typedata is not None and typedata.Class.Name == "ParticleModuleTypeDataMesh"
                ok = material is not None and own and not (is_mesh and typedata.Mesh is None)
                if ok:
                    if lod.RequiredModule.Material != material:
                        lod.RequiredModule.Material = material
                        lines.append(f"{system_name}[{index}] {name}: {material.Name}")
                    if is_mesh:
                        typedata.bOverrideMaterial = True
                if bool(lod.bEnabled) != ok:
                    lod.bEnabled = ok
                    if not ok and final:
                        lines.append(f"{system_name}[{index}] {name}: off ({key})")
    for spec in EXTRA_SPECS:
        if (spec.name in shaders.compiled_names() or shaders.failed(spec.name) or not shaders.available()
                or _missing(spec)):
            continue
        if compile_budget <= 0:
            pending = True
            continue
        compile_budget -= 1
        if shaders.material(spec) is not None:
            lines.append(f"{spec.name}: compiled")
        elif get_pc() is None:
            pending = True                        # no controller yet: the next tick
    _pending = pending
    for line in lines:
        _log(line)
    return lines


_loading_since: float | None = None      # CRYO_LOAD_GUARD2: when a level load started (None: none is going on)
ARRIVAL_WAIT = 30.0                       # s after a map's arrival before the queue compiles again (CRYO_LOAD_GUARD4)
_quiet_until = 0.0
LOAD_WAIT = 300.0                          # s: a load that never reports its arrival stops holding the queue back after this


def _level_loading() -> bool:
    """True from the moment a load starts (a save's, a map change's) until a map has arrived. A compile during a load crashed the game."""
    global _loading_since  # noqa: PLW0603
    now = time.monotonic()
    if _loading_since is None:
        return now < _quiet_until          # CRYO_LOAD_GUARD4: just after an arrival the loader threads are still busy
    if now - _loading_since > LOAD_WAIT:
        _loading_since = None
        return now < _quiet_until
    return True


@hook("WillowGame.FrontendGFxMovie:LaunchSaveGame", Type.PRE)      # CRYO_LOAD_GUARD3
@hook("WillowGame.FrontendGFxMovie:LaunchSaveGameEx", Type.PRE)
@hook("WillowGame.FrontendGFxMovie:LaunchNewGame", Type.PRE)
@hook("Engine.PlayerController:ClientTravel", Type.PRE)
@hook("WillowGame.WillowPlayerController:ServerTeleportPlayerToStation", Type.PRE)
@hook("Engine.GameInfo:PreCommitMapChange", Type.PRE)
def _load_started(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    global _loading_since  # noqa: PLW0603
    _loading_since = time.monotonic()


@hook("WillowGame.FrontendGFxMovie:Start", Type.POST)     # CRYO_LOAD_GUARD5: the main menu is up
@hook(maps.ARRIVED, Type.POST)
@hook(maps.NEW_WORLD, Type.POST)
def _load_ended(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    global _loading_since, _quiet_until  # noqa: PLW0603
    _loading_since = None
    _quiet_until = time.monotonic() + ARRIVAL_WAIT


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def _tick(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    """While materials are still to compile: one per tick (the setup can run before there's a controller)."""
    if not _pending:
        _tick.disable()
        return
    if _level_loading():        # CRYO_LOAD_GUARD
        return
    try:
        apply(compile_budget=1)
    except Exception as ex:  # noqa: BLE001 - looks only; never break a frame
        _log(f"not applied: {type(ex).__name__}: {ex}")
        _tick.disable()
    if not _pending:
        _tick.disable()


def stop() -> None:
    """The mod is switched off in the MODS menu: no more compiling over the ticks (the tick is turned on by hand, so
    the SDK doesn't turn it off with the mod's listed hooks). What is still to compile waits for `start`."""
    _tick.disable()


def start() -> None:
    """Once the packages with particle systems are loaded (the setup), and at every map start: assign what's ready,
    and compile the rest over the next ticks."""
    apply(compile_budget=0)
    if _pending:
        _tick.enable()


@hook(maps.ARRIVED, Type.POST)
@hook(maps.NEW_WORLD, Type.POST)
def _map_started(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    try:
        start()
    except Exception as ex:  # noqa: BLE001 - looks only; never break a map start
        _log(f"not applied: {type(ex).__name__}: {ex}")


HOOKS = (_load_started, _load_ended, _map_started)   # _tick is enabled on demand by start(), not with the mod
