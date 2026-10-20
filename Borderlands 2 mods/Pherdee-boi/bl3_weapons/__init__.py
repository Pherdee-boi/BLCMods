"""BL3 weapon mechanics for Borderlands 2.

One "weapon mode" key (default B) triggers the held gun's BL3 manufacturer feature: Maliwan guns swap to their second element, Vladof assault rifles deploy an underbarrel launcher and Torgue guns toggle impact/sticky gyrojets and Dahl guns cycle fire modes. Tediore guns roll a BL3 reload throw (turret, MIRV, chaser, bouncing). Bandit guns behave like BL3's Children of Vladof: no reloads, but they overheat. Jakobs critical hits ricochet to a nearby enemy. Aiming a Hyperion gun raises a frontal gun shield. Rare and better guns can roll a BL3 anointment. Relics roll BL3 artifact perks, and snipers can roll close-range sights from shotguns, rifles, pistols and SMGs. The mod has its own legendary: the HeartShatter, a slow, hard-hitting Vladof sniper with a Dahl pistol sight and a bipod that drops in the world in different forms and is always cryo (its cryo element is The Pre-Sequel's, built into this mod from your own copy of that game, after the Aurelia mod by Grim Reader). Radiation is a new element after Borderlands 3's: a damage over time whose enemy hurts the ones around it and explodes when it dies, on guns that drop in the world (and Maliwan guns can switch to it).
"""

import traceback
from pathlib import Path

import unrealsdk
from mods_base import build_mod, keybind
from ui_utils import show_hud_message

from . import cryo, anoint, backups, bandit, beams, card, gear, dahl, elementicons, heartshatter, hyperion, indicator, jakobs, maliwan, radgear, radiation, relics, sights, testgear, tediore, torgue, vladof
from .core import active_maker_of, addons, clock_tick, held_weapon, maker_of, state

# Test buttons, the F5-F7 test keys and the exec.now.py trigger exist only where a file named dev.flag sits in this folder (a release ships without it).
DEV = (Path(__file__).parent / "dev.flag").exists()

FEATURES = {"Maliwan": maliwan.switch, "Vladof": vladof.switch, "Torgue": torgue.switch, "Dahl": dahl.switch, "Tediore": tediore.switch}
# Makers whose BL3 trait is passive: the mode key explains it instead of "no mode" (v0.34.1, user)
PASSIVE = {
    "Hyperion": ("Hyperion Shield", "Aim down sights to raise an energy shield that blocks damage from the front."),
    "Jakobs": ("Jakobs Ricochet", "Critical hits ricochet a bullet to the nearest enemy."),
    "Bandit": ("COV Overheat", "Keep firing until it overheats, then let it cool down - or reload (1 reserve round) to vent it at once when it's hot."),
}


@keybind("Weapon Mode", "B", description="BL3 alt mode for the held gun (Maliwan element, Vladof underbarrel, Torgue sticky, Dahl fire mode, Tediore throw info).")
def weapon_mode() -> None:
    try:
        weapon = held_weapon()
        if weapon is None:
            return
        if maker_of(weapon) and not active_maker_of(weapon):
            show_hud_message(f"{maker_of(weapon)} Add-on", "Switched off in the Mods menu.", 1.5)
            return
        feature = FEATURES.get(maker_of(weapon))
        if feature is None and maker_of(weapon) in PASSIVE:
            title, text = PASSIVE[maker_of(weapon)]
            show_hud_message(title, text, 2.5)
            return
        if feature is None:
            show_hud_message("Weapon Mode", f"{maker_of(weapon) or 'This'} guns have no BL3 mode yet.", 1.5)
            return
        feature(weapon)
    except Exception:
        unrealsdk.logging.error("[BL3Weapons] " + traceback.format_exc())


# Switching an add-on off in the menu puts back what it had changed on the gun in hand (a deployed underbarrel, a sticky mode, a thrown reload...).
_SWITCH_OFF = {"Vladof": vladof.reset, "Torgue": torgue.restore, "Dahl": dahl.reset, "Tediore": tediore.restore, "Bandit": bandit.restore_ammo, "Hyperion": hyperion.reset}
for _maker, _undo in _SWITCH_OFF.items():
    addons[_maker].on_change_anytime = lambda _option, value, undo=_undo: None if value else undo()

mod = build_mod(
    name="BL3 Weapons",
    description=__doc__.split("\n\n")[1],
    options=[*addons.values(), anoint.enabled, *vladof.OPTIONS, *dahl.OPTIONS, *bandit.OPTIONS, jakobs.ricochet_pct, jakobs.show_tracer, *hyperion.OPTIONS, *indicator.OPTIONS, *torgue.OPTIONS, *card.OPTIONS, *beams.OPTIONS, *sights.OPTIONS, *gear.OPTIONS, *tediore.OPTIONS, *heartshatter.OPTIONS, *radiation.OPTIONS, *radgear.OPTIONS, *([anoint.test_anoint, *testgear.OPTIONS] if DEV else []), *cryo.PARTS["options"], state],
    keybinds=[weapon_mode, anoint.show_anointment, *([*heartshatter.KEYBINDS, *radiation.KEYBINDS] if DEV else [])],
    commands=[heartshatter.command_heartshatter, radiation.command_radiation, *cryo.PARTS["commands"]],
    hooks=[clock_tick, *vladof.HOOKS, *bandit.HOOKS, *anoint.HOOKS, *torgue.HOOKS, *jakobs.HOOKS, *hyperion.HOOKS, *dahl.HOOKS, *tediore.HOOKS, *indicator.HOOKS, *card.HOOKS, *beams.HOOKS, *sights.HOOKS, *gear.HOOKS, *relics.HOOKS, *heartshatter.HOOKS, *radiation.HOOKS, *elementicons.HOOKS, *backups.HOOKS, *radgear.HOOKS, *cryo.PARTS["hooks"]],
    on_disable=lambda: (vladof.reset(), beams.reset(), dahl.reset(), tediore.restore(), bandit.restore_ammo(), hyperion.reset(), indicator.reset(), torgue.restore(), relics.reset(), sights.reset(), radiation.reset(), (cryo.PARTS["on_disable"] or (lambda: None))()),
    on_enable=lambda: (radiation.safe_build(), sights._patch_safe(), (cryo.PARTS["on_enable"] or (lambda: None))()),
)

# mods_base only enables a mod that was enabled last session, and a fresh install has no settings file yet: enable it once here.
# A player who disables it in the MODS menu stays disabled, because the settings file then exists.
if mod.settings_file is not None and not mod.settings_file.exists() and not mod.is_enabled:
    mod.enable()
