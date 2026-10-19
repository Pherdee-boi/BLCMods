"""The radiation element's particle systems for BL2: RadiationFX_SF.upk, cut from BL2's own Startup.upk.

BL2 draws a particle system through its PhysX variant (`PhysxParticleSystemRef`): the plain slag explosion systems are never drawn themselves. The
NOFLD variants (no fluid emitter, no PhysX link) are drawn as they are, so copies of those are what is carried, into a package of their own
(upkextract.py) under RadiationFX. Nothing of the game is edited at run time any more: the mod dresses these copies (colours, sizes, materials of
its own, cryo/radfx.py), and spawns them where it needs the effect.

Nothing of the game is stored with the mod: this runs over the player's own install (BL2's Startup.upk), the way the ice package is cut from TPS's.

    python radfx.py <BL2 root> <out.upk>
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from findexport import PartialPackage
from upkextract import Extraction

PACKAGE = "RadiationFX_SF.upk"
TOP = "RadiationFX"
SOURCE = Path("WillowGame/CookedPCConsole/Startup.upk")
SYSTEMS = {f"FX_WEP_Explosions_Fluid_Px.Particles.NOFLD.Part_WEP_SlagExplosion{size}": f"Part_WEP_SlagExplosion{size}"
           for size in ("Small", "Medium", "Large")}
# The radiation gun's bullet impact: corrosive's, without its PhysX fluid (that variant has no fluid emitter and no PhysX link); in BL2's WillowGame.upk
GAME_SOURCE = Path("WillowGame/CookedPCConsole/WillowGame.upk")
IMPACT_SYSTEMS = {"FX_Bullet_Impacts_Elem_Fld_Px.Particles.corrosive.Part_Corrosive_ImpactFX_Nofld": "Part_Corrosive_ImpactFX_Nofld"}
# The shot effects of corrosive guns (BL2's muzzle flashes, tracers and trails for corrosive, from Startup.upk), carried as they are: cryo/radfx.py turns the
# copies yellow and adds them to BL2's effect lists for the radiation element (E-tech guns shoot purple without it).
SHOT_SYSTEMS = {
    "FX_WEP_Alien.Particles.Part_AR_Alien_Corrosive_Muzzleflash": "Part_AR_Alien_Muzzle",
    "FX_WEP_Alien.Particles.Part_Pistol_Alien_Corrosive_MuzzleFlash": "Part_Pistol_Alien_Muzzle",
    "FX_WEP_Alien.Particles.Part_Shotgun_Alien_Corrosive_Muzzleflash": "Part_Shotgun_Alien_Muzzle",
    "FX_WEP_Alien.Particles.Part_SMG_Alien_Corrosive_Muzzleflash": "Part_SMG_Alien_Muzzle",
    "FX_WEP_Alien.Particles.Part_Snipe_Alien_Corrosive_Muzzleflash": "Part_Snipe_Alien_Muzzle",
    "FX_WEP_Assault.Particles.Part_Assault_MuzzleFlash_Corrosive": "Part_AR_Muzzle",
    "FX_WEP_Launchers.Particles.Part_Launcher_Muzzle_Corrosive": "Part_RL_Muzzle",
    "FX_WEP_Shotgun.Particles.Part_Shotgun_MuzzleFlash_Corrosive": "Part_Shotgun_Muzzle",
    "FX_WEP_SMG.Particles.Part_SMG_MuzzleFlash_Corrosive": "Part_SMG_Muzzle",
    "FX_WEP_Sniper.Particles.Part_Sniper_MuzzleFlash_Corrosive": "Part_Sniper_Muzzle",
    "FX_WEP_Trails.Particles.Part_Wep_Tracer_Corrosive": "Part_Tracer",
    "FX_WEP_Trails.Particles.RL.Part_Rocket_Trail_Corrosive": "Part_Rocket_Trail",
    "FX_WEP_Trails_Alien.Particles.Part_AR_Alien_Corrosive_Tracer": "Part_AR_Alien_Tracer",
    "FX_WEP_Trails_Alien.Particles.Part_Pistol_Alien_Corrosive_Tracer": "Part_Pistol_Alien_Tracer",
    "FX_WEP_Trails_Alien.Particles.Part_Pistol_Homing_Corrosive_Tracer": "Part_Pistol_Homing_Tracer",
    "FX_WEP_Trails_Alien.Particles.Part_Shotgun_Alien_Corrosive_Tracer": "Part_Shotgun_Alien_Tracer",
    "FX_WEP_Trails_Alien.Particles.Part_SMG_Alien_Corrosive_Tracer": "Part_SMG_Alien_Tracer",
    "FX_WEP_Trails_Alien.Particles.Part_Sniper_Alien_Corrosive_Tracer": "Part_Sniper_Alien_Tracer",
    "FX_WEP_Grenades.Particles.Part_InFlight_Corrosive": "Part_InFlight",                                 # a radiation grenade's trail
    "FX_CHAR_Shared_Shield.Particles.SpikeShields.Part_Shield_Spike_Corrosive": "Part_Shield_Spike",      # a radiation spike shield's spikes
    "FX_WEP_Explosions.Particles.Novas.Part_Corrosive_Nova_Maliwan_Explosion": "Part_Nova_Sphere",         # the sphere of the Maliwan nova explosion, for the weapons' explosions
}
# BL2's own objects the copies refer to (its Startup.upk has them): imported, not carried
KEEP = frozenset({"FX_IO_StalkerSacs.Materials.Mat_Trans_Smoke",
                  "FX_WEP_Explosions.Materials.Mat_SmokeSlagFlipBook",
                  "FX_WEP_Explosions.Materials.Mat_SlagSmokeVariant2",
                  "FX_WEP_Explosions.Materials.Mati_ExplosionGlowMODDepthBias",
                  "FX_Shared_Explosions.Materials.Mati_Distortion_Ring_2_Add",
                  "FX_WEP_Explosions.Materials.Mat_SlagExplosionSmoke",
                  "EngineMaterials.DefaultParticle",
                  "FX_Shared_Smoke.Materials.Mati_Smoke_Generic_SubUV",
                  "FX_WEP_Explosions.Materials.Mati_Explosive_Spikes",
                  "FX_Shared_Dirt.Materials.Mati_Dirtkick_NoWorldLight",
                  "FX_WEP_Explosions.Materials.Mat_AcidSprayGoop"})
# what the carried shot effects draw with (their materials and meshes): BL2's own, in its Startup.upk; without them the copies drew nothing or a flat square
SHOT_KEEP = frozenset({"FX_BulletImpacts.Materials.Instance.Mati_Energy_Rings",
                         "FX_CHAR_Generic.Materials.Mat_Sphere",
                         "FX_CREA_Skag.Materials.Mat_Lightning_Horizontal",
                         "FX_CREA_Skag.Materials.Mat_Skag_Bile",
                         "FX_CREA_Skag.Materials.Mat_Skag_ElecGround",
                         "FX_ENV_Smoke.Materials.Mati_WispySmokeCloud_NoWorldLight",
                         "FX_ENV_Smoke.Materials.Mati_Wispy_Smoke_Cloud_SubUV",
                         "FX_Shared_Mesh.Meshes.Planes.DecalPlane_LiquidDev",
                         "FX_WEP_Alien.Materials.BlasterRing_Geo",
                         "FX_WEP_Alien.Materials.BlasterRing_Geo_Desat",
                         "FX_WEP_Alien.Materials.Muzzle_FlashHider_INST",
                         "FX_WEP_Assault.Materials.MS.Mati_Glow_Modulate_Angle_Fade",
                         "FX_WEP_Assault.Materials.Slag.Mati_Slag_Black_Goo",
                         "FX_WEP_Assault.Materials._Default.Mati_MuzzFlash_Assault_Front_Desat",
                         "FX_WEP_Assault.Materials._Default.Mati_MuzzFlash_Assault_Side",
                         "FX_WEP_Assault.Materials.corrosive.Mati_MuzzFlash_Assault_Front_Corrosive",
                         "FX_WEP_Assault.Materials.corrosive.MuzzFlash_Assault_Corrosive_Front",
                         "FX_WEP_Assault.Materials.corrosive.MuzzFlash_Assault_Corrosive_Side",
                         "FX_WEP_Assault.Materials.shock.Mati_MuzzFlash_Assault_Shock_Front",
                         "FX_WEP_Explosions.Materials.Mat_SeraEnergyBlast",
                         "FX_WEP_Grenades.Materials.Mat_VertexColorOnlySparkle",
                         "FX_WEP_SMG.Materials._Default.Mati_MuzzFlash_SMG_Front",
                         "FX_WEP_SMG.Materials.corrosive.Mati_MuzzFlash_SMG_Corrosive_Front",
                         "FX_WEP_SMG.Materials.corrosive.Mati_MuzzFlash_SMG_Corrosive_Side",
                         "FX_WEP_SMG.Materials.shock.Mati_MuzzFlash_SMG_Shock_Side",
                         "FX_WEP_Shotgun.Materials.Instances.Mati_Corrosive_MuzzleFlash",
                         "FX_WEP_Shotgun.Materials.Instances.Mati_MuzzFlash_Shotgun_Front_Corrosive",
                         "FX_WEP_Sniper.Materials.Mat_Weapon_Smoke",
                         "FX_WEP_SoldierTurret.Materials.Material_Default_MuzzleFlashSide",
                         "FX_WEP_Trails.Materials.Instances.Default.Mati_Dark_Bullet_Tracer",
                         "FX_WEP_Trails.Materials.Instances.Default.Mati_Default_Bullet_Tracer",
                         "FX_WEP_Trails.Materials.Instances.Default.Mati_Default_Bullet_Trail",
                         "FX_WEP_Trails.Materials.Instances.corrosive.Mati_Corrosive_Bullet_Tracer",
                         "FX_WEP_Trails.Materials.Instances.corrosive.Mati_Corrosive_Tracer",
                         "FX_WEP_Trails.Materials.Mat_PrasmaDistortion",
                         "FX_WEP_Trails.Materials.Mat_Prasma_Base",
                         "FX_WEP_Trails.Materials.Mat_Rocketglow",
                         "FX_WEP_Trails.Meshes.Mesh_Alien_SMG_Bolt",
                         "FX_WEP_Trails.Meshes.Mesh_Alien_Tracer",
                         "FX_WEP_Trails_Alien.Materials.Instances.Mati_Base_Color",
                         "FX_WEP_Trails_Alien.Materials.Mat_BlasterTrail_INST_Green",
                         "FX_WEP_Trails_Alien.Materials.Mati_Alien_Sniper_Corrosive",
                         "FX_WEP_Trails_Alien.Materials.Mati_Orb_Core_Desat",
                         "Ake_FX_Player_General.Shields.Ak_Play_FX_Player_Shield_Spike_Corrosive",
                         "Ake_Wep_Grenades.Ak_Play_Wep_Grenade_Projectile_Acid_Start",
                         "Ake_Wep_Grenades.Ak_Play_Wep_Grenade_Projectile_Acid_Stop",
                         "FX_CHAR_Siren.Materials.Mat_SirenHandGlow",
                         "FX_CREA_EXPLoader.Materials.Glow_Modulate_Inst",
                         "FX_Shared_Smoke.Materials.Mat_Core_Smoke",
                         "FX_Shared_Water.Materials.Moved.WaterSpring_Trans_90deg_Mati",
                         "Ake_FX_Player_General.Shields.Ak_Play_FX_Player_Shield_Spike_Fire",
                         "FX_Brick.Materials.Mati_Impact_Brick_RagePunch",
                         "FX_CHAR_Siren.Materials.Mat_Stagnant_Buildup",
                         "FX_Fast_Travel.Mesh.Mesh_FastTravel",
                         "FX_WEP_Explosions_px.Particles.Novas.Part_Corrosive_Nova_Maliwan_Explosion_Px"})
BL2_HEADER = SimpleNamespace(file_version=832, licensee_version=46, package_flags=0x80880009, engine_version=1712559,
                             cooker_version=134, package_source=0)


def build(startup: Path, out: Path, game: Path | None = None) -> str:
    """Write the package from BL2's Startup.upk (and the bullet impact from its WillowGame.upk, beside it unless given); returns what it holds."""
    pkg = PartialPackage.open(startup)
    extraction = Extraction(TOP, keep=KEEP | SHOT_KEEP)
    for path, name in SYSTEMS.items():
        extraction.add(pkg, path, f"{TOP}.Particles.{name}")
    for path, name in SHOT_SYSTEMS.items():
        extraction.add(pkg, path, f"{TOP}.Shots.{name}")
    for path, name in SYSTEMS.items():     # second copies of the gas blasts, for the weapon explosions (BL2's plain explosion and rays are put on these at run time)
        if "Large" not in path:
            extraction.add(pkg, path, f"{TOP}.Blast.{name}")
    game_pkg = PartialPackage.open(game or startup.with_name(GAME_SOURCE.name))
    for path, name in IMPACT_SYSTEMS.items():
        extraction.add(game_pkg, path, f"{TOP}.Particles.{name}")
    report = extraction.write(out, BL2_HEADER)
    objects = sum(n for line, n in report.items() if line.startswith(("extracted", "carried")))
    return f"{len(SYSTEMS) + len(IMPACT_SYSTEMS) + len(SHOT_SYSTEMS)} particle systems, {objects} objects from BL2's Startup.upk and WillowGame.upk"


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) / SOURCE, Path(sys.argv[2])))
