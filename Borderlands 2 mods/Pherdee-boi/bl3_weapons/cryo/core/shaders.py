"""Runtime-compiled materials: BL2's own material compiler, run from Python (RESEARCH.md §7, "Custom shaders").

TPS's materials can't draw in BL2: they are compiled for TPS's renderer and cooked without their graphs. BL2's
always-loaded masters cover plain looks (fx.py). For her effects' own looks (a sparkle masked to the Diadem's ice,
additive particles in her colours) this builds materials at runtime with BL2's own material compiler. The shipping
exe still carries it (the HLSL translator and D3DX); a cooked build just never runs it. Three runtime values stand in
the way. Each is set for the length of one compile and put back straight after:

- **The cooked-console check.** `FMaterial::CacheShaders` only compiles when `appGetPlatformType()` isn't a console
  platform, and a cooked PC build reports WindowsConsole while a global flag is set. Cleared: plain Windows.
- **The compile workers.** `[DevOptions.Shaders] bAllowMultiThreadedShaderCompile` (on by default) hands jobs to
  `UE3ShaderCompileWorker.exe`, which BL2 doesn't ship. The shader manager's copy of the setting is turned off; the
  ini isn't touched.
- **The local shader cache.** A compile marks it dirty, and the next engine tick writes
  `My Games/Borderlands 2/WillowGame/CookedPCConsole/LocalShaderCache-PC-D3D-SM3.upk`, a file vanilla BL2 never
  creates. Its dirty flag is cleared again, so nothing reaches the disk.

The compile itself is the console's `RecompileShaders Material <name>`, so it needs a player controller; the main
menu's will do. It takes 0.3 to 2.7 s a material, nine tenths of it inside the compiler (a particle material that
carries a dynamic parameter is 48 shaders), and it was done anew at every start: half a minute of hitches. Since
0.4.19 what the compiler returns is kept between launches (shadercache.py), and a material whose shaders are all
kept takes a tenth of that.

Texture nodes are the catch. In the shipping exe every texture-sample expression's compile is either a stub or emits
the sample and then returns chunk 0 (`TextureSampleParameterNormal`, `TextureSampleParameterMeshSubUV`), so a sample
never reaches the node that uses it. It does declare and bind the texture's sampler, though
(`sampler2D PixelUE_Texture2D_<n>`: the parameter's texture, or a material instance's override). So every material
here is one Custom (HLSL) node. Texture nodes are wired to it as unused inputs, which declares their samplers, and the
HLSL samples them by name: `TEX(n)` is the n-th texture of the spec.

Addresses come from byte patterns in the running exe, each unique in the Steam build. If one is missing,
`available()` is False and callers keep their BL2-master stand-ins.
"""

from __future__ import annotations

import ctypes
import re
import struct
import time
from dataclasses import dataclass
from typing import Any

import unrealsdk
from mods_base import get_pc
from unrealsdk.unreal import UObject

from . import shadercache

PACKAGE = "GD_AureliaShaders"
_RF_ROOTSET = 0x4000

# EBlendMode
OPAQUE, MASKED, TRANSLUCENT, ADDITIVE, MODULATE = 0, 1, 2, 3, 4
_UNLIT = 2       # EMaterialLightingModel.MLM_Unlit
_FLOAT4 = 3      # ECustomMaterialOutputType.CMOT_Float4
_TRANSFORM_WORLD = 0   # EMaterialVectorCoordTransform.TRANSFORM_World

# Byte patterns in the running exe; "." is any byte, and group 1 is an absolute address.
_PATTERNS = {
    # appGetPlatformType(): cmp [cooked-console flag], 0; je; mov eax, PLATFORM_WindowsConsole; ret
    "platform": rb"\x83\x3d(....)\x00\x74.\xb8\x40\x00\x00\x00\xc3",
    # the RecompileShaders command: mov ecx, [GShaderCompilingThreadManager]; xor ebx, ebx; push ebx x3
    "manager": rb"\x8b\x0d(....)\x33\xdb\x53\x53\x53",
    # the manager's constructor: bAllowMultiThreadedShaderCompile read into this+0x70
    "multithreaded": rb"\x8d\x46\x70\x50\x68....\x89\xbe\xb4\x00\x00\x00",
    # SaveLocalShaderCache(): mov ebx, [GShaderCaches + platform*4] ... cmp [ebx+0xf8] (dirty), 0
    "caches": rb"\x8b\x1c\xb5(....)\x8d\x34\xb5\1\x85\xdb\x0f\x84....\x83\xbb\xf8\x00\x00\x00\x00",
}
_MULTITHREADED = 0x70    # in the shader compile manager
_DIRTY = 0xF8            # in a UShaderCache
_CACHE_SLOTS = 14        # the local and the reference caches, 7 platforms each
_SHADER_MAP = 92         # in an FMaterialResource: its shader map, null until compiled


@dataclass(frozen=True)
class Shader:
    """A material made of one Custom (HLSL) node.

    `code` is the body of an HLSL function returning float4: rgb is the emissive colour, and a is the opacity
    (translucent) or the mask (masked). It sees `Parameters` (`Parameters.TexCoords[0]`, `.VertexColor`,
    `.TangentCameraVector`, `.TangentReflectionVector`, `.ScreenPosition`), one float4 per vector parameter and one
    float per scalar parameter, named after them, and `Time` (seconds) if asked for. `TEX(n)` is the sampler of
    `textures[n]`.

    Particles: `Parameters.VertexColor` is a sprite's colour (colour and alpha over life). A SubUV sprite's frame is
    `TexCoords[0]`; with `coordinates=3`, `TexCoords[1]` is the next frame and `TexCoords[2].x` the blend between them
    (a plain sprite has its one UV in all three). A mesh emitter's colour is `MeshColor` (`mesh_color=True`). An
    emitter's dynamic parameter (`ParticleModuleParameterDynamic`) is `GetDynamicParameter(Parameters)` once
    `dynamic=True` (the material then also compiles the vertex factories that carry it; without them such an
    emitter can't draw with it). `SkyLight` (float3, `sky_light=True`) is the map's dominant sky light colour, which
    TPS's unlit translucent particles are tinted by.
    """

    name: str
    code: str
    textures: tuple[tuple[str, str], ...] = ()                            # (parameter, default texture's path)
    vectors: tuple[tuple[str, tuple[float, float, float, float]], ...] = ()  # (parameter, default RGBA)
    scalars: tuple[tuple[str, float], ...] = ()                           # (parameter, default)
    blend: int = OPAQUE
    two_sided: bool = False
    time: bool = False
    usage: tuple[str, ...] = ()      # vertex factories beyond static meshes, e.g. "bUsedWithParticleSprites"
    coordinates: int = 1             # texture coordinates declared (sprites: 3 gives SubUV's second frame and blend)
    mesh_color: bool = False         # a "MeshColor" float4 input: a mesh emitter's particle colour
    scene_depth: bool = False        # binds the scene depth, for DepthBiasedAlpha() (translucent and additive only)
    dynamic: bool = False            # a particle dynamic parameter node: GetDynamicParameter(Parameters) carries it
    sky_light: bool = False          # a "SkyLight" float3 input: the dominant sky light (TPS's TimeOfDayLightColor)
    world_normal: bool = False       # a "WorldNormal" float3 input: the surface's normal in world space
    world_position: bool = False     # a "WorldPos" float3 input: the pixel's position in the world
    object_bounds: bool = False      # "ObjPos" (float3) and "ObjRadius" (float): the primitive's bounds
    distortion: str = ""             # a second HLSL body (same inputs) whose .rg is the refraction (translucent only)
    edge_detection: bool = True      # Borderlands' ink outline over its edges (opaque and masked); False as on TPS's
                                     # M_FX_Diadem (bDisableEdgeDetection), which otherwise looks a third thinner


@dataclass(frozen=True)
class _Addresses:
    platform_flag: int
    manager: int        # the global holding the shader compile manager's address
    caches: int         # GShaderCaches


_addresses: _Addresses | None = None
_scanned = False
_materials: dict[str, UObject] = {}      # compiled, by spec name
_failed: set[str] = set()
_attempts = 0


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] shaders: {msg}")


def _find(cls: str, path: str) -> Any:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def find_texture(path: str) -> Any:
    """A texture by path, or by the first loaded of several ("a|b": her build's copy first, then BL2's own)."""
    for alternative in path.split("|"):
        texture = _find("Texture2D", alternative)
        if texture is not None:
            return texture
    return None


def _root(obj: UObject) -> UObject:
    flags = int(obj.ObjectFlags)
    if not flags & _RF_ROOTSET:
        obj.ObjectFlags = flags | _RF_ROOTSET
    return obj


def _package() -> UObject:
    pkg = _find("Package", PACKAGE)
    if pkg is None:
        pkg = unrealsdk.construct_object("Package", None, PACKAGE)
    return _root(pkg)


# ---------------------------------------------------------------- finding the compiler's switches
def _code_section() -> bytes:
    kernel32 = ctypes.windll.kernel32
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    base = kernel32.GetModuleHandleW(None)
    pe = base + struct.unpack("<I", ctypes.string_at(base + 0x3C, 4))[0]
    count, = struct.unpack("<H", ctypes.string_at(pe + 6, 2))
    optional_size, = struct.unpack("<H", ctypes.string_at(pe + 20, 2))
    table = pe + 24 + optional_size
    for i in range(count):
        name, size, rva = struct.unpack("<8sII", ctypes.string_at(table + 40 * i, 16))
        if name.rstrip(b"\0") == b".text":
            return ctypes.string_at(base + rva, size)
    raise LookupError("no .text section")


def _scan() -> _Addresses | None:
    code = _code_section()
    found = {}
    for key, pattern in _PATTERNS.items():
        hits = list(re.finditer(pattern, code, re.DOTALL))
        if len(hits) != 1:
            _log(f"off: pattern '{key}' found {len(hits)} times (another BL2 build?); BL2 stand-ins stay")
            return None
        found[key] = hits[0]

    def address(key: str) -> int:
        return struct.unpack("<I", found[key].group(1))[0]

    return _Addresses(address("platform"), address("manager"), address("caches"))


def available() -> bool:
    """Whether runtime materials can be compiled in this game (found once, on first use)."""
    global _addresses, _scanned  # noqa: PLW0603
    if not _scanned:
        _scanned = True
        try:
            _addresses = _scan()
        except Exception as ex:  # noqa: BLE001 - looks only; never break the mod
            _log(f"off: {type(ex).__name__}: {ex}")
            _addresses = None
    return _addresses is not None


# ---------------------------------------------------------------- compiling
def shader_map(material: UObject) -> int:
    """The material's compiled shader map (0 until compiled)."""
    resource = int(material.MaterialResources[0].Dummy)
    return ctypes.c_uint32.from_address(resource + _SHADER_MAP).value if resource else 0


def compile_material(material: UObject) -> bool:
    """Compile one material's shaders now; True when it has a shader map afterwards. Needs a player controller."""
    pc = get_pc()
    if pc is None or not available() or _addresses is None:
        return False
    manager = ctypes.c_uint32.from_address(_addresses.manager).value
    if not manager:
        return False
    flag = ctypes.c_int.from_address(_addresses.platform_flag)
    multithreaded = ctypes.c_int.from_address(manager + _MULTITHREADED)
    caches = [c for c in (ctypes.c_uint32 * _CACHE_SLOTS).from_address(_addresses.caches) if c]
    clean = [c for c in caches if not ctypes.c_int.from_address(c + _DIRTY).value]
    saved = flag.value, multithreaded.value
    try:
        flag.value = 0
        multithreaded.value = 0
        with shadercache.kept():                # shaders compiled at an earlier start come from the mod's file
            pc.ConsoleCommand(f"RecompileShaders Material {material.Name}", False)
    finally:
        flag.value, multithreaded.value = saved
        for cache in clean:     # left clean, so the engine never writes a local shader cache
            ctypes.c_int.from_address(cache + _DIRTY).value = 0
    return shader_map(material) != 0


# ---------------------------------------------------------------- building
def _node(cls: str, material: UObject, name: str) -> UObject:
    return _root(unrealsdk.construct_object(cls, material, name))


def _connect(material: UObject, prop: str, expression: UObject, channels: str) -> None:
    value = getattr(material, prop)
    value.Expression = expression
    value.Mask = 1
    value.MaskR, value.MaskG, value.MaskB, value.MaskA = (int(c in channels) for c in "rgba")
    setattr(material, prop, value)


def _build(spec: Shader) -> UObject | None:
    textures = [(param, find_texture(path)) for param, path in spec.textures]
    missing = [path for (_, path), (_, texture) in zip(spec.textures, textures, strict=True) if texture is None]
    if missing:
        _log(f"{spec.name}: textures not loaded: {missing}")
        return None
    global _attempts  # noqa: PLW0603
    name = f"M_Aurelia_{spec.name}"
    while _find("Material", f"{PACKAGE}.{name}") is not None:   # an earlier attempt's (or a reload's): never reused
        _attempts += 1
        name = f"M_Aurelia_{spec.name}_{_attempts}"
    material = _root(unrealsdk.construct_object("Material", _package(), name))
    nodes: list[UObject] = []
    inputs: list[tuple[str, UObject]] = []
    for i, (param, texture) in enumerate(textures):
        node = _node("MaterialExpressionTextureSampleParameterNormal", material, f"Texture{i}")
        node.Texture = _root(texture)
        node.ParameterName = param
        nodes.append(node)
        inputs.append((f"Sampler{i}", node))      # unused in the HLSL: wired only to declare TEX(i)
    for param, (r, g, b, a) in spec.vectors:
        node = _node("MaterialExpressionVectorParameter", material, param)
        node.ParameterName = param
        node.DefaultValue = unrealsdk.make_struct("LinearColor", R=r, G=g, B=b, A=a)
        nodes.append(node)
        inputs.append((param, node))
    for param, value in spec.scalars:
        node = _node("MaterialExpressionScalarParameter", material, param)
        node.ParameterName = param
        node.DefaultValue = value
        nodes.append(node)
        inputs.append((param, node))
    if spec.time:
        node = _node("MaterialExpressionTime", material, "Time")
        nodes.append(node)
        inputs.append(("Time", node))
    if spec.mesh_color:
        node = _node("MaterialExpressionMeshEmitterVertexColor", material, "MeshColor")
        nodes.append(node)
        inputs.append(("MeshColor", node))
    for index in range(max(spec.coordinates, 1)):    # unused: they only declare the texture coordinates (with no
        node = _node("MaterialExpressionTextureCoordinate", material, f"Coordinate{index}")   # texture node, a material
        node.CoordinateIndex = index                    # has none, and `Parameters.TexCoords` doesn't exist)
        nodes.append(node)
        inputs.append((f"Coordinate{index}", node))
    if spec.scene_depth:
        node = _node("MaterialExpressionSceneDepth", material, "SceneDepth")
        nodes.append(node)
        inputs.append(("SceneDepth", node))
    if spec.dynamic:
        node = _node("MaterialExpressionDynamicParameter", material, "Dynamic")
        nodes.append(node)
        inputs.append(("Dynamic", node))
    if spec.sky_light:
        node = _node("MaterialExpressionDominantSkyLight", material, "SkyLight")
        nodes.append(node)
        inputs.append(("SkyLight", node))
    if spec.world_normal:                   # tangent space's +Z taken to world space
        up = _node("MaterialExpressionConstant3Vector", material, "TangentUp")
        up.R, up.G, up.B = 0.0, 0.0, 1.0
        node = _node("MaterialExpressionTransform", material, "WorldNormal")
        node.Input = unrealsdk.make_struct("ExpressionInput", Expression=up)
        node.TransformType = _TRANSFORM_WORLD
        nodes += [up, node]
        inputs.append(("WorldNormal", node))
    for wanted, cls, input_name in ((spec.world_position, "MaterialExpressionWorldPosition", "WorldPos"),
                                    (spec.object_bounds, "MaterialExpressionObjectWorldPosition", "ObjPos"),
                                    (spec.object_bounds, "MaterialExpressionObjectRadius", "ObjRadius")):
        if wanted:
            node = _node(cls, material, input_name)
            nodes.append(node)
            inputs.append((input_name, node))
    customs = []
    for label, code in (("HLSL", spec.code), ("Distortion", spec.distortion)):
        if not code:
            continue
        custom = _node("MaterialExpressionCustom", material, label)
        custom.Code = re.sub(r"TEX\((\d+)\)", r"PixelUE_Texture2D_\1", code)
        custom.OutputType = _FLOAT4
        custom.Description = f"{spec.name} {label}"
        custom.Inputs = [unrealsdk.make_struct("CustomInput", InputName=input_name,
                                               Input=unrealsdk.make_struct("ExpressionInput", Expression=node))
                         for input_name, node in inputs]
        customs.append(custom)
    custom = customs[0]
    material.Expressions = [*nodes, *customs]
    if len(customs) > 1:
        _connect(material, "Distortion", customs[1], "rg")
    material.LightingModel = _UNLIT
    material.BlendMode = spec.blend
    material.TwoSided = spec.two_sided
    material.bDisableEdgeDetection = not spec.edge_detection
    for usage in spec.usage:
        setattr(material, usage, True)
    _connect(material, "EmissiveColor", custom, "rgb")
    if spec.blend == TRANSLUCENT:
        _connect(material, "Opacity", custom, "a")
    elif spec.blend == MASKED:
        _connect(material, "OpacityMask", custom, "a")
    return material


def material(spec: Shader) -> UObject | None:
    """The compiled material for a spec (built and compiled once, then rooted), or None: runtime materials are off,
    a texture isn't loaded, there's no player controller yet (try again later), or its HLSL didn't compile."""
    if spec.name in _materials:
        return _materials[spec.name]
    if spec.name in _failed or not available() or get_pc() is None:
        return None
    built = _build(spec)
    if built is None:
        return None
    begun, before = time.perf_counter(), dict(shadercache.stats)
    if not compile_material(built):
        _failed.add(spec.name)
        _log(f"{spec.name}: didn't compile (an HLSL error?); BL2 stand-in stays")
        return None
    _materials[spec.name] = built
    asked, kept = (shadercache.stats[key] - before[key] for key in ("asked", "kept"))
    _log(f"{spec.name}: compiled ({built.Name}) in {time.perf_counter() - begun:.2f} s"
         + (f": {asked} shaders, {kept} of them kept from an earlier start" if asked else ""))
    return built


def compiled_names() -> set[str]:
    """The specs whose materials are compiled."""
    return set(_materials)


def failed(name: str) -> bool:
    """Whether a spec's material failed to compile (then its stand-in stays for the session)."""
    return name in _failed


def instance(spec: Shader, name: str, textures: dict[str, UObject] | None = None,
             vectors: dict[str, tuple[float, float, float, float]] | None = None,
             scalars: dict[str, float] | None = None) -> UObject | None:
    """A material instance of a spec's material with its own parameter values (built once, rooted), or None."""
    parent = material(spec)
    if parent is None:
        return None
    mic = _find("MaterialInstanceConstant", f"{PACKAGE}.{name}")
    if mic is None:
        mic = _root(unrealsdk.construct_object("MaterialInstanceConstant", _package(), name))
    if mic.Parent != parent:           # an earlier one's (a module reload compiles the material anew)
        mic.SetParent(parent)
    for param, texture in (textures or {}).items():
        mic.SetTextureParameterValue(param, _root(texture))
    for param, (r, g, b, a) in (vectors or {}).items():
        mic.SetVectorParameterValue(param, unrealsdk.make_struct("LinearColor", R=r, G=g, B=b, A=a))
    for param, value in (scalars or {}).items():
        mic.SetScalarParameterValue(param, value)
    return mic
