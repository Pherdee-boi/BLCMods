"""BL3 movement for Borderlands 2.

Sprint + crouch to slide with BL3-style momentum, jump out of a slide to keep it, and crouch in mid-air to ground slam.
"""

from mods_base import build_mod

from . import arms, slam, slide

build_mod(
    name="BL3 Movement",
    description=__doc__.split("\n\n")[1],
    options=[slide.speed_option, slide.length_option, slide.sprint_after, slam.damage_option, *arms.OPTIONS],
    hooks=[*slide.HOOKS, *slam.HOOKS],
    on_disable=lambda: (slide.reset(), slam.reset()),
)
