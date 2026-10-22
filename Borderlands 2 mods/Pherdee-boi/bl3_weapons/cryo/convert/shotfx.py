"""Build CryoShotFX_SF.upk: TPS's cryo shot effects (muzzle flashes, tracer, rocket trail, bullet impact and first-person glows), cut out of TPS's own
Startup.upk and WillowGame.upk the way icefx.py cuts the ice effects (the same Extraction). Nothing of TPS is stored: this runs over the player's own install."""
from __future__ import annotations

from pathlib import Path

from findexport import PartialPackage
from icefx import BL2_HEADER
from upkextract import Extraction

PACKAGE = "CryoShotFX_SF.upk"
TOP = "CryoShotFX"
SOURCE = Path("WillowGame/CookedPCConsole/Startup.upk")
GAME_SOURCE = Path("WillowGame/CookedPCConsole/WillowGame.upk")
FLASHES = {f"FX_CO_Wep_Ice.Particles.MuzzleFlashes.Part_{kind}_MuzzleFlash_Ice": f"Part_{kind}_MuzzleFlash_Ice"
           for kind in ("Assault", "Pistol", "RL", "SG", "SMG", "SR")}
GLOWS = {f"FX_CO_Wep_Ice.Particles.Part_Ice_Elemental_{kind}": f"Part_Ice_Elemental_{kind}"
         for kind in ("AR", "Pistol", "SG", "SMG", "Sniper")}
SHOTS = {f"FX_CO_Wep_Ice.Particles.{name}": name for name in ("Part_Wep_Tracer_Ice", "Part_Ice_Trail_Fast", "Part_InFlight_Ice")}
SYSTEMS = {**FLASHES, **GLOWS, **SHOTS}
GAME_SYSTEMS = {"FX_CO_Wep_Ice.Particles.Part_Ice_ImpactFX": "Part_Ice_ImpactFX"}     # the bullet impact is in TPS's WillowGame.upk
# Objects BL2 has in its always-loaded Startup.upk / WillowGame.upk: imported, not carried
KEEP = frozenset({"Ake_Wep_Grenades.Ak_Play_Wep_Grenade_Projectile_Fire_Start",
                  "Ake_Wep_Grenades.Ak_Play_Wep_Grenade_Projectile_Fire_Stop",
                  "FX_CHAR_Damage_Matrix.Materials.Mat_Char_Damage_Corrosive_Bubble",
                  "FX_CREA_Skag.Materials.Mat_Dark_Slag_Smoke",
                  "FX_ENV_Fire.Materials.Mat_SubUV_2X2_LerpSmoke",
                  "FX_ENV_Smoke.Materials.Mati_Wispy_Smoke_Cloud_SubUV",
                  "FX_IO_Barrels.Materials.Mat_FX_Barrel_Shock2_Sparks",
                  "FX_Shared_Energy.Materials.Material_EMPShock",
                  "FX_Shared_Sparks.Materials.Mati_Spark_Generic_2",
                  "FX_WEP_Assault.Materials.MS.Mati_Glow_Modulate_Angle_Fade",
                  "FX_WEP_Assault.Materials._Default.Mati_MuzzFlash_Assault_Front",
                  "FX_WEP_Assault.Materials._Default.Mati_MuzzFlash_Assault_Side",
                  "FX_WEP_Elemental.Materials.Mat_Lightning_Vertical_NoBias",
                  "FX_WEP_Explosions.Materials.Mati_Explosive_Spikes",
                  "FX_WEP_Sniper.Materials.Mat_Weapon_Smoke",
                  "FX_WEP_Trails.Materials.Instances.Default.Mati_Default_Bullet_Trail",
                  "FX_WEP_Trails.Materials.Instances.corrosive.Mati_Corrosive_Bullet_Tracer",
                  "FX_WEP_Trails.Materials.Instances.shock.Mati_Shock_Tracer",
                  "FX_WEP_Trails.Materials.Mat_Rocketglow"})


def build(startup: Path, out: Path, game: Path | None = None) -> str:
    """Write the package from TPS's Startup.upk and WillowGame.upk (beside it unless given); returns what it holds."""
    extraction = Extraction(TOP, keep=KEEP)
    for source, systems in ((startup, SYSTEMS), (game or startup.with_name(GAME_SOURCE.name), GAME_SYSTEMS)):
        pkg = PartialPackage.open(source)
        for path, name in systems.items():
            extraction.add(pkg, path, f"{TOP}.Particles.{name}")
    report = extraction.write(out, BL2_HEADER)
    objects = sum(n for line, n in report.items() if line.startswith(("extracted", "carried")))
    return f"{len(SYSTEMS) + len(GAME_SYSTEMS)} particle systems, {objects} objects from TPS's Startup.upk and WillowGame.upk"
