"""Runtime looks for the TPS-only materials of the cryo shot effects (aurelia's core/particles.py draws them).

The shot effects use BL2's own muzzle sprites, smoke and tracer materials wherever TPS does (those are imported with
the package and draw as they are: `OWN`). These are the few that are TPS's: first-pass procedural stand-ins in the
manner of aurelia's other ice effects, to be replaced by ports of TPS's own shaders (core/tpsports.py) later.
"""

from ..core import particles

ICY = (0.8, 0.93, 1.1)

LOOKS = {
    # The muzzle flash's soft spot (TPS: Mat_Shared_SpotAdditive). The soft-orb look (6) falls to nothing at the sprite's
    # edge; the soft-flash look (2) keeps a faint tail there, which TPS's very bright flash colours turned into a
    # visible white square around the muzzle.
    "Mat_Shared_SpotAdditive": particles.add(6, 2.0, tint=ICY),
    # its two streaks (Material_Default_MuzzleFlashSide): the cross-flare look, which fades out at the edge
    "Material_Default_MuzzleFlashSide": particles.add(3, 1.0, tint=ICY),
    # the impact's blowing snow (FX_ENV_Weather.Mat_BlowingSnow): the same puff as aurelia's snow
    "Mat_BlowingSnow": particles.tran(0, textures={"Tex0": particles.SNOW_DUST}, fade=48.0, tint=(0.95, 0.98, 1.05)),
}


def register() -> None:
    """Add the looks that aurelia's library doesn't have yet (its own entries win)."""
    for key, look in LOOKS.items():
        particles.LOOKS.setdefault(key, look)
