"""TPS's ice effects for her build: Crocus_IceFX_SF.upk, from TPS's own Startup.upk (RESEARCH.md §6.30, §6.32).

TPS draws a frozen enemy's icicles, its thaw and its shatter natively, with four particle systems in its always-loaded
Startup.upk (`FX_CO_Damage_Ice.Particles`). Its cryo explosions (three sizes) and the Cryo Nova shield's blast are four
more (`FX_CO_Wep_Ice.Particles`), which iceeffects.py puts on the cryo explosions in place of shock's. They are carried
into a package of their own (upkextract.py), under Crocus_IceFX so that nothing clashes with BL2's objects, with the two
meshes the frozen effects need converted (staticmesh.py; a third, IcicleSingleLong, has a physics body the converter
doesn't take, and its emitter stays off).

TPS's materials can't load in BL2: most are left out and drawn at runtime (src/aurelia/particles.py). The explosions
share some of theirs with BL2's own explosions, and BL2's always-loaded Startup.upk has those, and the Cryo Nova's ring
mesh: the package imports BL2's own (`KEEP`), which draw as they are.

The cryo area-effect grenade's spray (`Part_Ice_AOESpray`, RESEARCH.md §6.43) is a ninth, from the same package.

TPS's slam is here too (RESEARCH.md §6.40): the ring of dust and stones every slam raises (`Part_PlayerSlam_500r`, in
TPS's Startup.upk) and the burst of snow a cryo slam adds (`PS_Slam_Ice_500r`, which only the character packages
hold: taken from the Baroness's own, the one her build already reads).

The package also carries TPS's cryo element icon for item cards (RESEARCH.md §6.33). Both games draw a card's element
icons from one sheet of UI art (SharedWillowComponents_I1F, 1024x1024, DXT5, in WillowGame.upk), and TPS's has a
snowflake in two places: the stat rows' ("elementIce") and the title's ("ice"). The DXT blocks that hold the two are
copied, as they are, into the corner of a small texture of its own, which src/aurelia/cardicons.py shows on BL2's cards.

    python tools/cli.py icefx <TPS root> <out.upk>
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import texdump
from findexport import PartialPackage
from upk import Package
from upkextract import ExtractError, Extraction

PACKAGE = "Crocus_IceFX_SF.upk"
TOP = "Crocus_IceFX"
SOURCE = Path("WillowGame/CookedPCConsole/Startup.upk")
# TPS path -> its name under Crocus_IceFX.Particles
SYSTEMS = {f"FX_CO_Damage_Ice.Particles.{name}": name
           for name in ("Part_Frozen_Icicles", "Part_Frozen_BreakingLoose", "Part_Frozen_BreakFree", "Part_Frozen_Shatter")}
SYSTEMS.update({f"FX_CO_Wep_Ice.Particles.{name}": name
                for name in ("Part_IceExplosion_Small", "Part_IceExplosion_Medium", "Part_IceExplosion_Large",
                             "Part_Ice_Nova_Shield_Explosion", "Part_Ice_AOESpray")})
SYSTEMS["FX_CO_Shared.Particles.Part_PlayerSlam_500r"] = "Part_PlayerSlam_500r"
# from the Baroness's own package (TPS's Startup.upk has only the plain slam's)
SLAM_SOURCE = Path("DLC/Crocus/Compat/Content/Crocus_Baroness_Streaming_SF.upk")
SLAM_SYSTEMS = {"FX_CO_Shared.Particles.PS_Slam_Ice_500r": "PS_Slam_Ice_500r"}
# Objects BL2 has (its Startup.upk, checked 2026-10-03): imported, not carried
KEEP = frozenset({"EngineMaterials.DefaultParticle",
                  "FX_Brick.Materials.Mati_Impact_Brick_RagePunch",
                  "FX_CHAR_Siren.Materials.Mat_Stagnant_Buildup",
                  "FX_CREA_EXPLoader.Materials.Mati_XPLoader_Glint3",
                  "FX_ENV_Smoke.Materials.Mati_Wispy_Smoke_Cloud_SubUV_LowBias",
                  "FX_Fast_Travel.Mesh.Mesh_FastTravel",
                  "FX_Shared_Dirt.Materials.Mati_Dirt_Chunks_SubUV_2x2",
                  "FX_Shared_Explosions.Materials.Mati_Distortion_Ring_Add",
                  "FX_Shared_Mesh.Meshes.Planes.DecalPlane_LiquidDev"})
# The cryo element icon: TPS's sheet of card art, and the DXT5 blocks (4x4 pixels each) of it that hold the icon's two
# places, (207, 939)-(244, 976) for the stat rows and (246, 939)-(283, 976) for the title (the sub-images its card
# movie names, read 2026-10-03). They go into the top left corner of a texture of ICON_SIZE, the rest transparent, so
# the icons sit at (3, 3)-(40, 40) and (42, 3)-(79, 40) there: cardicons.py's ICON_PLACES.
ICON_SOURCE = Path("WillowGame/CookedPCConsole/WillowGame.upk")
ICON_SHEET = "SharedWillowComponents.SharedWillowComponents_I1F"
ICON_SHEET_SIZE = (1024, 1024)
ICON_BLOCKS = (51, 234, 20, 10)          # first block's column and row, then columns and rows
ICON_SIZE = (128, 64)
ICON = "Icon_ElementIce"
DXT5_BLOCK = 16
# Textures the ice looks draw with that BL2 has only in some maps or not at all (core/tpsports.py `carried`, cryo/frostlayer.py): TPS path ->
# (which TPS package holds it, its name under Crocus_IceFX.Textures). From TPS's own caches, each kept from TEXTURE_TOP down (these shade small sprites
# and a coat of frost: TPS's streamer never loads them sharper either). "hers" is the Baroness's package (the Baroness Pack): without it those are left
# out, and the looks that need them fall back to BL2's own copy where a map has it, else stay off.
TEXTURE_TOP = 256
TEXTURES = {
    "FX_CO_Wep_Ice.Textures.Ice_Muzzle_01": ("startup", "Ice_Muzzle_01"),
    "FX_CO_Damage_Ice.Textures.4X4Rocks": ("startup", "4X4Rocks"),
    "Prop_Glacier.Textures.FrozenLake": ("game", "FrozenLake"),
    "Prop_Skybox.MoveMe.Ice_Nrm": ("game", "Ice_Nrm"),
    "Prop_Skybox.MoveMe.R2Tex_SnowTempCubeStaticNegY": ("game", "R2Tex_SnowTempCubeStaticNegY"),
    "FX_ENV_Weather.Textures.Snow_Panner_Dif": ("game", "Snow_Panner_Dif"),
    "Prop_Rocks_03.Textures.AridRock_Nrm": ("game", "AridRock_Nrm"),
    "FX_Shared_Dirt.Textures.Tex_Dustmotes2": ("hers", "Tex_Dustmotes2"),
    "FX_ENV_Weather.Textures.Snow_FlakesScreen_Dif": ("hers", "Snow_FlakesScreen_Dif"),
    "FX_ENV_Weather.Textures.Snow_Dissipate_Mask": ("hers", "Snow_Dissipate_Mask"),
    "FX_ENV_Weather.Textures.Snow_Flakes_Dif": ("hers", "Snow_Flakes_Dif"),
    "FX_ENV_Weather.Textures.Snow_Dust_Dif": ("hers", "Snow_Dust_Dif"),
    "FX_ENV_VOG.Textures.CloudsA_Dif2": ("hers", "CloudsA_Dif2"),
    "FX_ENV_VOG.Textures.CloudsA_Alpha": ("hers", "CloudsA_Alpha"),
    "FX_CO_Gladiator.Textures.T_FX_GladShield_Ice": ("hers", "T_FX_GladShield_Ice"),
}
# BL2's seekfree package header, as her converted packages have it (read 2026-10-03)
BL2_HEADER = SimpleNamespace(file_version=832, licensee_version=46, package_flags=0x80880009, engine_version=1712559,
                             cooker_version=134, package_source=0)


def icon_blocks(sheet: bytes) -> bytes:
    """The icon's blocks of TPS's sheet (its one mip, DXT5), in the top left corner of a texture of ICON_SIZE."""
    columns, rows = ICON_SHEET_SIZE[0] // 4, ICON_SHEET_SIZE[1] // 4
    if len(sheet) != columns * rows * DXT5_BLOCK:
        raise ExtractError(f"{ICON_SHEET}: {len(sheet)} bytes, not a {ICON_SHEET_SIZE[0]}x{ICON_SHEET_SIZE[1]} DXT5 mip")
    left, top, wide, high = ICON_BLOCKS
    out_columns, out_rows = ICON_SIZE[0] // 4, ICON_SIZE[1] // 4
    out = bytearray(out_columns * out_rows * DXT5_BLOCK)        # an all-zero block is transparent
    for row in range(high):
        start = ((top + row) * columns + left) * DXT5_BLOCK
        at = row * out_columns * DXT5_BLOCK
        out[at:at + wide * DXT5_BLOCK] = sheet[start:start + wide * DXT5_BLOCK]
    return bytes(out)


def add_icon(extraction: Extraction, game: PartialPackage) -> None:
    """TPS's cryo element icon, from the card art in its WillowGame.upk, as <TOP>.<ICON>."""
    export = next((e for e in game.exports if game.path_of(e.index) == ICON_SHEET), None)
    if export is None:
        raise ExtractError(f"{ICON_SHEET} isn't in {game.path.name}")
    game.ensure_export(export)
    values, levels = texdump.mips(game, export, [game.path.parent])
    width, height, sheet = levels[0]
    if values.get("Format") != "PF_DXT5" or (width, height) != ICON_SHEET_SIZE or sheet is None:
        raise ExtractError(f"{ICON_SHEET}: {values.get('Format')} {width}x{height}, not the sheet this was written for")
    extraction.add_texture(game, ICON_SHEET, f"{TOP}.{ICON}", [(*ICON_SIZE, icon_blocks(sheet))])


def add_textures(extraction: Extraction, sources: dict[str, Package], tfc_dirs: list[Path]) -> int:
    """TEXTURES from the TPS packages in `sources` ("startup", "game", "hers"; one not given leaves its textures out), as Crocus_IceFX.Textures.<name>."""
    added = 0
    for path, (where, name) in TEXTURES.items():
        pkg = sources.get(where)
        export = next((e for e in pkg.exports if pkg.path_of(e.index) == path), None) if pkg is not None else None
        if export is None:
            continue
        pkg.ensure_export(export) if hasattr(pkg, "ensure_export") else None
        values, probe = texdump.mips(pkg, export, [])      # the sizes and how many mips there are: no cache is read
        top, skip = max(int(values.get("SizeX", 1)), int(values.get("SizeY", 1))), 0
        while top > TEXTURE_TOP and skip < len(probe) - 1:
            top, skip = top // 2, skip + 1
        _values, levels = texdump.mips(pkg, export, tfc_dirs, skip=skip)
        kept = [lv for lv in levels if lv[2] is not None]
        if not kept or any(data is None for _sx, _sy, data in levels[len(levels) - len(kept):]):
            continue                  # a mip that can't be read: the texture is left out (its looks stay off)
        extraction.add_texture(pkg, path, f"{TOP}.Textures.{name}", kept)
        added += 1
    return added


def build(startup: Path, out: Path, game: Path | None = None, baroness: Path | None = None) -> str:
    """Write the package from TPS's Startup.upk, for the cryo icon its WillowGame.upk (beside it unless given), and
    for the cryo slam the Baroness's own package (left out when not given); returns what it holds."""
    pkg = PartialPackage.open(startup)
    extraction = Extraction(TOP, keep=KEEP)
    for path, name in SYSTEMS.items():
        extraction.add(pkg, path, f"{TOP}.Particles.{name}")
    count = len(SYSTEMS)
    if baroness is not None:
        hers = Package.load(baroness)             # an ordinary package, compressed in chunks
        for path, name in SLAM_SYSTEMS.items():
            extraction.add(hers, path, f"{TOP}.Particles.{name}")
        count += len(SLAM_SYSTEMS)
    game_pkg = PartialPackage.open(game or startup.with_name(ICON_SOURCE.name))
    add_icon(extraction, game_pkg)
    tfc_dirs = [startup.parent] + ([baroness.parents[1] / "Textures"] if baroness is not None else [])
    textures = add_textures(extraction, {"startup": pkg, "game": game_pkg, **({"hers": hers} if baroness is not None else {})}, tfc_dirs)
    report = extraction.write(out, BL2_HEADER)
    objects = sum(n for line, n in report.items() if line.startswith(("extracted", "carried")))
    meshes = sum(n for line, n in report.items() if line.startswith("carried and converted"))
    return (f"{count} particle systems, {textures} textures, {objects} objects ({meshes} meshes) from TPS's Startup.upk"
            f"{' and her package' if baroness is not None else ''}, the cryo icon from its WillowGame.upk")
