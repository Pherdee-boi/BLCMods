"""Cryo for Borderlands 2: The Pre-Sequel's element, on its own (RESEARCH.md §6.6-§6.8, §6.13, §6.24).

It stands on `core` alone and never imports the Baroness; she uses it through the names below (her skills deal cryo
damage and react to freezes).

- `element`: the damage types, the status and its skill; freezing, the frozen bonuses, shattering.
- `rules`, `freeze`: which enemy freezes, slows or shrugs it off, and how an enemy is held frozen.
- `attributes`, `explosions`: TPS's ice attributes and ice explosions, built at TPS's paths.
- `guns`, `gear`: cryo guns; cryo grenades, shields and relics.
- `noelement`: cryo counts as an element for BL2's skills that are for guns without one (Axton's Duty Calls).
- `switches`: cryo in what BL2's projectiles switch by element (a grenade's cloud, a rocket's lingering area).
- `frozenfx`, `frostlayer`, `cardicons`: a frozen enemy's look, and the cryo icon on item cards.
- `proxies`: that look in a joined player's game, which is never told of the status itself.

Marcus's cryo step of "Rock, Paper, Genocide" (0.3.0 to 0.6.1) is gone since 0.6.2, on the user's word: it asked for
frozen enemies killed by melee, where the lesson should have been to freeze the dummy and kill it with an explosion
(RESEARCH.md §6.85). No save ever named it (it was kept beside them, in the settings folder's aurelia_missions.json,
which nothing reads any more).

What still ties it to her build (not to her code): the ice effects' materials draw from textures her streaming package
carries (core/tpsports.py `carried`).
"""

from ..core.setup import Step
from ... import radfx  # (BL3 Weapons' own module: it runs on Aurelia's core too)
from . import (attributes, cardicons, element, explosions, freeze, frostlayer, frozenfx, gear, guns, noelement, proxies,
               rules, switches)
from .element import (AFTER_DAMAGE, BONUS_CAP, FROZEN_CRIT_BONUS, FROZEN_EXPLOSIVE, FROZEN_MELEE, IMMUNE, IMPACT,
                      IMPACT_NO_FREEZE, LOOK, ON_FREEZE, ON_FROZEN_KILL, ON_PLAYER_FROZEN, ON_SHATTER, ON_THAW, SKILL,
                      SLOW_FACTOR, STATUS, STATUS_DAMAGE, Hit, apply_status, cap_option, chill, ensure_objects, hit,
                      is_cryo_damage, is_frozen, recent_cryo_kill, state, thaw)

# Hooks on the same game function run in the order listed; this is the order they had before the split.
HOOKS = (*freeze.HOOKS, *element.HOOKS, *guns.HOOKS, *gear.HOOKS, *frozenfx.HOOKS, *cardicons.HOOKS, *noelement.HOOKS,
         *proxies.HOOKS, *radfx.HOOKS)
OPTIONS = (guns.drops_option, *element.OPTIONS, guns.enemy_option)
STEPS = (
    # at TPS's paths, before any converted package loads: its imports resolve to them
    Step("objects", "cryo objects", element.ensure_objects),
    Step("objects", "ice attributes", attributes.ensure_attributes),
    Step("objects", "ice explosions", explosions.ensure_effects),
    # TPS's own effects, when the build carries them (looks only)
    Step("looks", "radiation effects (bl3_weapons)", radfx.load),
    Step("looks", "TPS's frozen-enemy effects", frozenfx.load),
    Step("looks", "TPS's ice explosion particles", explosions.dress),
    Step("looks", "cryo in BL2's element switches", switches.apply),    # after her ice effects: the cloud's spray
    Step("looks", "cryo icon on item cards", cardicons.apply),      # before a map makes the game read the card movie
    # saved cryo guns and gear need their parts registered before the save is read
    Step("items", "cryo guns", guns.register, guarded=False),
    Step("items", "cryo gear", gear.register, guarded=False),
)

__all__ = ["AFTER_DAMAGE", "BONUS_CAP", "FROZEN_CRIT_BONUS", "FROZEN_EXPLOSIVE", "FROZEN_MELEE", "HOOKS", "Hit",
           "IMMUNE", "IMPACT", "IMPACT_NO_FREEZE", "LOOK", "ON_FREEZE", "ON_FROZEN_KILL", "ON_PLAYER_FROZEN",
           "ON_SHATTER", "ON_THAW", "OPTIONS", "SKILL", "SLOW_FACTOR", "STATUS", "STATUS_DAMAGE", "STEPS",
           "apply_status", "attributes", "cap_option", "cardicons", "chill", "element", "ensure_objects", "explosions",
           "freeze", "frostlayer", "frozenfx", "gear", "guns", "hit", "is_cryo_damage", "is_frozen", "noelement",
           "proxies", "recent_cryo_kill", "rules", "state", "switches", "thaw"]
