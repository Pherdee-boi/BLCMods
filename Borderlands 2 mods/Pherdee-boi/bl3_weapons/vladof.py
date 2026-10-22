"""Vladof: assault rifles, pistols, sniper rifles and rocket launchers get a BL3-style underbarrel; the mode key deploys/stows it.

Each gun rolls its underbarrel once (saved per UniqueId). A deploy gives one load of underbarrel shots
(the fire-rate boost lasts a few seconds instead). Emptying it or reloading auto-stows it and starts a
cooldown; stowing by hand starts a cooldown scaled by how much was used.

  * Grenade Launcher (ARs only): BL2's own Torgue grenade barrel for ARs (AR_Barrel_Torgue_Dahl), a real
    part, so a save taken mid-switch is still a valid gun.
  * Every other underbarrel is a "mode" on the gun's own barrel: attributes held every frame (BL2
    recomputes attributes on zoom etc.; bases are re-read with a relative tolerance), restored on stow,
    including when the gun is put away or the mod is turned off.
      - Rocket Launcher: WillowWeapon.GetFiringModeDefinition returns BL2's real Vladof launcher mode
        (GD_Weap_Launchers.FiringModes.FM_Rocket_Vladof), slow single shots.
      - Shotgun: ProjectilesPerShot 8, Spread 7.0, kick 12 (BL2's WT_Jakobs_Shotgun: 7 / 7.4 / 11), pump.
      - Taser (v0.26.0, BL3): 2 bullets per pull that always shock: the gun takes its type's shock element
        part while deployed (the original is saved, so a save mid-deploy is put right on the next hold)
        and StatusEffectChanceModifier is raised.
      - Double Barrel (v0.26.0 as "Fire Rate", renamed v0.27.0 after BL3): FireInterval halved for a few
        seconds; uses the gun's own ammo. Saved "Fire Rate" rolls are renamed on first use.
      - Bipod (v0.28.0, BL3, snipers): a stance toggled with the mode key: Spread x0.3, kick x0.3, fire rate
        x1.5, and the player is nearly rooted (Pawn.GroundSpeed held at 15%, WillowPlayerController.BeginSprint
        refused). Gun's own ammo, no shot count, no cooldown; stows when the gun is put away.
      - Zip Rockets (v0.26.2, BL3, pistols): the small AR rockets (GD_Weap_AssaultRifle.FiringModes.
        FM_Rocket_Vladof), automatic at 4 per second.
      - Launchers (BL3), all toggled with the mode key like the bipod, none with a cooldown:
          Rocket Salvo: twice the launcher's own rockets per shot at a high fire rate (so its explosion, element and special effect, the Mongol's
          or an E-tech's), a shot costing 1 round whatever the gun's own shot cost, out of the current magazine.
          Big-Boy Rocket: not a toggle: deploying it loads one big rocket (Torgue's Nukem mode; a Mongol or E-tech keeps its own rocket), two to three
          times a normal rocket's damage (rolled per shot) with a bigger explosion, out of the reserve ammo. The shot stows it again, so the gun is back to
          its normal rockets at once (the magazine as it was), and the underbarrel is on a cooldown (a slider).
          Mortar: the cursor is where the aim meets a surface (WillowWeapon.GetTargetDistance); a pull fires a burst of
          four shells, one round each from the current magazine, each launched on a lob that lands on the cursor.
  * Own ammo (v0.23.0): the reserve (GetAmmoCount) is noted on deploy and what the underbarrel spent is
    given back on stow (AddAmmo); the gun's magazine is restored too. Not for the fire-rate boost.
  * Damage: a multiplier per underbarrel, applied in anoint.scale_damage (the mod's single damage hook).
"""

import copy
import math
import random

import unrealsdk
from mods_base import BoolOption, DropdownOption, GroupedOption, SliderOption, hook
from ui_utils import show_hud_message
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import UObject, WeakPointer

from .core import find_part, forget, held_weapon, log, active_maker_of as maker_of, now as game_now, pawns, rebuild, remembered, state

AR_BARRELS = "GD_Weap_AssaultRifle.Barrel."
SHOCK_PART = {
    "AR": "GD_Weap_AssaultRifle.elemental.AR_Elemental_Shock",
    "Pistol": "GD_Weap_Pistol.elemental.Pistol_Elemental_Shock",
}
# name shown on the HUD -> barrel part (ARs) or None for a mode on the gun's own barrel
UNDERBARRELS = {
    "Rocket Launcher": None,
    "Grenade Launcher": AR_BARRELS + "AR_Barrel_Torgue_Dahl",  # FM_Rocket_Dahl_Grenade
    "Shotgun": None,
    "Taser": None,
    "Double Barrel": None,
    "Bipod": None,
    "Zip Rockets": None,
    "Rocket Salvo": None,
    "Big-Boy Rocket": None,
    "Mortar": None,
}
KINDS = {  # which underbarrels each gun type can roll (v0.26.1, user: taser = pistols only, grenade = ARs
    # only, rocket = ARs and later snipers). A saved roll that's no longer allowed is re-rolled.
    "AR": ["Rocket Launcher", "Grenade Launcher", "Shotgun", "Double Barrel"],
    "Pistol": ["Zip Rockets", "Taser", "Double Barrel"],  # v0.26.2: zip rockets replace the pistol shotgun
    "Sniper": ["Rocket Launcher", "Shotgun", "Double Barrel", "Bipod"],  # v0.27.0, Bipod v0.28.0
    "Launcher": ["Rocket Salvo", "Big-Boy Rocket", "Mortar"],
}
MODES = {
    "Shotgun": {"attrs": {"ProjectilesPerShot": 8, "Spread": 7.0, "PerShotAccuracyImpulse": 12.0}, "interval": 0.55, "semi": True},
    "Rocket Launcher": {"attrs": {"ProjectilesPerShot": 1, "PerShotAccuracyImpulse": 20.0}, "interval": 1.1, "semi": True,
                        "fm": "GD_Weap_Launchers.FiringModes.FM_Rocket_Vladof"},
    "Taser": {"attrs": {"ProjectilesPerShot": 2}, "interval": 0.5, "semi": True, "shock": True},
    "Double Barrel": {"attrs": {}, "rate": 2.0, "timed": True},
    "Bipod": {"attrs": {}, "scale": {"Spread": 0.3, "PerShotAccuracyImpulse": 0.3}, "rate": 1.5, "toggle": True, "stance": True},
    # BL3 zip rockets: the small Vladof AR rockets (the AR Torgue-Vladof barrel's firing mode, which ARs
    # used as their rocket underbarrel up to v0.24.0), automatic, on the pistol's own barrel
    "Zip Rockets": {"attrs": {"ProjectilesPerShot": 1}, "interval": 0.25,
                    "fm": "GD_Weap_AssaultRifle.FiringModes.FM_Rocket_Vladof"},
}
# BL3's launcher underbarrels (toggles: no shots of their own, no cooldown; "stance" is the bipod's pose, speed and sounds only).
MODES["Rocket Salvo"] = {"attrs": {"ShotCost": 1, "PerShotAccuracyImpulse": 8.0}, "scale": {"ProjectilesPerShot": 2}, "rate": 2.6, "toggle": True}  # (the gun's own rocket: twice
# as many a shot, with the explosion (radius 250 against the AR mini-rocket's 90), element and special effect of the launcher it is on)
MODES["Big-Boy Rocket"] = {"attrs": {"ShotCost": 1, "PerShotAccuracyImpulse": 30.0}, "interval": 1.0, "semi": True, "one_shot": True,
                           "fm": "GD_Weap_Launchers.FiringModes.FM_Rocket_Torgue_Nukem", "fm_if_standard": True}  # (a special launcher, the Mongol or an E-tech, keeps its own rocket)
STANDARD_FM = "GD_Weap_Launchers.FiringModes.FM_Rocket_Vladof"  # a launcher with this firing mode is a plain one
MODES["Mortar"] = {"attrs": {"ProjectilesPerShot": 1, "PerShotAccuracyImpulse": 15.0}, "rate": 4.0, "toggle": True, "burst": 4, "arc": True,
                   "fm": "GD_Weap_AssaultRifle.FiringModes.FM_Rocket_Dahl_Grenade", "shell": "Dahl_Grenade"}
BIG_BOY_LOW = 2 / 3  # a Big-Boy rocket's damage is rolled between this share of the slider and all of it
MORTAR_BURST_GAP = 0.6  # seconds without a shot: the next one starts a new burst
MORTAR_G = 1011.0  # a shell's own gravity in uu/s^2 (measured on a Dahl grenade: 809 at its 0.8 gravity scale)
PISTOL_SHOTS = 2 / 3  # pistols get fewer underbarrel shots
BIPOD_SPEED = 0.15  # walk speed while the bipod is out

grenade_damage = SliderOption(
    "Grenade Launcher Damage", 250, 50, 600, 10, True,
    description="Vladof underbarrel grenade damage, percent of the BL2 launcher barrel's.",
)
rocket_damage = SliderOption(
    "Rocket Launcher Damage", 1000, 50, 2000, 25, True,
    description="Vladof underbarrel rocket damage, percent of the gun's own damage (a launcher rocket scales from it).",
)
rocket_shots = SliderOption("Rocket Launcher Shots", 3, 1, 8, 1, True, description="Rockets per underbarrel deploy.")
shotgun_damage = SliderOption(
    "Shotgun Damage", 100, 30, 300, 10, True,
    description="Vladof underbarrel shotgun pellet damage, percent of the gun's bullet.",
)
shotgun_shots = SliderOption("Shotgun Shots", 6, 2, 12, 1, True, description="Shotgun shells per underbarrel deploy.")
taser_damage = SliderOption(
    "Taser Damage", 70, 20, 300, 10, True,
    description="Vladof underbarrel taser bullet damage, percent of the gun's bullet (the shock damage over time comes on top).",
)
taser_shots = SliderOption("Taser Shots", 6, 2, 12, 1, True, description="Taser shots per underbarrel deploy.")
zip_damage = SliderOption(
    "Zip Rocket Damage", 260, 50, 1000, 10, True,
    description="Vladof pistol zip rocket damage, percent of the gun's own damage.",
)
zip_shots = SliderOption("Zip Rocket Shots", 12, 2, 16, 1, True, description="Zip rockets per underbarrel deploy (pistols).")
salvo_damage = SliderOption(
    "Rocket Salvo Damage", 55, 10, 300, 5, True,
    description="Vladof launcher rocket salvo: damage of each mini-rocket, percent of the gun's own damage.",
)
bigboy_cooldown = SliderOption(
    "Big-Boy Rocket Cooldown", 8, 2, 30, 1, True,
    description="Seconds after a Big-Boy rocket before it can be deployed again (it is one rocket, out of the reserve ammo, and the gun goes back to its normal rockets).",
)
bigboy_damage = SliderOption(
    "Big-Boy Rocket Damage", 300, 100, 800, 10, True,
    description="Vladof launcher Big-Boy rocket: the most damage it can roll, percent of a normal rocket (it rolls between two thirds of this and all of it; BL3: double to triple).",
)
mortar_damage = SliderOption(
    "Mortar Damage", 130, 30, 600, 10, True,
    description="Vladof launcher mortar: damage of each shell, percent of the gun's own damage.",
)
fire_rate_time = SliderOption("Double Barrel Time", 8, 3, 20, 1, True, description="Seconds the doubled fire rate lasts.")
RENAMED = {"Fire Rate": "Double Barrel"}  # old saved names -> current
DAMAGE = {"Grenade Launcher": grenade_damage, "Rocket Launcher": rocket_damage, "Shotgun": shotgun_damage,
          "Taser": taser_damage, "Zip Rockets": zip_damage, "Rocket Salvo": salvo_damage, "Mortar": mortar_damage}  # (the Big-Boy's damage is put on its rocket: _retag_big_boy)
SHOTS = {"Rocket Launcher": rocket_shots, "Shotgun": shotgun_shots, "Taser": taser_shots, "Zip Rockets": zip_shots}
FULL_SHOTS = {"Zip Rockets"}  # shot counts already meant for pistols (no 2/3 cut)
# v0.64.7: one switch for each underbarrel (the old "Underbarrel Type" dropdown forced a single type, and did nothing on the gun types that cannot have it: Bipod
# on an AR left the AR with its random roll). A switched-off underbarrel is not rolled; a gun type none of whose own underbarrels is switched on rolls from all of them.
KIND_OPTIONS = {
    kind: BoolOption(
        f"Underbarrel: {kind}", True,
        description=f"Vladof {' and '.join(g.lower() + 's' for g, kinds in KINDS.items() if kind in kinds)} can roll the {kind.lower()} underbarrel. "
                    "Off = it is not rolled (a gun that already has it gets another one, its saved roll is kept). If every underbarrel of a gun type is off, that type rolls all of its own.",
    )
    for kind in dict.fromkeys(k for kinds in KINDS.values() for k in kinds)
}
KIND_GROUP = GroupedOption("Vladof Underbarrels That Can Roll", list(KIND_OPTIONS.values()), description="Switch off the underbarrels you do not want to see on Vladof guns.")


NEEDS_BARREL = {("Pistol", "Double Barrel"): "Pistol_Barrel_Vladof"}  # (gun type, underbarrel) -> the barrel part's name it needs: a pistol's double barrel only makes sense on the Vladof barrel


def kinds_for(weapon: UObject) -> list[str]:
    """The underbarrels this gun can have: its type's, minus the ones that need a barrel it doesn't have."""
    gun = gun_type(weapon) or ""
    barrel = weapon.DefinitionData.BarrelPartDefinition
    name = barrel.Name if barrel is not None else ""
    return [k for k in KINDS.get(gun, []) if not NEEDS_BARREL.get((gun, k)) or name.startswith(NEEDS_BARREL[(gun, k)])]


def wanted_kinds(allowed: list[str]) -> list[str]:
    """Of the underbarrels a gun can have, the ones switched on, or all of them when none is."""
    return [k for k in allowed if KIND_OPTIONS[k].value] or allowed


cooldown = SliderOption(
    "Underbarrel Cooldown",
    20,
    0,
    120,
    5,
    True,
    description="Seconds before a Vladof underbarrel can be redeployed after its magazine runs out.",
)
OPTIONS = [cooldown, KIND_GROUP, grenade_damage, rocket_damage, rocket_shots, shotgun_damage, shotgun_shots,
           taser_damage, taser_shots, zip_damage, zip_shots, salvo_damage, bigboy_damage, bigboy_cooldown, mortar_damage, fire_rate_time]

# This session only (not saved), keyed by UniqueId
_deployed: dict[int, int] = {}  # gun rounds loaded when deployed
_ready_at: dict[int, float] = {}  # when the cooldown ends
_forced: dict[int, str] = {}  # underbarrel forced by the option when deployed
_ammo_at_deploy: dict[int, int] = {}  # reserve when deployed (given back on stow)
_total_shots: dict[int, int] = {}  # shots in this deploy
_mode: dict[int, dict] = {}  # deployed mode: {"kind", "ref", "base": {attr: v}, "written": {attr: v}, "until"}
_reloading: set[int] = set()  # guns whose player pressed reload while an underbarrel was out (the next tick stows it)
_hit_logged: set[str] = set()
_big_boy_pending = [0, -1e9]  # Big-Boy rockets fired that are not given their damage and explosion yet, and when the last was fired
_big_boy_seen: dict[int, float] = {}  # a rocket's address -> when it was
_big_boy_explosions: dict[str, UObject] = {}  # an explosion's path -> its bigger copy
_burst: dict[int, list] = {}  # mortar: [shots in this burst, when the last one was]
_mortar_pending = [0]  # mortar shells fired that are not aimed yet
_mortar_seen: dict[int, float] = {}  # a shell's address -> when it was aimed
_mortar_lock: list = [None, -1e9]  # the cursor point a burst was fired at (the recoil moves the view during the burst) and when it was taken
_mortar_due: list[tuple[WeakPointer, float]] = []  # shells in the air and when each reaches the cursor (it goes off there)
_fm_logged = False
_speed: dict | None = None  # bipod: {"pawn": WeakPointer, "base": float, "written": float}


def gun_type(weapon: UObject) -> str | None:
    wtype = weapon.DefinitionData.WeaponTypeDefinition
    name = wtype.Name if wtype is not None else ""
    if "AssaultRifle" in name:
        return "AR"
    if "Pistol" in name:
        return "Pistol"
    if "Sniper" in name:
        return "Sniper"
    if "Launcher" in name:
        return "Launcher"
    return None


def _cost(weapon: UObject) -> int:
    try:
        return max(1, int(round(weapon.ShotCost)))
    except Exception:  # noqa: BLE001 - older/other weapon classes
        return 1


def underbarrel_kind(weapon: UObject) -> str | None:
    """The underbarrel a Vladof AR or pistol has (rolled once and saved), or None for other guns."""
    if gun_type(weapon) is None or weapon.DefinitionData.BarrelPartDefinition is None:
        return None
    return _plan(weapon)[1]


def damage_multiplier(weapon: UObject | None) -> float:
    """Extra damage while the held gun's underbarrel is deployed (1.0 otherwise)."""
    if weapon is None or maker_of(weapon) != "Vladof" or not _is_deployed(weapon):
        return 1.0
    saved = _entry(weapon.DefinitionData.UniqueId)
    option = DAMAGE.get(saved[1]) if saved else None
    return option.value / 100 if option is not None else 1.0


def log_hit(weapon: UObject, damage: float) -> None:
    saved = _entry(weapon.DefinitionData.UniqueId)
    kind = saved[1] if saved else "?"
    if kind not in _hit_logged:
        _hit_logged.add(kind)
        log(f"Vladof {kind} hit for {damage:.0f} (x{damage_multiplier(weapon):.2f} applied)")


def shots_left(weapon: UObject) -> tuple[int, int]:
    """(shots left, shots in this deploy) for the indicator."""
    cost = _cost(weapon)
    uid = weapon.DefinitionData.UniqueId
    return weapon.ReloadCnt // cost, _total_shots.get(uid, max(1, weapon.ClipSize // cost))


def time_left(weapon: UObject) -> float | None:
    """Seconds left of a timed underbarrel (the fire-rate boost), else None."""
    entry = _mode.get(weapon.DefinitionData.UniqueId)
    if entry is None or not MODES[entry["kind"]].get("timed"):
        return None
    return max(0.0, entry["until"] - game_now())


def _plan(weapon: UObject) -> tuple[str, str] | None:
    """(original barrel path, underbarrel) for this gun, rolled once and saved."""
    current = weapon.DefinitionData.BarrelPartDefinition._path_name()
    allowed = kinds_for(weapon)
    wanted = wanted_kinds(allowed)

    def make(rng):
        # A gun that already has a launcher barrel never gets that barrel as its underbarrel.
        options = sorted(k for k in wanted if UNDERBARRELS[k] != current) or sorted(k for k in allowed if UNDERBARRELS[k] != current)
        from . import heartshatter

        if "Bipod" in options and heartshatter.has_fingerprint(weapon):  # the HeartShatter's underbarrel is not random
            return [current, "Bipod"]
        return [current, rng.choice(options)]

    original, kind = remembered("vladof", weapon, make)
    from . import heartshatter

    if "Bipod" in allowed and kind != "Bipod" and heartshatter.has_fingerprint(weapon):
        # the HeartShatter always has the bipod (its saved roll may be an older random one)
        kind = "Bipod"
        state.value = {**state.value, f"vladof:{weapon.DefinitionData.UniqueId}": [original, kind]}
        state.save()
    if kind in RENAMED:
        kind = RENAMED[kind]
        state.value = {**state.value, f"vladof:{weapon.DefinitionData.UniqueId}": [original, kind]}
        state.save()
    if UNDERBARRELS.get(kind, 0) == original or kind not in allowed:  # old/bad state: re-roll
        forget("vladof", weapon)
        original, kind = remembered("vladof", weapon, make)
    if kind not in wanted:  # switched off: another one, the same one every time for this gun (the saved roll stays as it was)
        others = sorted(k for k in wanted if UNDERBARRELS[k] != original)
        if others:
            kind = random.Random(f"vladof-pick:{weapon.DefinitionData.UniqueId}").choice(others)
    return original, kind


def _entry(uid: int) -> list[str] | None:
    """[original barrel, underbarrel] as used right now (a forced one overrides the saved one)."""
    saved = state.value.get(f"vladof:{uid}")
    if not saved:
        return None
    return [saved[0], _forced[uid]] if uid in _forced else saved


def _is_deployed(weapon: UObject) -> bool:
    uid = weapon.DefinitionData.UniqueId
    if uid in _mode:
        return True
    barrel = weapon.DefinitionData.BarrelPartDefinition
    if barrel is None or "AR_Barrel_Torgue_" not in barrel.Name:
        return False
    saved = _entry(uid)
    return bool(saved) and UNDERBARRELS.get(saved[1]) is not None and barrel._path_name() == UNDERBARRELS[saved[1]] != saved[0]


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-4 * max(1.0, abs(b))


def _tune_mode(weapon: UObject) -> None:
    """Hold a mode underbarrel's attributes on the held gun."""
    entry = _mode[weapon.DefinitionData.UniqueId]
    mode = MODES[entry["kind"]]
    uid = weapon.DefinitionData.UniqueId
    shots = _total_shots.get(uid, 0) if not mode.get("timed") else 0
    watched = [*mode["attrs"], *mode.get("scale", {}), "FireInterval"] + (["StatusEffectChanceModifier"] if mode.get("shock") else [])
    if shots:
        watched.append("ClipSize")
    for attr in watched:
        if attr == "ShotCost":  # the gun's own reading flickers between 0 and its cost around each shot: its original is the base value
            entry["base"].setdefault(attr, max(1, round(weapon.ShotCostBaseValue)))
            continue
        current = getattr(weapon, attr)
        if attr not in entry["written"] or not _close(current, entry["written"][attr]):
            entry["base"][attr] = current  # first time, or BL2 recomputed it
    targets = dict(mode["attrs"])
    for attr, factor in mode.get("scale", {}).items():
        targets[attr] = entry["base"][attr] * factor
        if attr == "ProjectilesPerShot":
            targets[attr] = max(1, round(targets[attr]))  # (an integer property)
    base_interval = entry["base"]["FireInterval"]
    if "rate" in mode:
        targets["FireInterval"] = base_interval / mode["rate"]
    else:
        targets["FireInterval"] = max(base_interval, mode["interval"])
    if mode.get("shock"):
        targets["StatusEffectChanceModifier"] = max(4.0, entry["base"]["StatusEffectChanceModifier"] * 4)
    if shots:
        # v0.26.3: the magazine is the underbarrel's own shots, so BL2's HUD doesn't show its reload
        # prompt (WillowHUDGFxMovie.UpdateAmmo compares rounds left to ClipSize) and the ammo counter
        # reads underbarrel shots
        targets["ClipSize"] = shots * _cost(weapon)
    for attr, value in targets.items():
        setattr(weapon, attr, value)
        entry["written"][attr] = getattr(weapon, attr)
    # v0.27.1: spin-up barrels (Vladof Minigun/Shredifier) fire slower until BarrelSpinUpPercent reaches 1
    # (the part's StartingSpinUpFireIntervalMultiplier), so a rocket waited seconds for its first shot.
    # Held fully spun while an underbarrel mode is out; BL2 spins it down normally after the stow.
    weapon.BarrelSpinUpPercent = 1.0
    weapon.MagazineSpinUpPercent = 1.0


def _restore_element(weapon: UObject, loaded: int | None) -> None:
    """Give a gun its own element back after the taser (also after a save taken mid-deploy)."""
    uid = weapon.DefinitionData.UniqueId
    key = f"vladoftaser:{uid}"
    original = state.value.get(key)
    if original is None:
        return
    part = find_part(original[0]) if original and original[0] else None
    rebuild(weapon, loaded=loaded if loaded is not None else weapon.ReloadCnt, ElementalPartDefinition=part)
    state.value = {k: v for k, v in state.value.items() if k != key}
    state.save()


def _stow(weapon: UObject, kind: str, original: str, used: float, reason: str) -> None:
    uid = weapon.DefinitionData.UniqueId
    loaded = _deployed.pop(uid, None)
    _reloading.discard(uid)
    entry = _mode.pop(uid, None)
    if entry is not None:
        for attr, value in entry["base"].items():
            setattr(weapon, attr, value)
        if loaded is not None and not (MODES[entry["kind"]].get("timed") or MODES[entry["kind"]].get("toggle")):
            weapon.ReloadCnt = loaded
        if MODES[entry["kind"]].get("toggle"):
            if MODES[entry["kind"]].get("stance"):
                _release_speed()
            used = 0.0
        _restore_element(weapon, loaded)
    else:
        rebuild(weapon, loaded=loaded, BarrelPartDefinition=find_part(original))
    _muzzle_off(uid)
    _forced.pop(uid, None)
    _total_shots.pop(uid, None)
    before = _ammo_at_deploy.pop(uid, None)
    if before is not None and kind != "Big-Boy Rocket":  # (the Big-Boy's rocket does come out of the reserve)
        spent = before - weapon.GetAmmoCount()
        if spent > 0:
            weapon.AddAmmo(spent)  # the underbarrel's shots don't cost the gun's ammo
            log(f"Vladof #{uid}: gave back {spent} rounds the underbarrel used")
    wait = (bigboy_cooldown.value if kind == "Big-Boy Rocket" else cooldown.value) * used
    if wait > 0:
        _ready_at[uid] = game_now() + wait
    log(f"Vladof #{uid}: {kind} stowed ({reason}), cooldown {wait:.0f}s")
    from . import indicator

    indicator.bump()
    if not MODES.get(kind, {}).get("stance"):
        indicator.flick()  # quick gun flick on deploy / stow
        _clack(weapon, kind, False)
    else:
        from mods_base import get_pc

        _loop_stop()
        pc = get_pc(possibly_loading=True)
        fold = _find_obj("AkEvent", FOLD_SOUND)
        if fold is not None and pc is not None and pc.Pawn is not None:
            pc.Pawn.PlayAkEvent(fold)


def _deploy_mode(weapon: UObject, kind: str) -> None:
    uid = weapon.DefinitionData.UniqueId
    mode = MODES[kind]
    if mode.get("shock"):
        shock = find_part(SHOCK_PART[gun_type(weapon)])
        if shock is not None:
            current = weapon.DefinitionData.ElementalPartDefinition
            state.value = {**state.value, f"vladoftaser:{uid}": [current._path_name() if current is not None else ""]}
            state.save()
            rebuild(weapon, loaded=weapon.ReloadCnt, ElementalPartDefinition=shock)
    if mode.get("timed") or mode.get("toggle"):
        _ammo_at_deploy.pop(uid, None)  # the boost / the bipod fire the gun's own ammo
        _total_shots[uid] = 0
    elif mode.get("one_shot"):
        _total_shots[uid] = 1
    else:
        shots = SHOTS[kind].value
        if gun_type(weapon) == "Pistol" and kind not in FULL_SHOTS:
            shots = max(1, round(shots * PISTOL_SHOTS))
        _total_shots[uid] = shots
    native = weapon.GetFiringModeDefinition()  # (before the mode is in place: the gun's own firing mode)
    _mode[uid] = {"kind": kind, "ref": WeakPointer(weapon), "base": {}, "written": {}, "until": game_now() + fire_rate_time.value,
                  "native_fm": native._path_name() if native is not None else ""}
    _tune_mode(weapon)
    if _total_shots[uid]:
        weapon.ReloadCnt = _total_shots[uid] * _cost(weapon)



def is_stance(weapon: UObject | None) -> bool:
    """Is the held gun's deployed mode the bipod's stance (its pose, sounds and the slowed walk)?"""
    entry = _mode.get(weapon.DefinitionData.UniqueId) if weapon is not None else None
    return entry is not None and bool(MODES[entry["kind"]].get("stance"))


def is_toggle(weapon: UObject) -> bool:
    entry = _mode.get(weapon.DefinitionData.UniqueId)
    return entry is not None and bool(MODES[entry["kind"]].get("toggle"))


def _hold_speed() -> None:
    """Bipod: keep the player nearly rooted (BL2 recomputes GroundSpeed, e.g. for sprint)."""
    global _speed
    from mods_base import get_pc

    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if pawn is None:
        return
    current = pawn.GroundSpeed
    if _speed is None or _speed["pawn"]() is None or not _close(current, _speed["written"]):
        _speed = {"pawn": WeakPointer(pawn), "base": current, "written": 0.0}  # first frame, or BL2 recomputed it
    pawn.GroundSpeed = _speed["base"] * BIPOD_SPEED
    _speed["written"] = pawn.GroundSpeed


def _release_speed() -> None:
    global _speed
    if _speed is not None:
        pawn = _speed["pawn"]()
        if pawn is not None:
            pawn.GroundSpeed = _speed["base"]
    _speed = None


@hook("WillowGame.WillowPlayerController:BeginSprint")
def no_sprint_on_bipod(_obj: UObject, *_: object) -> type[Block] | None:
    weapon = held_weapon()
    return Block if weapon is not None and is_stance(weapon) else None


def switch(weapon: UObject) -> None:
    if gun_type(weapon) is None or weapon.DefinitionData.BarrelPartDefinition is None:
        show_hud_message("Underbarrel", "This Vladof gun has no underbarrel.", 1.5)
        return
    uid = weapon.DefinitionData.UniqueId
    original, kind = _plan(weapon)
    current = weapon.DefinitionData.BarrelPartDefinition._path_name()

    if _is_deployed(weapon):
        entry = _entry(uid)
        timed = time_left(weapon)
        if is_toggle(weapon):
            _stow(weapon, entry[1] if entry else kind, original, 0, "stowed")
            return
        if timed is not None:
            used = 1 - timed / max(fire_rate_time.value, 1)
        else:
            left, total = shots_left(weapon)
            used = 1 - left / max(total, 1)
        _stow(weapon, entry[1] if entry else kind, original, used, "stowed")
        return
    if current != original:
        # The barrel changed outside this mod (another mod/editor): start over from it.
        forget("vladof", weapon)
        switch(weapon)
        return

    left = _ready_at.get(uid, 0) - game_now()
    if left > 0:
        from . import indicator

        indicator.bump()  # shows the cooldown
        return
    _deployed[uid] = weapon.ReloadCnt
    _ammo_at_deploy[uid] = weapon.GetAmmoCount()
    saved = state.value.get(f"vladof:{uid}")
    if saved and kind != saved[1]:
        _forced[uid] = kind
    else:
        _forced.pop(uid, None)
    if kind in MODES:
        _deploy_mode(weapon, kind)
    else:
        under = find_part(UNDERBARRELS[kind])
        if under is None:
            log(f"missing barrel part {UNDERBARRELS[kind]}")
            _deployed.pop(uid, None)
            _ammo_at_deploy.pop(uid, None)
            return
        rebuild(weapon, loaded=10**6, BarrelPartDefinition=under)  # one full launcher magazine
        _total_shots[uid] = weapon.ClipSize // _cost(weapon)
    log(f"Vladof #{uid}: {kind} deployed ({_total_shots[uid]} shots)")
    _muzzle_on(weapon, kind)
    from . import indicator

    indicator.bump()
    if MODES.get(kind, {}).get("stance"):
        _plant_fx(weapon)  # the bipod has its own plant pose and sound instead of the flick
    else:
        indicator.flick()  # quick gun flick on deploy / stow
        _clack(weapon, kind, True)


@hook("WillowGame.WillowWeapon:GetFiringModeDefinition", hook_identifier="bl3w_vladof_fm")
def mode_firing_mode(obj: UObject, *_: object) -> tuple[type[Block], UObject] | None:
    """A mode underbarrel with its own firing mode (the rocket launcher) fires that instead."""
    global _fm_logged
    entry = _mode.get(obj.DefinitionData.UniqueId)
    path = MODES[entry["kind"]].get("fm") if entry is not None else None
    if path is None or (MODES[entry["kind"]].get("fm_if_standard") and entry["native_fm"] != STANDARD_FM):
        return None
    try:
        fm = unrealsdk.find_object("FiringModeDefinition", path)
    except ValueError:
        fm = None
    if fm is None:
        if not _fm_logged:
            _fm_logged = True
            log(f"Vladof: firing mode {path} not loaded")
        return None
    return Block, fm


@hook("WillowGame.WillowWeapon:ShouldRefire")
def pump_refire(obj: UObject, *_: object) -> tuple[type[Block], bool] | None:
    """Single-shot underbarrels (shotgun, rocket, taser) fire once per trigger pull."""
    uid = obj.DefinitionData.UniqueId
    entry = _mode.get(uid)
    if entry is not None and MODES[entry["kind"]].get("semi"):
        held = held_weapon()
        if held is not None and held._get_address() == obj._get_address():
            return Block, False
    if entry is not None and MODES[entry["kind"]].get("burst"):
        held = held_weapon()
        if held is not None and held._get_address() == obj._get_address():
            count, last = _burst.get(uid, [0, -1e9])
            return Block, 0 < count < MODES[entry["kind"]]["burst"] and game_now() - last < MORTAR_BURST_GAP  # the rest of the burst fires by itself
    return None


@hook("WillowGame.WillowWeapon:ConsumeAmmo", Type.POST, hook_identifier="bl3w_vladof_shot")
def single_shot(obj: UObject, *_: object) -> None:
    """v0.33.2: one shot per pull for real. Refusing ShouldRefire (pump_refire) doesn't stop the engine's
    native refire (found on Dahl semi-auto); clearing the "trigger held" flag after each shot does."""
    uid = obj.DefinitionData.UniqueId
    entry = _mode.get(uid)
    if entry is None:
        return
    mode = MODES[entry["kind"]]
    held = held_weapon()
    if held is None or held._get_address() != obj._get_address():
        return
    if entry["kind"] == "Big-Boy Rocket":
        _big_boy_pending[:] = [_big_boy_pending[0] + 1, game_now()]
    if mode.get("burst"):
        now = game_now()
        count, last = _burst.get(uid, [0, -1e9])
        count = 1 if now - last > MORTAR_BURST_GAP else count + 1
        _burst[uid] = [count, now]
        if mode.get("arc"):
            _mortar_pending[0] += 1
            if count == 1:
                _mortar_lock[:] = [mortar_cursor(obj), now]
        if count >= mode["burst"]:
            _burst[uid] = [0, now]
            obj.ClearPendingFire(0)  # the burst is over: the next shell needs another pull
        return
    if mode.get("semi"):
        obj.ClearPendingFire(0)


def _note_reload(obj: UObject) -> None:
    """A reload (the player's own, or the gun running dry) while an underbarrel is out: noted here, stowed by the next tick. Before v0.64.11
    this was looked for in the weapon's state name (GetStateName is always None for a gun), so a manual reload never stowed the launcher: it
    simply refilled it, and pressing reload over and over kept it out for good with no cooldown."""
    uid = obj.DefinitionData.UniqueId
    if uid in _deployed:
        _reloading.add(uid)


@hook("WillowGame.WillowWeapon:BeginManualReload", hook_identifier="bl3w_vladof_manual_reload")
def manual_reload(obj: UObject, *_: object) -> None:
    _note_reload(obj)


@hook("WillowGame.WillowWeapon:StartReload", hook_identifier="bl3w_vladof_start_reload")
def auto_reload(obj: UObject, *_: object) -> None:
    _note_reload(obj)


def _view_ray():
    """(camera position, forward unit vector) of the player's view."""
    from mods_base import get_pc

    pc = get_pc(possibly_loading=True)
    if pc is None:
        return None
    zero_v = unrealsdk.make_struct("Vector", X=0, Y=0, Z=0)
    zero_r = unrealsdk.make_struct("Rotator", Pitch=0, Yaw=0, Roll=0)
    ret = pc.GetPlayerViewPoint(zero_v, zero_r)
    if not isinstance(ret, tuple) or len(ret) < 3:
        return None
    cam, rot = ret[1], ret[2]
    pitch, yaw = rot.Pitch * math.tau / 65536, rot.Yaw * math.tau / 65536
    return cam, (math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch))


SNAP_ANGLE = 4.5  # degrees: an enemy this close to the aim line takes the cursor
_snap: dict = {"at": -1e9, "point": None}


def _snap_enemy(cam, fwd) -> tuple[float, float, float] | None:
    """The feet of the live enemy nearest the aim line (within SNAP_ANGLE of it, 3 to 90 m away), looked for 5 times a second."""
    now = game_now()
    if now - _snap["at"] < 0.2:
        return _snap["point"]
    _snap["at"], _snap["point"] = now, None
    from mods_base import get_pc

    pc = get_pc(possibly_loading=True)
    player = pc.Pawn if pc is not None else None
    if player is None:
        return None
    best, best_angle = None, SNAP_ANGLE
    for pawn in pawns(player):
        try:
            if pawn._get_address() == player._get_address() or not pawn.IsAliveAndWell() or not player.IsEnemy(pawn):
                continue
            loc = pawn.Location
            to = (loc.X - cam.X, loc.Y - cam.Y, loc.Z - cam.Z)
            dist = math.sqrt(sum(c * c for c in to))
            if not 300.0 < dist < 9000.0:
                continue
            angle = math.degrees(math.acos(max(-1.0, min(1.0, sum(a * b for a, b in zip(to, fwd)) / dist))))
            if angle < best_angle:
                best, best_angle = loc, angle
        except Exception:  # noqa: BLE001 - a pawn going away mid-walk
            continue
    if best is not None:
        _snap["point"] = (best.X, best.Y, best.Z - 70.0)  # (the pawn's origin is at its middle: a shell comes down at its feet)
    return _snap["point"]


_boxes: dict[float, UObject] = {}
SURFACE_MAX = 4000.0      # uu: how far the aim is followed: beyond it the marking is hidden and the mortar has no target
SURFACE_STEP = 28.0       # the march's step: less than the 32 a 16-unit box is thick, so a surface is never stepped over, near the player
SURFACE_FAR = (900.0, 80.0)   # beyond this distance the step is the second number: what is far is big (a hill, a wall), and the sky must be cheap to find out
SURFACE_RETRY = 0.12      # s: after the aim met nothing, it is looked at again this often
SURFACE_BOX = 16.0
_surface: dict = {"d": None, "normal": (0.0, 0.0, 1.0), "point": None, "retry": -1e9, "frame": 0}


def _spot(pawn: UObject, x: float, y: float, z: float, half: float) -> tuple[float, tuple[float, float, float]]:
    """How far Actor.FindSpot moves a box of this half size centred here (0 when it is clear of the world: the SDK's traces never report a hit, but this tells the truth,
    and a surface has no thickness, so only a box touching it is moved), and where to. A box that touches a surface is pushed out along its normal."""
    box = _boxes.get(half)
    if box is None:
        box = _boxes[half] = unrealsdk.make_struct("Vector", X=half, Y=half, Z=half)
    ok, out = pawn.FindSpot(BoxExtent=box, SpotLocation=unrealsdk.make_struct("Vector", X=x, Y=y, Z=z))
    if not ok:
        return 1e9, (x, y, z)
    return abs(out.X - x) + abs(out.Y - y) + abs(out.Z - z), (out.X, out.Y, out.Z)


def _hit(pawn: UObject, cam, fwd, hint: float | None, floor: float = 160.0, ceil: float = SURFACE_MAX) -> float | None:
    """How far along the ray a box first touches the world (None: nothing within floor..ceil, or the start is in geometry). The box is marched from `hint`, outward when it
    is clear there and back when it is not, then halved down to about a unit."""
    def touching(d: float) -> bool:
        return _spot(pawn, cam.X + fwd[0] * d, cam.Y + fwd[1] * d, cam.Z + fwd[2] * d, SURFACE_BOX)[0] > 0.5

    def stride(distance: float) -> float:
        return SURFACE_STEP if distance < SURFACE_FAR[0] else SURFACE_FAR[1]

    d = min(max(hint if hint is not None else 700.0, floor), ceil)
    lo = hi = None
    if not touching(d):
        lo = d
        while True:
            d += stride(d)
            if d > ceil:
                return None
            if touching(d):
                hi = d
                break
            lo = d
    else:
        hi = d
        while True:
            d -= stride(d)
            if d < floor:
                return None          # the camera is in geometry
            if not touching(d):
                lo = d
                break
            hi = d
    for _ in range(7):
        mid = (lo + hi) / 2
        if touching(mid):
            hi = mid
        else:
            lo = mid
    return hi


def _cross(a, b) -> tuple[float, float, float]:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def _plane(pawn: UObject, cam, fwd, hi: float) -> tuple[float, float, float] | None:
    """The surface's normal facing the player, from where two more rays, a little to the right and a little above the aim, meet it (FindSpot only ever pushes a box out
    along an axis, so its direction says nothing about a slope). None when either misses the surface near this distance (an edge, or a grazing angle)."""
    right = (fwd[1], -fwd[0], 0.0)
    length = math.hypot(right[0], right[1])
    right = (right[0] / length, right[1] / length, 0.0) if length > 1e-3 else (1.0, 0.0, 0.0)
    up = _cross(right, fwd)
    delta = min(0.1, max(0.015, 140.0 / hi))          # the rays are about 140 units apart at the surface
    reach = 10.0 * delta * hi          # a flat surface seen at 6 degrees puts the two hits this far apart along the ray
    points = []
    for side in (right, up):
        ray = tuple(f + delta * c for f, c in zip(fwd, side))
        norm = math.sqrt(sum(c * c for c in ray))
        ray = tuple(c / norm for c in ray)
        d = _hit(pawn, cam, ray, hi, floor=max(hi - reach, 160.0), ceil=hi + reach)
        if d is None:
            return None
        points.append(tuple(cam_c + r * d for cam_c, r in zip((cam.X, cam.Y, cam.Z), ray)))
    here = (cam.X + fwd[0] * hi, cam.Y + fwd[1] * hi, cam.Z + fwd[2] * hi)
    a, b = (tuple(p - h for p, h in zip(pt, here)) for pt in points)
    if max(math.sqrt(sum(c * c for c in a)), math.sqrt(sum(c * c for c in b))) > reach:
        return None          # the two hits are not on one surface
    n = _cross(a, b)
    length = math.sqrt(sum(c * c for c in n))
    if length < 1e-6:
        return None
    n = tuple(c / length for c in n)
    return tuple(-c for c in n) if sum(x * y for x, y in zip(n, fwd)) > 0 else n


def surface_along(pawn: UObject, cam, fwd, hint: float | None, prev: tuple[float, float, float] = (0.0, 0.0, 1.0), plane: bool = True) -> tuple[tuple[float, float, float], tuple[float, float, float], float] | None:
    """Where the aim first touches the world: (the point on the surface, the surface's normal facing the player, the distance), or None (the sky, or too far). The normal
    is `prev` when it could not be worked out or `plane` is false (the caller skips it on alternate frames: the aim has not moved far, and it halves the cost)."""
    hi = _hit(pawn, cam, fwd, hint)
    if hi is None:
        return None
    n = _plane(pawn, cam, fwd, hi) if plane or hint is None or abs(hi - hint) > 100.0 else prev
    if n is None:          # no plane found: a wall's last angle is not kept for the ground a shallow aim meets
        n = (0.0, 0.0, 1.0) if fwd[2] < -0.02 else prev
    reach = SURFACE_BOX * sum(abs(c) for c in n)          # a box's centre is this far from the surface it touches
    return (cam.X + fwd[0] * hi - n[0] * reach, cam.Y + fwd[1] * hi - n[1] * reach, cam.Z + fwd[2] * hi - n[2] * reach), n, hi


def mortar_cursor(weapon: UObject) -> tuple[float, float, float] | None:
    """Where the mortar's cursor is: on the enemy nearest the aim line, else on the surface the aim meets (the ground, a wall, a roof). None for the sky.
    The surface's normal is left in `_surface["normal"]` (eased from frame to frame)."""
    ray = _view_ray()
    if ray is None:
        return None
    cam, fwd = ray
    enemy = _snap_enemy(cam, fwd)
    if enemy is not None:
        _surface["point"] = enemy
        _surface["d"] = None
        _surface["normal"] = _ease(_surface["normal"], (0.0, 0.0, 1.0), 0.4)
        return enemy
    now = game_now()
    if now < _surface["retry"]:
        return None
    from mods_base import get_pc

    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if pawn is None:
        return None
    try:
        _surface["frame"] ^= 1
        found = surface_along(pawn, cam, fwd, _surface["d"], _surface["normal"], _surface["frame"] == 0)
    except Exception:  # noqa: BLE001 - no pawn to ask, or the call refused
        found = None
    if found is None:
        _surface["d"] = None
        _surface["retry"] = now + SURFACE_RETRY
        return None
    point, normal, distance = found
    _surface["d"] = distance
    _surface["point"] = point
    _surface["normal"] = _ease(_surface["normal"], normal, 0.45)
    return point


def _ease(old: tuple[float, float, float], new: tuple[float, float, float], rate: float) -> tuple[float, float, float]:
    mixed = tuple(o + (n - o) * rate for o, n in zip(old, new))
    length = math.sqrt(sum(c * c for c in mixed)) or 1.0
    return tuple(c / length for c in mixed)


def _aim_mortar(weapon: UObject) -> None:
    """Give each shell just fired a lob that lands on the cursor (the flight time grows with the distance, a little scatter around the point)."""
    now = game_now()
    for key in [k for k, t in _mortar_seen.items() if now - t > 10]:
        _mortar_seen.pop(key, None)
    for entry in [e for e in _mortar_due if e[1] <= now]:
        _mortar_due.remove(entry)
        shell = entry[0]()
        if shell is not None:
            try:
                shell.Detonate()  # a mortar shell goes off where it lands, not after a grenade's bounces and fuse
            except Exception as e:  # noqa: BLE001
                log(f"mortar shell: Detonate refused ({e!r})")
    if _mortar_pending[0] <= 0:
        return
    target = _mortar_lock[0] if now - _mortar_lock[1] < 2.0 else mortar_cursor(weapon)
    for p in unrealsdk.find_all("WillowProjectile", exact=False):
        if _mortar_pending[0] <= 0:
            break
        address = p._get_address()
        if address in _mortar_seen or p.Name.startswith("Default__") or p.Definition is None or MODES["Mortar"]["shell"] not in p.Definition._path_name():
            continue
        _mortar_seen[address] = now
        _mortar_pending[0] -= 1
        if target is None:
            continue  # nothing to land on: it flies as the gun fires it
        loc = p.Location
        scatter = 40 + 0.02 * math.dist((loc.X, loc.Y), target[:2])
        dx, dy = target[0] + random.uniform(-scatter, scatter) - loc.X, target[1] + random.uniform(-scatter, scatter) - loc.Y
        dz = target[2] - loc.Z
        flight = min(2.6, 0.9 + math.hypot(dx, dy) / 2200)
        g = MORTAR_G * p.CustomGravityScaling
        p.Velocity = unrealsdk.make_struct("Vector", X=dx / flight, Y=dy / flight, Z=(dz + 0.5 * g * flight * flight) / flight)
        _mortar_due.append((WeakPointer(p), now + flight))
    _mortar_pending[0] = min(_mortar_pending[0], 8)


# The mortar's cursor is painted on the ground, in the world (BL3's red ring): two flat planes with a ring texture on an additive material, one inside the other.
RING_MESH = "FX_Shared_Mesh.Meshes.Planes.DecalPlane_LiquidDev"   # flat, 170 wide
RING_MASTER = "FX_Shared_MasterShaders.Materials.Additive.Mat_FX_Unlit_Add_2-Sided_MS"
RING_PLANE_HALF = 85.1                                             # the mesh's half width
RING_TEXTURE = "FX_Shared_Gradients.Textures.Round_Ring_Distortion_Clamped_Dif"   # concentric rings (the other ring texture tints the ground inside it green)
RINGS = ((RING_TEXTURE, 1.0, (2.5, 0.15, 0.1)), (RING_TEXTURE, 0.42, (2.5, 0.15, 0.1)))  # (texture, share of the radius, colour): the big ring and a small one in it
RING_RADIUS = 190.0   # uu: the burst lands within about this of the cursor
RING_LIFT = 6.0       # above the ground, so it does not flicker in it

RING_ARTS = {"Badge": ("mortar_ring.png", "mortar_ring_inner.png"), "Rocket Brackets": ("mortar_ring_brackets.png",)}   # the pictures (bl3_weapons/art, see art/ASSETS.md): the big ring's and the small one's
reticle_style = DropdownOption("Mortar Reticle", "Rocket Brackets", list(RING_ARTS), description="The marking the mortar paints on the ground: the badge between brackets, or four brackets round rocket volleys.")
OPTIONS += [reticle_style]
_ring_style = [""]          # the reticle the planes were built with
RING_ART_COLOUR = (1.0, 1.0, 1.0)                          # their own colours, a little brighter (the ground adds them)
_ring: list[WeakPointer] = []
_ring_owner = 0
_ring_shares: list[float] = []
_ring_mics: list[UObject] = []
_ring_colours: list[tuple[float, float, float]] = []


def _ring_picture(name: str) -> UObject | None:
    """The player's picture `name` as a texture (loaded by the texloader mod, which is optional), or None when there is none or it cannot be loaded."""
    from . import assets

    png = assets.DIR / "art" / name
    if not png.is_file():
        return None
    try:
        from texloader import api  # noqa: PLC0415 - optional: without it the ring keeps the game's own look

        key = f"MortarRing{name.split('.')[0].title().replace('_', '')}{int(png.stat().st_mtime)}"   # new art is a new texture, not the old one still loaded
        try:
            return unrealsdk.find_object("Texture2D", f"Transient.{key}")
        except ValueError:
            return unrealsdk.find_object("Texture2D", api.load(str(png), key))
    except Exception as e:  # noqa: BLE001
        log(f"mortar ring: could not load {name} ({e!r}); the game's own rings are used")
        return None


def _ring_layers() -> list[tuple[UObject, float, tuple[float, float, float]]] | None:
    """The planes of the ring: (texture, share of the radius, colour). The player's own pictures when there are any, else the game's rings."""
    art = RING_ARTS.get(reticle_style.value, RING_ARTS["Badge"])
    big = _ring_picture(art[0])
    if big is not None:
        small = _ring_picture(art[1]) if len(art) > 1 else None
        return [(big, 1.0, RING_ART_COLOUR)] + ([(small, 0.42, RING_ART_COLOUR)] if small is not None else [])
    layers = []
    for texture, share, colour in RINGS:
        try:
            layers.append((unrealsdk.find_object("Texture2D", texture), share, colour))
        except ValueError:
            return None
    return layers


def _ring_cells(pawn: UObject) -> list[UObject] | None:
    """The ring's planes on this pawn (made once, with their materials)."""
    global _ring, _ring_owner
    cells = [w() for w in _ring]
    if _ring and (any(c is None for c in cells) or _ring_owner != pawn._get_address() or _ring_style[0] != reticle_style.value):
        for c in cells:
            if c is not None:
                c.SetHidden(True)          # (the old planes stay in the world, hidden)
        _ring = []
    if _ring:
        return [w() for w in _ring]
    try:
        mesh = unrealsdk.find_object("StaticMesh", RING_MESH)
        master = unrealsdk.find_object("Material", RING_MASTER)
    except ValueError:
        return None
    from . import hyperion

    layers = _ring_layers()
    if not layers:
        return None
    _ring_shares[:] = [share for _t, share, _c in layers]
    _ring_colours[:] = [colour for _t, _s, colour in layers]
    _ring_mics.clear()
    for i, (tex, _share, colour) in enumerate(layers):
        mic = unrealsdk.construct_object("MaterialInstanceConstant", master.Outer, f"BL3W_MortarRing_{i}")
        mic.ObjectFlags = int(mic.ObjectFlags) | 0x4000
        mic.SetParent(master)
        mic.SetTextureParameterValue("Emissive_GP", tex)
        mic.SetVectorParameterValue("p_color", unrealsdk.make_struct("LinearColor", R=colour[0], G=colour[1], B=colour[2], A=1.0))
        _ring_mics.append(mic)
        cell = hyperion._new_cell(pawn)
        cell.SetStaticMesh(mesh, True)
        cell.SetMaterial(0, mic)
        _ring.append(WeakPointer(cell))
    _ring_owner = pawn._get_address()
    _ring_style[0] = reticle_style.value
    return [w() for w in _ring]


def _ring_hide() -> None:
    for w in _ring:
        c = w()
        if c is not None:
            c.SetHidden(True)


RING_SPIN = 10.0        # degrees a second
RING_PULSE = (3.6, 0.05, 0.14)   # period (s), how much its size and its brightness swing


def _ring_rotation(normal: tuple[float, float, float], heading: tuple[float, float, float], spin: float) -> tuple[int, int, int]:
    """The rotator (pitch, yaw, roll in the engine's units) of a plane whose up is `normal` and whose forward is `heading` flattened onto it, turned by `spin` radians
    about the normal."""
    dot = sum(a * b for a, b in zip(heading, normal))
    x = tuple(a - dot * b for a, b in zip(heading, normal))
    length = math.sqrt(sum(c * c for c in x)) or 1.0
    x = tuple(c / length for c in x)
    n_x = (normal[1] * x[2] - normal[2] * x[1], normal[2] * x[0] - normal[0] * x[2], normal[0] * x[1] - normal[1] * x[0])
    c, s_ = math.cos(spin), math.sin(spin)
    x = tuple(c * a + s_ * b for a, b in zip(x, n_x))
    y = (normal[1] * x[2] - normal[2] * x[1], normal[2] * x[0] - normal[0] * x[2], normal[0] * x[1] - normal[1] * x[0])
    pitch = math.asin(max(-1.0, min(1.0, x[2])))
    if abs(x[2]) > 0.99999:        # its forward is straight up or down: yaw and roll are one angle then (the rotator's gimbal lock), taken all as yaw
        yaw, roll = (math.atan2(-normal[1], -normal[0]) if x[2] > 0 else math.atan2(normal[1], normal[0])), 0.0
    else:
        yaw = math.atan2(x[1], x[0])
        roll = math.atan2(-y[2], normal[2])
    unit = 65536 / math.tau
    return int(pitch * unit) & 0xFFFF, int(yaw * unit) & 0xFFFF, int(roll * unit) & 0xFFFF


@hook("WillowGame.WillowGameViewportClient:PostRender", hook_identifier="bl3w_vladof_cursor")
def draw_mortar_cursor(_obj: UObject, args, *_: object) -> None:
    """Keep the red marking on the surface the mortar will land on, lying along it (the ground, a slope, a wall), turning and pulsing slowly (hidden when no mortar
    is out or the aim meets nothing)."""
    from mods_base import get_pc

    weapon = held_weapon()
    target = None
    if weapon is not None and weapon.DefinitionData.UniqueId in _mode and _mode[weapon.DefinitionData.UniqueId]["kind"] == "Mortar":
        target = mortar_cursor(weapon)
    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if target is None or pawn is None:
        if _ring:
            _ring_hide()
        return
    cells = _ring_cells(pawn)
    if not cells:
        return
    normal = _surface["normal"]
    yaw = pc.Rotation.Yaw * math.tau / 65536
    heading = (math.cos(yaw), math.sin(yaw), 0.0) if normal[2] > 0.6 else (0.0, 0.0, 1.0)   # on the ground the marking faces the player; on a wall its top is up
    now = game_now()
    rotator = None
    period, swing, glow = RING_PULSE
    wave = math.sin(now * math.tau / period)
    rot = _ring_rotation(normal, heading, math.radians(RING_SPIN) * now)
    rotator = unrealsdk.make_struct("Rotator", Pitch=rot[0], Yaw=rot[1], Roll=rot[2])
    brightness = 1.0 - glow * (0.5 - 0.5 * wave)
    for cell, share, mic, colour in zip(cells, _ring_shares, _ring_mics, _ring_colours):
        lift = RING_LIFT + share
        cell.SetScale(RING_RADIUS * share / RING_PLANE_HALF * (1.0 + swing * wave))
        cell.SetRotation(rotator)
        cell.SetTranslation(unrealsdk.make_struct("Vector", X=target[0] + normal[0] * lift, Y=target[1] + normal[1] * lift, Z=target[2] + normal[2] * lift))
        mic.SetVectorParameterValue("p_color", unrealsdk.make_struct("LinearColor", R=colour[0] * brightness, G=colour[1] * brightness, B=colour[2] * brightness, A=1.0))
        cell.SetHidden(False)


def _big_explosion(explosion: UObject) -> UObject:
    """A bigger copy of an element's explosion, for a Big-Boy rocket: its biggest size bracket's particles at 1.8 times the scale (made once)."""
    path = explosion._path_name()
    if _big_boy_explosions.get(path) is not None:
        return _big_boy_explosions[path]
    name = explosion.Name + "_BigBoy"
    try:
        clone = unrealsdk.find_object("ExplosionDefinition", f"{explosion.Outer._path_name()}.{name}")
    except ValueError:
        clone = unrealsdk.construct_object(explosion.Class, explosion.Outer, name, 0, explosion)
        clone.ObjectFlags = int(clone.ObjectFlags) | 0x4000
        tiers = [copy.copy(t) for t in clone.ExplosionScale]
        tiers[-1].MaxParticleScale *= 1.8
        tiers[-1].MinParticleScale *= 1.8
        clone.ExplosionScale = tiers
    _big_boy_explosions[path] = clone
    return clone


def _retag_big_boy() -> None:
    """Give the Big-Boy rocket just fired its damage (two to three times a normal rocket's: the slider's share, rolled) and a bigger explosion."""
    now = game_now()
    for key in [k for k, t in _big_boy_seen.items() if now - t > 10]:
        _big_boy_seen.pop(key, None)
    if _big_boy_pending[0] <= 0:
        return
    if now - _big_boy_pending[1] > 1.5:
        _big_boy_pending[0] = 0
        return
    for p in unrealsdk.find_all("WillowProjectile", exact=False):
        if _big_boy_pending[0] <= 0:
            break
        address = p._get_address()
        if address in _big_boy_seen or p.Name.startswith("Default__") or p.Definition is None:
            continue
        path = p.Definition._path_name()
        if not path.startswith(("GD_Weap", "GD_Anemone")) or path.endswith("_Child"):
            continue
        _big_boy_seen[address] = now
        _big_boy_pending[0] -= 1
        factor = random.uniform(BIG_BOY_LOW, 1.0) * bigboy_damage.value / 100
        p.DamageBaseValue = p.DamageBaseValue * factor
        p.Damage = p.Damage * factor
        explosion = p.MyExplosionDefinition
        if explosion is not None and "Nukem" not in explosion.Name:
            p.MyExplosionDefinition = _big_explosion(explosion)  # (the Nukem's own is the biggest there is)
        log(f"Vladof Big-Boy rocket: x{factor:.2f} damage, {p.MyExplosionDefinition.Name if p.MyExplosionDefinition is not None else None}")


@hook("WillowGame.WillowGameViewportClient:Tick")
def watch_underbarrel(*_: object) -> None:
    """Auto-stow when the shots/time run out or a reload starts; keep mode attributes on; stow a mode
    whose gun was put away (its attributes must not stay); repair a taser element left by a save."""
    weapon = held_weapon()
    held_uid = weapon.DefinitionData.UniqueId if weapon is not None else None
    try:
        _retag_big_boy()
    except Exception as e:  # noqa: BLE001 - a rocket that keeps its plain damage is no reason to lose the tick
        log(f"Big-Boy rocket error: {e!r}")
    try:
        _bipod_fx_tick(weapon)
        _muzzle_tick()
    except Exception as e:  # noqa: BLE001 - visuals must never break the underbarrel
        log(f"underbarrel visuals error: {e!r}")
    for uid in [u for u in _mode if u != held_uid]:
        other = _mode[uid]["ref"]()
        if other is None:
            _mode.pop(uid, None)
            continue
        entry = _entry(uid)
        _stow(other, _mode[uid]["kind"], entry[0] if entry else "", 0, "put away")
    if weapon is None or maker_of(weapon) != "Vladof":
        return
    if held_uid not in _mode and f"vladoftaser:{held_uid}" in state.value:
        _restore_element(weapon, None)  # saved while the taser was out
        log(f"Vladof #{held_uid}: element restored after a taser deploy from an earlier session")
    if not _is_deployed(weapon):
        return
    entry = _entry(held_uid)
    if entry is None:
        return
    original, kind = entry
    if held_uid in _mode:
        _tune_mode(weapon)
        if MODES[_mode[held_uid]["kind"]].get("arc"):
            _aim_mortar(weapon)
    if held_uid not in _deployed:
        # Deployed in a previous session (saved while out): no free shots, just stow.
        _stow(weapon, kind, original, 0, "stowed")
        return
    if is_toggle(weapon):
        if is_stance(weapon):
            _hold_speed()
        return
    timed = time_left(weapon)
    if timed is not None:
        if timed <= 0:
            _stow(weapon, kind, original, 1, "time up")
    elif weapon.ReloadCnt <= 0 or held_uid in _reloading:
        _stow(weapon, kind, original, 1, "reload" if held_uid in _reloading else "empty")


def reset() -> None:
    """Put mode underbarrel attributes and the bipod pose back (mod disabled)."""
    for uid in list(_muzzle):
        _muzzle_off(uid)
    _ring_hide()
    try:
        _loop_stop()
    except Exception:  # noqa: BLE001
        pass
    _fx["p"] = 0.0001
    try:
        _bipod_fx_tick(None)
        _fx["p"] = 0.0
        _bipod_fx_tick(None)
    except Exception:  # noqa: BLE001
        pass
    for uid in list(_mode):
        other = _mode[uid]["ref"]()
        if other is not None:
            for attr, value in _mode[uid]["base"].items():
                setattr(other, attr, value)
            _restore_element(other, None)
        _mode.pop(uid, None)
    _release_speed()



# --------------------------------------------------------------------------- bipod plant visuals (v0.29.0)
# Eased in while the bipod is out, out after it folds: the view drops (WillowPawn.BaseEyeHeight), the gun
# settles down and in (Pawn.Arms.SkeletalMesh Origin / RotOrigin offsets, like the mode-change flick), the
# field of view narrows a little (PlayerController.DesiredFOV, re-read when BL2 changes it), and planting
# plays a small camera jolt (BL2's melee CameraAnim) and the rifle equip clack (AkEvent).
BIPOD_EYE_DROP = 6.0  # units (v0.30.0: 16 felt like crouching)
BIPOD_GUN = (-2.0, 1.2, -2.6)  # Origin offset x, y, z
BIPOD_GUN_PITCH = -250  # rotation units (muzzle a touch down)
BIPOD_FOV = 5.0  # degrees narrower
BIPOD_EASE = 7.0  # per second
PLANT_ANIM = "Anim_CameraAnimations.Melee.Canim_SniperRifle_Melee"
PLANT_SOUND = "Ake_Obj_Pickup.Obj_Pickup_Equip.Ak_Play_Obj_Pickup_Equip_Rifle"
# v0.31.0 (user: like BL3's bipod, "rockets pushing the weapon up"): BL2 sounds layered on the plant
BIPOD_SOUNDS = {
    "Thrusters": ["Ake_Wep_Assault_Rifles.Rifle_Torgue.Ak_Play_Wep_Rifle_Torgue_Ignite",
                  "Ake_Wep_Rocket_Launchers.Rocket_Launcher_Vladof.Ak_Play_Wep_Rocket_Launcher_Vladof_Reload_Lock"],
    "Booster": ["Ake_UI.UI_Shields.Ak_Play_UI_Shield_Booster",
                "Ake_Wep_Rocket_Launchers.Rocket_Launcher_Vladof.Ak_Play_Wep_Rocket_Launcher_Vladof_Reload_Lock"],
    "Servo": ["Ake_Wep_SMGs.SMG_Hyperion.Ak_Play_Wep_SMG_Hyperion_Reload_Servo",
              "Ake_Wep_Rocket_Launchers.Rocket_Launcher_Vladof.Ak_Play_Wep_Rocket_Launcher_Vladof_Reload_Lock"],
    "Clack": [PLANT_SOUND],
}
FOLD_SOUND = "Ake_Wep_Rocket_Launchers.Rocket_Launcher_Vladof.Ak_Play_Wep_Rocket_Launcher_Vladof_Reload_Unload"
bipod_sound = DropdownOption("Bipod Sound", "Thrusters", list(BIPOD_SOUNDS), description="What planting the bipod sounds like.")
# v0.32.0: a loop while planted (user). (play event, stop events); loops with a matching Ak_Stop event stop
# reliably; the Vladof spin loop has none, so it gets its spin-down plus WillowWeapon.StopLoopingSounds().
FX_GLOBAL = "Ake_FX_Global."
BIPOD_LOOPS = {
    "Thruster Burn": (FX_GLOBAL + "Ak_Play_FX_Elemental_Incendiary_StatusEffect_lp",
                      [FX_GLOBAL + "Ak_Stop_FX_Elemental_Incendiary_StatusEffect_lp"]),
    "Energy Hum": (FX_GLOBAL + "Ak_Play_FX_Elemental_Shock_StatusEffect_lp",
                   [FX_GLOBAL + "Ak_Stop_FX_Elemental_Shock_StatusEffect_lp"]),
    "Whirr": ("Ake_Wep_Assault_Rifles.Rifle_Vladof.Ak_Play_Wep_Rifle_Vladof_Spin_Loop",
              ["Ake_Wep_Assault_Rifles.Rifle_Vladof.Ak_Play_Wep_Rifle_Vladof_Spin_Down"]),
    "Off": (None, []),
}
bipod_loop = DropdownOption("Bipod Loop", "Whirr", list(BIPOD_LOOPS), description="Sound while the bipod is planted.")
OPTIONS += [bipod_sound, bipod_loop]
_loop: dict | None = None  # {"actor": WeakPointer, "weapon": WeakPointer, "kind": str} while a loop plays


def _loop_start(weapon: UObject) -> None:
    global _loop
    from mods_base import get_pc

    _loop_stop()
    play, _stops = BIPOD_LOOPS.get(bipod_loop.value, (None, []))
    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    event = _find_obj("AkEvent", play) if play else None
    if event is None or pawn is None:
        return
    pawn.PlayAkEvent(event)
    _loop = {"actor": WeakPointer(pawn), "weapon": WeakPointer(weapon), "kind": bipod_loop.value}


def _loop_stop() -> None:
    global _loop
    if _loop is None:
        return
    actor, weapon = _loop["actor"](), _loop["weapon"]()
    _play, stops = BIPOD_LOOPS.get(_loop["kind"], (None, []))
    _loop = None
    if actor is not None:
        for path in stops:
            event = _find_obj("AkEvent", path)
            if event is not None:
                actor.PlayAkEvent(event)
    if weapon is not None:
        try:
            weapon.StopLoopingSounds()
        except Exception as e:  # noqa: BLE001
            log(f"bipod loop stop error: {e!r}")
_fx = {"p": 0.0, "eye_rest": None, "arms": None, "fov_base": None, "fov_written": None, "last": None}


def _find_obj(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _plant_fx(weapon: UObject) -> None:
    """The bipod hitting the ground: a small camera jolt and a clack."""
    from mods_base import get_pc

    pc = get_pc(possibly_loading=True)
    if pc is None:
        return
    anim = _find_obj("CameraAnim", PLANT_ANIM)
    if anim is not None:
        pc.ClientPlayCameraAnim(anim, 0.35, 1.3, 0.03, 0.15)
    for path in BIPOD_SOUNDS.get(bipod_sound.value, [PLANT_SOUND]):
        sound = _find_obj("AkEvent", path)
        if sound is not None and pc.Pawn is not None:
            pc.Pawn.PlayAkEvent(sound)
    _loop_start(weapon)


def _bipod_fx_tick(weapon: UObject | None) -> None:
    """Ease the plant pose toward 1 while the held gun's bipod is out, else back to 0."""
    from mods_base import get_pc

    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if pawn is None:
        _fx.update(p=0.0, eye_rest=None, arms=None, fov_base=None, fov_written=None)
        return
    now = game_now()
    dt = min(0.1, now - _fx["last"]) if _fx["last"] is not None else 0.016
    _fx["last"] = now
    target = 1.0 if weapon is not None and is_stance(weapon) else 0.0
    p = _fx["p"]
    if p == 0.0 and target == 0.0:
        return
    if p == 0.0:  # starting from rest: remember it
        _fx["eye_rest"] = pawn.BaseEyeHeight
        mesh = pawn.Arms.SkeletalMesh if pawn.Arms is not None else None
        if mesh is not None:
            r, o = mesh.RotOrigin, mesh.Origin
            _fx["arms"] = (WeakPointer(mesh), (r.Pitch, r.Yaw, r.Roll), (o.X, o.Y, o.Z))
        _fx["fov_base"] = pc.DesiredFOV
        _fx["fov_written"] = None
    p += (target - p) * min(1.0, BIPOD_EASE * dt)
    if abs(p - target) < 0.01:
        p = target
    _fx["p"] = p
    k = p * p * (3 - 2 * p)  # smoothstep
    if _fx["eye_rest"] is not None:
        pawn.BaseEyeHeight = _fx["eye_rest"] - BIPOD_EYE_DROP * k
    arms = _fx["arms"]
    mesh = arms[0]() if arms is not None else None
    if mesh is not None:
        rp, (ox, oy, oz) = arms[1][0], arms[2]
        mesh.RotOrigin.Pitch = int(rp + BIPOD_GUN_PITCH * k)
        mesh.Origin.X, mesh.Origin.Y, mesh.Origin.Z = ox + BIPOD_GUN[0] * k, oy + BIPOD_GUN[1] * k, oz + BIPOD_GUN[2] * k
    current = pc.DesiredFOV
    if _fx["fov_written"] is not None and not _close(current, _fx["fov_written"]):
        _fx["fov_base"] = current  # BL2 changed it (zoom, settings): build on the new value
    if _fx["fov_base"] is not None:
        pc.DesiredFOV = _fx["fov_base"] - BIPOD_FOV * k
        _fx["fov_written"] = pc.DesiredFOV
    if p == 0.0:  # fully back: hand everything back exactly
        if _fx["eye_rest"] is not None:
            pawn.BaseEyeHeight = _fx["eye_rest"]
        if mesh is not None:
            (rp, ry, rr), (ox, oy, oz) = arms[1], arms[2]
            mesh.RotOrigin.Pitch, mesh.RotOrigin.Yaw, mesh.RotOrigin.Roll = rp, ry, rr
            mesh.Origin.X, mesh.Origin.Y, mesh.Origin.Z = ox, oy, oz
        if _fx["fov_base"] is not None:
            pc.DesiredFOV = _fx["fov_base"]
        _fx.update(eye_rest=None, arms=None, fov_base=None, fov_written=None)



# --------------------------------------------------------------------------- muzzle effects (v0.30.0)
# A ParticleSystemComponent attached to the gun's own mesh at its muzzle socket
# (WeaponTypeDefinition.MuzzleFlashSocket) with the mesh's depth priority group, so it lines up with the
# first-person gun like BL2's own muzzle flashes. Looping effects are removed on stow.
MUZZLE_FX = {
    # kind: (particle, loop?, scale, repeat every s)
    # v0.30.1: the rocket deploy puffs looked like a stray little explosion and the double barrel's
    # repeated muzzle glow never showed (user); both dropped for sounds.
    "Taser": ("fx_env_sparks.Particles.Part_Shock_Small_Loop", True, 0.15, 0.0),
}
# v0.30.2: the muzzle-flash socket sits in front of the barrel (flashes bloom forward); the taser sparks
# are pulled back along the socket's X axis onto the barrel (relative translation on the attached comp).
spark_offset = SliderOption("Taser Spark Position", -86, -100, 10, 1, True,
                            description="Move the taser sparks along the barrel (negative = back toward the gun).")
spark_height = SliderOption("Taser Spark Height", 1, -40, 20, 1, True,
                            description="Move the taser sparks up (+) or down (-).")
OPTIONS += [spark_offset, spark_height]
# Deploy / stow click-clack: BL2's weapon equip sounds (Ake_Obj_Pickup.Obj_Pickup_Equip.*), by underbarrel,
# else by gun type. The double barrel also plays the gun's own barrel spin-up sound.
EQUIP = "Ake_Obj_Pickup.Obj_Pickup_Equip.Ak_Play_Obj_Pickup_Equip_"
CLACK_BY_KIND = {"Rocket Launcher": "RL", "Grenade Launcher": "RL", "Zip Rockets": "SMG", "Shotgun": "Shotgun"}
CLACK_BY_GUN = {"AR": "Rifle", "Sniper": "Rifle", "Pistol": "Pistol", "Launcher": "RL"}


def _clack(weapon: UObject, kind: str, deploying: bool) -> None:
    """The mechanical sound of the underbarrel going on / off."""
    from mods_base import get_pc

    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if pawn is None:
        return
    name = CLACK_BY_KIND.get(kind) or CLACK_BY_GUN.get(gun_type(weapon) or "", "Rifle")
    sound = _find_obj("AkEvent", EQUIP + name)
    if sound is not None:
        pawn.PlayAkEvent(sound)
    if deploying and kind == "Double Barrel":
        try:
            weapon.PlayStartSpinningUpSound()  # the gun's own barrel whirr
        except Exception as e:  # noqa: BLE001
            log(f"double barrel spin sound error: {e!r}")
_muzzle: dict[int, dict] = {}  # UniqueId -> {"comp": WeakPointer, "kind", "next": t}
_muzzle_logged: set[str] = set()


def _spark_pos():
    return unrealsdk.make_struct("Vector", X=float(spark_offset.value), Y=0.0, Z=float(spark_height.value))


def _spawn_muzzle(weapon: UObject, kind: str) -> UObject | None:
    path, _loop, scale, _every = MUZZLE_FX[kind]
    tmpl = _find_obj("ParticleSystem", path)
    mesh = weapon.FirstPersonMesh
    wtype = weapon.DefinitionData.WeaponTypeDefinition
    socket = wtype.MuzzleFlashSocket if wtype is not None else None
    if tmpl is None or mesh is None or socket is None:
        return None
    comp = unrealsdk.construct_object("ParticleSystemComponent", weapon)
    comp.SetTemplate(tmpl)
    comp.SetDepthPriorityGroup(mesh.DepthPriorityGroup)
    mesh.AttachComponentToSocket(comp, socket)
    comp.SetScale(scale)
    if kind == "Taser":
        comp.SetTranslation(_spark_pos())
    comp.ActivateSystem(True)
    if kind not in _muzzle_logged:
        _muzzle_logged.add(kind)
        log(f"Vladof {kind}: muzzle effect {tmpl.Name} on socket {socket}")
    return comp


def _muzzle_on(weapon: UObject, kind: str) -> None:
    if kind not in MUZZLE_FX:
        return
    try:
        comp = _spawn_muzzle(weapon, kind)
    except Exception as e:  # noqa: BLE001 - effects must never break the underbarrel
        log(f"muzzle effect error: {e!r}")
        return
    if comp is not None:
        _muzzle[weapon.DefinitionData.UniqueId] = {"comp": WeakPointer(comp), "kind": kind, "next": game_now() + MUZZLE_FX[kind][3], "weapon": WeakPointer(weapon)}


def _muzzle_off(uid: int) -> None:
    entry = _muzzle.pop(uid, None)
    comp = entry["comp"]() if entry is not None else None
    weapon = entry["weapon"]() if entry is not None else None
    if comp is None:
        return
    try:
        comp.DeactivateSystem()
        if weapon is not None and weapon.FirstPersonMesh is not None:
            weapon.FirstPersonMesh.DetachComponent(comp)
    except Exception as e:  # noqa: BLE001
        log(f"muzzle effect removal error: {e!r}")


def _muzzle_tick() -> None:
    """Keep the taser sparks where the slider puts them; repeat effects that repeat."""
    now = game_now()
    for uid, entry in list(_muzzle.items()):
        comp = entry["comp"]()
        if comp is not None and entry["kind"] == "Taser":
            t = comp.Translation
            if abs(t.X - spark_offset.value) > 0.01 or abs(t.Z - spark_height.value) > 0.01:
                comp.SetTranslation(_spark_pos())
        every = MUZZLE_FX[entry["kind"]][3]
        if every <= 0 or now < entry["next"]:
            continue
        entry["next"] = now + every
        comp = entry["comp"]()
        if comp is not None:
            comp.ActivateSystem(True)


HOOKS = [watch_underbarrel, manual_reload, auto_reload, mode_firing_mode, pump_refire, single_shot, no_sprint_on_bipod, draw_mortar_cursor]
