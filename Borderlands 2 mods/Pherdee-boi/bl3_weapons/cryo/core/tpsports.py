"""TPS's own particle materials, ported line for line from their compiled pixel shaders (RESEARCH.md §7, "Measured
against TPS").

TPS keeps every material's compiled shaders in a package's shader cache: her streaming package for her own and the
copies she carries, `Startup.upk` and `WillowGame.upk` for the shared ones she imports. `tools/shadercache.py`
disassembles them; each port below is that assembly turned back into HLSL, the constants and the textures as TPS has
them. A material instance with static switches compiles to its own permutation: the one ported is the one its id
picks. Where a port can't be exact it says so (a refraction). Cube maps, which the runtime compiler can't bind, are
read through one face where all six are the same image (CUBE_UV).

Ports are grouped by blend mode into a few shared materials (GROUPS; particles.py compiles them, each one costs a hitch),
and each TPS material is an instance: `Look` picks its branch, `Tex0`-`Tex3` are its textures, `P0`/`P1` its own
constants (what they mean is in each port's comment). TPS writes additive and modulate colours premultiplied, as the
engine does with an additive material's opacity; here the HLSL does it, because these materials have no opacity input.

What every branch sees (the shared prelude): `uv` (a SubUV sprite's current frame), `uvB` (its next frame), `sub`
(the blend between them), `vc` (the particle's colour), `gap` (the scene's depth behind the pixel minus the pixel's:
TPS's soft particles), `sky` (TPS's tint for unlit translucent particles from the map's time-of-day light; BL2 has
the same `TimeOfDayLightColor`), and `Time`.

TPS's "Periodic" (a material's Frac of something computed once per frame, like a time) keeps the sign: x - trunc(x),
so -0.05 stays -0.05 where HLSL's frac() gives 0.95. On a wrapping texture the two only differ by whole tiles; on a
clamped one they don't (Excellent Shot's marker). A frac() of a per-pixel value is the shader's own (x - floor(x)).
"""

from __future__ import annotations

from dataclasses import dataclass

ADD, ADD2, TRAN, TRAN2, TRAN3, TRAN4, MOD = "add", "add2", "tran", "tran2", "tran3", "tran4", "mod"
# group -> (blend, compiles the dynamic-parameter vertex factories). Each group is one material: with every
# branch compiled in, a group's pixel shader must stay within ps_3_0's 512 instruction slots, or the device
# refuses it and the engine drops the whole material (the first translucent group hit 524, 2026-10-02).
GROUPS = {ADD: ("additive", False), ADD2: ("additive", False), TRAN: ("translucent", True),
          TRAN2: ("translucent", False), TRAN3: ("translucent", False), TRAN4: ("translucent", True),
          MOD: ("modulate", False)}


@dataclass(frozen=True)
class Port:
    group: str
    code: str
    textures: tuple[str, ...] = ()
    p0: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    p1: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    cap64: bool = False      # P1.xy = (the mip that is 64x64, the size) of its first texture, as found at runtime


PRELUDE = """float2 uv = Parameters.TexCoords[0];
float2 uvB = Parameters.TexCoords[1];
float sub = Parameters.TexCoords[2].x;
float4 vc = Parameters.VertexColor;
float2 dx = ddx(uv), dy = ddy(uv);
float gap = PreviousDepth(Parameters.ScreenPosition) - Parameters.ScreenPosition.w;
float3 sky = TimeOfDayLightColor.w > 0.0001 ? (TimeOfDayLightColor.rgb + 0.68) / TimeOfDayLightColor.w : 1.68;
sky = lerp(sky, SkyOverride.rgb, SkyOverride.a);
"""

FX = "FX_Shared_"
T = "Crocus_IceFX.Textures."   # the textures this mod's build carries from TPS (convert/icefx.py TEXTURES); Aurelia carried them in her character's package


def carried(path: str) -> str:
    """A texture her build carries from TPS (tools/tps2bl2.py CARRY_TEXTURES: its 64x64 mips, as TPS draws it),
    else BL2's own where a map has it."""
    return f"{T}{path.rpartition('.')[2]}|{path}"


# A cube map lookup without a cube sampler (the runtime compiler stubs cube textures): D3D's face rule turns the
# direction R into `face`, the UV on whichever face R points at. Only for cubes whose six faces are one image, so
# that one face can be read for all of them: both cubes her ports use. BL2 has those cubes, but a cube's faces get no
# texture of their own (sampled, they read white), so her build carries face 0 from TPS as a plain texture with all
# its mips (256x256; tools/tps2bl2.py CARRY_TEXTURES), named after the material using it.
CUBE_UV = """float3 ar = abs(R);
float2 face = ar.x >= ar.y && ar.x >= ar.z ? float2(R.x >= 0.0 ? -R.z : R.z, -R.y) / ar.x
            : ar.y >= ar.z ? float2(R.x, R.y >= 0.0 ? R.z : -R.z) / ar.y
            : float2(R.z >= 0.0 ? R.x : -R.x, -R.y) / ar.z;
face = face * 0.5 + 0.5;
"""
SPOTS = T + "Mat_AcidSprayGoop_0"                   # SphericalSpots_Cube's face
TOONED = T + "M_FX_Crocus_HeyMadamNiceShot_0"       # Generic2_Cube_Tooned's face


PORTS: dict[str, Port] = {
    # --- additive -----------------------------------------------------------------------------------------------
    # Her ice sparkles: the dust-mote texture twice (turning slowly about the centre, and tiled 10x) times a third,
    # panning, pushed through huge per-channel gains, with a round falloff. The texture is black but for its motes,
    # so the look depends on which mip is read. TPS keeps it at UE3's minimum of 7 resident mips (64x64 at the top,
    # measured 2026-10-02: an effect this small doesn't make the streamer load more), where the motes blur into a
    # faint haze that the gains light up as cyan, motion-stretched sprites; with every mip resident (BL2's own copy,
    # her build's) only specks are left. So the mip is chosen here, never finer than 64x64: P1 = (that mip, the
    # texture's size), set for whichever copy is found (`cap64`).
    "M_FX_IceSparkles": Port(ADD, """float2 p = uv - 0.5;
float s = sin(0.1 * Time), c = cos(0.1 * Time);
float lod = log2(max(length(dx), length(dy)) * P1.y);
float3 t = (tex2Dlod(TEX(0), float4(float2(c * p.x - s * p.y, s * p.x + c * p.y) + 0.5, 0.0, max(lod, P1.x))).rgb
          + tex2Dlod(TEX(0), float4(uv * 10.0, 0.0, max(lod + 3.321928, P1.x))).rgb)
         * tex2Dlod(TEX(0), float4(uv * 1.5 + float2(0.0, frac(0.02 * Time)), 0.0, max(lod + 0.584963, P1.x))).rgb;
float3 rgb = min(saturate(t * float3(11250.0, 75000.0, 560000.0)) * vc.rgb, 4.0);
float d = dot(p, p);
return float4(rgb * (1.0 - saturate(d * d * 15.0)) * vc.a, 1.0);
""", (carried("FX_Shared_Dirt.Textures.Tex_Dustmotes2"),), cap64=True),
    # Whiteout's haze: a panning smoke squared, a column texture, two scrollers turning against each other, tinted
    # by the sky light; its opacity is the 2x2 smoke sheet's blue, soft against geometry over 16 units.
    "Mat_GreySmoke": Port(ADD, """float3 pan = tex2Dgrad(TEX(0), uv + float2(0.0, frac(0.1 * Time)), dx, dy).rgb;
float col = tex2Dgrad(TEX(1), uv, dx, dy).r;
float sa = sin(-0.08 * Time), ca = cos(-0.08 * Time), sb = sin(0.05 * Time), cb = cos(0.05 * Time);
float2 a = uv + float2(-1.5, -1.0), b = uv + float2(0.5, -1.0);
float s = tex2Dgrad(TEX(2), float2(ca * a.x - sa * a.y, sa * a.x + ca * a.y) + float2(1.5, 1.0), dx, dy).g
        + tex2Dgrad(TEX(2), float2(cb * b.x - sb * b.y, sb * b.x + cb * b.y) + float2(-0.5, 1.0), dx, dy).g;
float3 rgb = min(sky * pan * pan * (col * 8.0 * lerp(s, col, 0.309017)) * vc.rgb, 4.0);
return float4(rgb * saturate(gap * 0.0625) * tex2Dgrad(TEX(3), uv, dx, dy).b * vc.a, 1.0);
""", (FX + "Smoke.Textures.Tiling_SmokePanner2_Dif", FX + "Smoke.Textures.Tiling_GenericSmoke3_Dif",
      FX + "Smoke.Textures.Tiling_Smoke_Scroller_256_Dif", "FX_ENV_Smoke.Textures.SubUV_2X2_GenericSmoke2_Dif")),
    # Her snowflakes: the flake (a muzzle-flash shape) lit by a reflection off the ice normal map, the frozen lake
    # tiled 3x, blue-weighted (2, 2, 3); soft over 16 units.
    "M_FX_Crocus_Snowflakes": Port(ADD2, """float3 V = normalize(Parameters.TangentCameraVector);
float2 uv3 = uv * 3.0;
float3 n = normalize(tex2Dgrad(TEX(0), uv3, dx * 3.0, dy * 3.0).rgb * 2.0 - 1.0);
float d = dot(n, V);
float3 m = tex2Dgrad(TEX(2), uv, dx, dy).rgb;
float env = tex2Dlod(TEX(1), float4(2.0 * d * n.y - V.y, 2.0 * d * n.z - V.z, 0.0, 0.0)).r;
float3 rgb = min(m * (env * 0.25 + m) * tex2Dgrad(TEX(3), uv3, dx * 3.0, dy * 3.0).r * vc.rgb * float3(2.0, 2.0, 3.0), 4.0);
return float4(rgb * saturate(gap * 0.0625) * vc.a, 1.0);
""", (carried("Prop_Skybox.MoveMe.Ice_Nrm"), carried("Prop_Skybox.MoveMe.R2Tex_SnowTempCubeStaticNegY"), carried("FX_CO_Wep_Ice.Textures.Ice_Muzzle_01"),
      carried("Prop_Glacier.Textures.FrozenLake"))),
    # Magic Bullet's ring: the energy ring tiled 2x, times (11, 10, 10).
    "Mat_Ring_MH_NonMesh": Port(ADD2, """return float4(min(tex2Dgrad(TEX(0), uv * 2.0, dx * 2.0, dy * 2.0).rgb * float3(11.0, 10.0, 10.0) * vc.rgb, 4.0) * vc.a, 1.0);
""", (FX + "Energy.Textures.Tex_Ring_Energy2_Mirror",)),
    # A procedural flash: (1 - r)^8 from the sprite's centre; soft over 31 units (Depth_Bias -30).
    "Mat_Explosion_Flash_Add_Procedural": Port(ADD2, """float k = 1.0 - length(uv - 0.5);
k *= k; k *= k; k = saturate(k * k);
return float4(min(k * vc.rgb, 4.0) * saturate(k * vc.a) * saturate(gap / 31.0), 1.0);
"""),
    # You First's impact flash: the sun flare times the colour (TPS ignores the particle's alpha here).
    "Mat_Assassin_Dash_Impact_Flash": Port(ADD2, """return float4(min(vc.rgb * tex2Dgrad(TEX(0), uv, dx, dy).rgb, 4.0), 1.0);
""", (FX + "Lighting.Textures.Round_Flare_Sun_Dif",)),
    # You First's flare: the sun flare doubled; soft over 22 units.
    "Mat_Hologram_Flare": Port(ADD2, """float3 t = tex2Dgrad(TEX(0), uv, dx, dy).rgb * 2.0 * vc.rgb;
return float4(min(vc.a * t, 4.0) * saturate(gap / 22.0) * t.r * vc.a, 1.0);
""", (FX + "Lighting.Textures.Round_Flare_Sun_Dif",)),
    # --- translucent --------------------------------------------------------------------------------------------
    # Whiteout's frost at the screen's edges: the frost sheet's green, revealed by the dissipate mask as the
    # emitter's dynamic parameter z rises.
    "Mat_Snow_Screen_Dissipate": Port(TRAN, """float g = tex2Dgrad(TEX(0), uv, dx, dy).g;
float m = saturate(tex2Dgrad(TEX(1), uv, dx, dy).g + GetDynamicParameter(Parameters).z);
return float4(min(sky * g * vc.rgb, 4.0), saturate(m * g * vc.a * 5.0));
""", (carried("FX_ENV_Weather.Textures.Snow_FlakesScreen_Dif"), carried("FX_ENV_Weather.Textures.Snow_Dissipate_Mask"))),
    # Falling flakes (an instance of the shared master Mat_VertColor_Unlit_Tran_SubUV_MS, permutation "depth-biased
    # alpha" + "world light colour"): the colour is the particle's, sky-tinted; the flake sheet's red, frame-blended,
    # is the opacity. P0.x: Depth_Bias_Value.
    "Mati_Snow_Flakes": Port(TRAN2, """float a = saturate(lerp(tex2Dgrad(TEX(0), uv, dx, dy).r, tex2Dgrad(TEX(0), uvB, dx, dy).r, sub) * vc.a);
return float4(min(sky * vc.rgb, 4.0), a * saturate(gap / (1.0 - P0.x)));
""", (carried("FX_ENV_Weather.Textures.Snow_Flakes_Dif"),), p0=(-64.0, 0.0, 0.0, 0.0)),
    # Snow puffs: a highlighted snow panner over render clouds that scroll with time and the dynamic parameter x,
    # plus the particle's colour, sky-tinted; the opacity is the mask (and the highlight) over a long depth fade.
    # P0: HighlightColor; P1: (TileSpecs, DepthBias).
    "Mat_IceP_Snow": Port(TRAN, """float3 hl = tex2Dgrad(TEX(0), uv * P1.x * 2.0, dx * P1.x * 2.0, dy * P1.x * 2.0).rgb * P0.rgb;
float3 cl = saturate(tex2Dgrad(TEX(1), uv + (Time + GetDynamicParameter(Parameters).x) * float2(-0.3, 0.3), dx, dy).rgb + 0.1);
float mask = tex2Dgrad(TEX(2), uv, dx, dy).r;
float a = (hl.r * cl.r + mask) * mask * vc.a;
return float4(min(sky * (hl * cl + vc.rgb), 4.0), saturate(saturate(gap / (1.0 - P1.y)) * a));
""", (carried("FX_ENV_Weather.Textures.Snow_Panner_Dif"), FX + "Smoke.Textures.Tiling_Rendercloud_Dif",
      FX + "Smoke.Textures.GenericSmoke1_Dif"), p0=(5.0, 5.0, 5.0, 1.0), p1=(1.0, -512.0, 0.0, 0.0)),
    # The snow puffs of TPS's frozen-enemy effects and cryo explosions (FX_CO_IceCreeper.Mat_SimpleSnow, from
    # TPS's Startup.upk): Mat_IceP_Snow's recipe with its own constants and no depth fade. The snow panner tiled 2x,
    # times (2.1, 2.85, 3), over the render clouds, plus the particle's colour, sky-tinted; the opacity is the mask
    # (one soft puff) and the highlight, times 5. In a group of its own: the first one's shader has no room left.
    "Mat_SimpleSnow": Port(TRAN4, """float3 hl = tex2Dgrad(TEX(0), uv * 2.0, dx * 2.0, dy * 2.0).rgb * float3(2.1, 2.85, 3.0);
float3 cl = tex2Dgrad(TEX(1), uv + (Time + GetDynamicParameter(Parameters).x) * float2(-0.3, 0.3), dx, dy).rgb;
float mask = tex2Dgrad(TEX(2), uv, dx, dy).r;
return float4(min(sky * (hl * cl + vc.rgb), 4.0), saturate((hl.r * cl.r + mask) * mask * 5.0 * vc.a));
""", (carried("FX_ENV_Weather.Textures.Snow_Panner_Dif"), FX + "Smoke.Textures.Tiling_Rendercloud_Dif",
      FX + "Smoke.Textures.GenericSmoke1_Dif")),
    # Clouds around her: the cloud sheet and its alpha, frame-blended; a 151-unit depth fade.
    "Mat_CloudsA": Port(TRAN2, """float3 rgb = min(lerp(tex2Dgrad(TEX(0), uv, dx, dy).rgb, tex2Dgrad(TEX(0), uvB, dx, dy).rgb, sub) * vc.rgb, 4.0);
float a = lerp(tex2Dgrad(TEX(1), uv, dx, dy).r, tex2Dgrad(TEX(1), uvB, dx, dy).r, sub) * vc.a;
return float4(rgb, saturate(gap * 0.00662252) * a);
""", (carried("FX_ENV_VOG.Textures.CloudsA_Dif2"), carried("FX_ENV_VOG.Textures.CloudsA_Alpha"))),
    # Backhand's smoke: a panning smoke times a second, sky-tinted; a 7.5-unit depth fade.
    "Mat_Emp_Trans": Port(TRAN2, """float t1 = tex2Dgrad(TEX(1), uv, dx, dy).r;
float a = tex2Dgrad(TEX(0), uv + float2(0.0, frac(0.1 * Time)), dx, dy).r * 0.5 * t1;
return float4(min(saturate(sky * a * vc.rgb), 4.0), saturate(saturate(gap * 0.13333334) * t1 * vc.a));
""", (FX + "Smoke.Textures.Smoke2_GP_Dif", FX + "Smoke.Textures.Pyro_Smoke_GP_03_Dif")),
    # The shared translucent master (Mat_Basic_FX_Unlit_Tran_MS) with every switch off: the Contract's screen words.
    "Mat_Basic_FX_Unlit_Tran_MS": Port(TRAN2, """return float4(min(clamp(tex2Dgrad(TEX(0), uv, dx, dy).rgb * vc.rgb, 0.0, 4.0), 4.0), saturate(tex2Dgrad(TEX(1), uv, dx, dy).r * vc.a));
"""),
    # The same master, "single texture" + "depth-biased alpha": Backhand's spikes. P0.x: Depth_Bias_Value.
    "Mat_Basic_FX_Unlit_Tran_MS_SingleGP_DBA": Port(TRAN2, """float t = tex2Dgrad(TEX(0), uv, dx, dy).r;
return float4(min(clamp(t * vc.rgb, 0.0, 4.0), 4.0), saturate(t * vc.a) * saturate(gap / (1.0 - P0.x)));
""", p0=(-15.0, 0.0, 0.0, 0.0)),
    # Backhand's goop: a height from two textures raised to the particle's alpha, lit by a cube map
    # (SphericalSpots_Cube) along the reflection off a normal made from that height. The runtime compiler can't bind
    # a cube, but all six of this one's faces are the same image, so CUBE_UV (D3D's face rule) and face 0 stand in.
    # P1: (tile U, tile V, DepthBias_Rate). Opacity: dynamic parameter x.
    "Mat_AcidSprayGoop": Port(TRAN, """float2 st = uv * P1.xy;
float h = clamp(clamp(tex2Dgrad(TEX(0), st, dx * P1.x, dy * P1.y).r, 0.0, 0.5) + tex2Dgrad(TEX(1), st, dx * P1.x, dy * P1.y).r, 0.0, 1.5);
float p = h > 1e-6 ? pow(h, vc.a) : 0.0;
float3 N = normalize(float3(p, p, sqrt(abs(1.0 - p * p))));
float3 V = normalize(Parameters.TangentCameraVector);
float3 R = 2.0 * dot(N, V) * N - V;
""" + CUBE_UV + """float k = saturate(p * 10.0);
return float4(min(tex2Dgrad(TEX(2), face, dx, dy).rgb * k * vc.rgb, 4.0), saturate(gap / (1.0 - P1.z)) * k * GetDynamicParameter(Parameters).x);
""", (FX + "Energy.Textures.PhaseLockScreenMask02_Dif_Tex", FX + "Fire.Textures.RoilingFlame_Dif", SPOTS),
        p1=(1.0, 1.0, -20.0, 0.0)),
    # Excellent Shot, Madam's marker: its texture's green, bobbing (0.07 sin 2πt), the opacity; coloured from a cube
    # map (Generic2_Cube_Tooned, six identical faces: face 0 by CUBE_UV) along the reflection off a 2D normal made
    # from its red plus green. 16-unit depth fade. TPS takes the bob's "Periodic" (see the module notes), which leaves
    # it as it is: frac() would push the downward half a whole texture away, off this clamped one (it blinked).
    "M_FX_Crocus_HeyMadamNiceShot": Port(TRAN3, """float4 t = tex2Dgrad(TEX(0), uv + float2(0.0, 0.07 * sin(6.28319 * Time)), dx, dy);
float a = t.r + t.g;
float2 n = normalize(float2(a, sqrt(abs(1.0 - a * a))));
float3 V = normalize(Parameters.TangentCameraVector);
float3 R = float3(2.0 * dot(n, V.xy) * n - V.xy, -V.z);
""" + CUBE_UV + """return float4(min(tex2Dgrad(TEX(1), face, dx, dy).rgb * vc.rgb, 4.0), t.g * vc.a * saturate(gap * 0.0625));
""", (T + "T_FX_HeyMadamNiceShot", TOONED)),
    # Magic Bullet's flash: an energy texture and a round flare, each turned by an angle that follows the sprite's
    # place on screen and its distance; invisible nearer than 200 units, squared alpha. TPS lerps from the scene's
    # colour to the flare by a depth fade whose bias grows as the alpha falls; that colour is what lies behind the
    # pixel, so blending the flare at alpha x fade is the same thing.
    "Mat_Lens_Flare_PD": Port(TRAN3, """float w = Parameters.ScreenPosition.w;
float depthF = saturate(w * 0.000122070313);
float2 suv = Parameters.ScreenPosition.xy / w * ScreenPositionScaleBias.xy + ScreenPositionScaleBias.wz;
float2 p = uv - 0.5;
float a1 = suv.x * 20.0 * depthF, a2 = depthF * suv.y * -15.0;
float s1 = sin(a1), c1 = cos(a1), s2 = sin(a2), c2 = cos(a2);
float g = tex2Dgrad(TEX(0), float2(c1 * p.x - s1 * p.y, s1 * p.x + c1 * p.y) + 0.5, dx, dy).g
        + tex2Dgrad(TEX(1), float2(c2 * p.x - s2 * p.y, s2 * p.x + c2 * p.y) + 0.5, dx, dy).b;
float k = (1.0 - depthF) * g * vc.a;
float alpha = w > 200.0 ? k * k : 0.0;
float bias = 8.0 * (1.0 - alpha);
float fade = saturate(gap * (bias - 0.001 >= 0.0 ? 1.0 / bias : 1000.0));
return float4(min(g * vc.rgb, 4.0), alpha * fade);
""", (FX + "Energy.GreyPack.T_Pyro_Energy_GP_07", FX + "Energy.GreyPack.T_Round_Flare_GP_01")),
    # Backhand's ice chunks (FX_CO_Damage_Ice.Mat_IceChunk2): the rock sheet's luminance x10 shades a mostly grey
    # frozen lake (tiled 3x, a fifth of its colour kept) plus three tenths of a sky reflection off a rock normal map;
    # the rock sheet's alpha is the opacity. Two of its textures are TPS-only: her build carries them, named after
    # this material (tools/tps2bl2.py CARRY_NAMES).
    "Mat_IceChunk2": Port(TRAN3, """float4 rocks = tex2Dgrad(TEX(3), uv, dx, dy);
float lum = dot(rocks.rgb, float3(0.3, 0.59, 0.11)) * 10.0;
float3 V = normalize(Parameters.TangentCameraVector);
float3 n = normalize(tex2Dgrad(TEX(0), uv * 0.5, dx * 0.5, dy * 0.5).rgb * 2.0 - 1.0);
float d = dot(n, V);
float3 env = tex2Dlod(TEX(1), float4(2.0 * d * n.y - V.y, 2.0 * d * n.z - V.z, 0.0, 0.0)).rgb;
float3 lake = tex2Dgrad(TEX(2), uv * 3.0, dx * 3.0, dy * 3.0).rgb;
float3 ice = lake.r + 0.2 * float3(0.0, lake.g - lake.r, lake.b - lake.r) + 0.3 * env;
return float4(min(ice * lum * vc.rgb, 4.0), rocks.a * vc.a);
""", (T + "AridRock_Nrm|Prop_Rocks_03.Textures.AridRock_Nrm", carried("Prop_Skybox.MoveMe.R2Tex_SnowTempCubeStaticNegY"),
      carried("Prop_Glacier.Textures.FrozenLake"), T + "4X4Rocks|FX_CO_Damage_Ice.Textures.4X4Rocks")),
    # Chin Up's and Magic Bullet's orb: the cross texture, stretched 2x across.
    "Mat_TransfusionOrb_Suck": Port(TRAN2, """float4 t = tex2Dgrad(TEX(0), uv * float2(2.0, 1.0), dx * float2(2.0, 1.0), dy * float2(2.0, 1.0));
return float4(min(t.rgb * vc.rgb, 4.0), t.r * vc.a);
""", ("FX_WEP_Grenades.Textures.Cross_Tex_Half_Mirror",)),
    # --- modulate (TPS's "modulate" flashes brighten: the scene is multiplied by 1 + the flash) ------------------
    # Backhand's and Winter's Fury's flash: a soft round; an 81-unit depth fade.
    "Mat_ExplosionFlashModulate": Port(MOD, """float t = tex2Dgrad(TEX(0), uv, dx, dy).r;
return float4(1.0 + min(saturate(gap * 0.012345679) * t * vc.rgb * vc.a, 3.0), 1.0);
""", (FX + "Energy.GreyPack.T_Round_Soft_GP_01",)),
    # Magic Bullet's glow: a soft orb tiled 2x. P0.x: Bias.
    "Mat_SirenGlowMOD": Port(MOD, """float t = tex2Dgrad(TEX(0), uv * 2.0, dx * 2.0, dy * 2.0).r;
return float4(1.0 + min(t * vc.rgb * saturate(gap / (1.0 - P0.x)) * vc.a, 3.0), 1.0);
""", (FX + "Energy.Textures.EnergyOrbSoftMod_Dif_Mirror",), p0=(-15.0, 0.0, 0.0, 0.0)),
}

# TPS material (as her emitters name it) -> (port, its own textures and constants where an instance overrides them)
INSTANCES: dict[str, tuple[str, tuple[str, ...] | None, tuple | None, tuple | None]] = {
    "Mat_IceP_Snow_SubUV2x2_01": ("Mat_IceP_Snow", (carried("FX_ENV_Weather.Textures.Snow_Panner_Dif"),
                                                    FX + "Smoke.Textures.Tiling_Rendercloud_Dif",
                                                    carried("FX_ENV_Weather.Textures.Snow_Dust_Dif")),
                                  (4.0, 5.0, 6.0, 1.0), (1.5, -512.0, 0.0, 0.0)),
    "MI_FX_Crocus_Master": ("Mat_Basic_FX_Unlit_Tran_MS", (T + "T_FX_Master_E", T + "T_FX_Crocus_Contract_Screen_M"),
                            None, None),
    "MI_FX_Crocus_Servant": ("Mat_Basic_FX_Unlit_Tran_MS", (T + "T_FX_Servant_E", T + "T_FX_Crocus_Contract_Screen_M"),
                             None, None),
    "Mati_Explosive_Spikes": ("Mat_Basic_FX_Unlit_Tran_MS_SingleGP_DBA", (FX + "Energy.Textures.Energy_Spike_Dif",),
                              None, None),
}


def resolve(tps_material: str) -> tuple[Port, tuple[str, ...], tuple, tuple] | None:
    """A TPS material's port with the textures and constants its instance uses, or None if it has none."""
    name, textures, p0, p1 = INSTANCES.get(tps_material, (tps_material, None, None, None))
    port = PORTS.get(name)
    if port is None:
        return None
    return port, textures if textures is not None else port.textures, p0 or port.p0, p1 or port.p1


def group_code(group: str) -> str:
    """One shared material's HLSL: the prelude, then a branch per port (index = its Look)."""
    body = [PRELUDE]
    for index, port in enumerate(p for p in PORTS.values() if p.group == group):
        body.append(f"[branch] if (Look < {index}.5) {{\n{port.code}}}\n")
    body.append("return float4(0.0, 0.0, 0.0, 0.0);\n")
    return "".join(body)


def look_of(port: Port) -> int:
    return [p for p in PORTS.values() if p.group == port.group].index(port)
