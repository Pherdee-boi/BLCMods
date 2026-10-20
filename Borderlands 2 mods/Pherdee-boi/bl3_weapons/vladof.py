"""Vladof: assault rifles, pistols and sniper rifles get a BL3-style underbarrel; the mode key deploys/stows it.

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
  * Own ammo (v0.23.0): the reserve (GetAmmoCount) is noted on deploy and what the underbarrel spent is
    given back on stow (AddAmmo); the gun's magazine is restored too. Not for the fire-rate boost.
  * Damage: a multiplier per underbarrel, applied in anoint.scale_damage (the mod's single damage hook).
"""

import random

import unrealsdk
from mods_base import BoolOption, DropdownOption, GroupedOption, SliderOption, hook
from ui_utils import show_hud_message
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import UObject, WeakPointer

from .core import find_part, forget, held_weapon, log, maker_of, now as game_now, rebuild, remembered, state

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
}
KINDS = {  # which underbarrels each gun type can roll (v0.26.1, user: taser = pistols only, grenade = ARs
    # only, rocket = ARs and later snipers). A saved roll that's no longer allowed is re-rolled.
    "AR": ["Rocket Launcher", "Grenade Launcher", "Shotgun", "Double Barrel"],
    "Pistol": ["Zip Rockets", "Taser", "Double Barrel"],  # v0.26.2: zip rockets replace the pistol shotgun
    "Sniper": ["Rocket Launcher", "Shotgun", "Double Barrel", "Bipod"],  # v0.27.0, Bipod v0.28.0
}
MODES = {
    "Shotgun": {"attrs": {"ProjectilesPerShot": 8, "Spread": 7.0, "PerShotAccuracyImpulse": 12.0}, "interval": 0.55, "semi": True},
    "Rocket Launcher": {"attrs": {"ProjectilesPerShot": 1, "PerShotAccuracyImpulse": 20.0}, "interval": 1.1, "semi": True,
                        "fm": "GD_Weap_Launchers.FiringModes.FM_Rocket_Vladof"},
    "Taser": {"attrs": {"ProjectilesPerShot": 2}, "interval": 0.5, "semi": True, "shock": True},
    "Double Barrel": {"attrs": {}, "rate": 2.0, "timed": True},
    "Bipod": {"attrs": {}, "scale": {"Spread": 0.3, "PerShotAccuracyImpulse": 0.3}, "rate": 1.5, "toggle": True},
    # BL3 zip rockets: the small Vladof AR rockets (the AR Torgue-Vladof barrel's firing mode, which ARs
    # used as their rocket underbarrel up to v0.24.0), automatic, on the pistol's own barrel
    "Zip Rockets": {"attrs": {"ProjectilesPerShot": 1}, "interval": 0.25,
                    "fm": "GD_Weap_AssaultRifle.FiringModes.FM_Rocket_Vladof"},
}
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
fire_rate_time = SliderOption("Double Barrel Time", 8, 3, 20, 1, True, description="Seconds the doubled fire rate lasts.")
RENAMED = {"Fire Rate": "Double Barrel"}  # old saved names -> current
DAMAGE = {"Grenade Launcher": grenade_damage, "Rocket Launcher": rocket_damage, "Shotgun": shotgun_damage,
          "Taser": taser_damage, "Zip Rockets": zip_damage}
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
           taser_damage, taser_shots, zip_damage, zip_shots, fire_rate_time]

# This session only (not saved), keyed by UniqueId
_deployed: dict[int, int] = {}  # gun rounds loaded when deployed
_ready_at: dict[int, float] = {}  # when the cooldown ends
_forced: dict[int, str] = {}  # underbarrel forced by the option when deployed
_ammo_at_deploy: dict[int, int] = {}  # reserve when deployed (given back on stow)
_total_shots: dict[int, int] = {}  # shots in this deploy
_mode: dict[int, dict] = {}  # deployed mode: {"kind", "ref", "base": {attr: v}, "written": {attr: v}, "until"}
_hit_logged: set[str] = set()
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
        current = getattr(weapon, attr)
        if attr not in entry["written"] or not _close(current, entry["written"][attr]):
            entry["base"][attr] = current  # first time, or BL2 recomputed it
    targets = dict(mode["attrs"])
    for attr, factor in mode.get("scale", {}).items():
        targets[attr] = entry["base"][attr] * factor
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
    entry = _mode.pop(uid, None)
    if entry is not None:
        for attr, value in entry["base"].items():
            setattr(weapon, attr, value)
        if loaded is not None and not (MODES[entry["kind"]].get("timed") or MODES[entry["kind"]].get("toggle")):
            weapon.ReloadCnt = loaded
        if MODES[entry["kind"]].get("toggle"):
            _release_speed()
            used = 0.0
        _restore_element(weapon, loaded)
    else:
        rebuild(weapon, loaded=loaded, BarrelPartDefinition=find_part(original))
    _muzzle_off(uid)
    _forced.pop(uid, None)
    _total_shots.pop(uid, None)
    before = _ammo_at_deploy.pop(uid, None)
    if before is not None:
        spent = before - weapon.GetAmmoCount()
        if spent > 0:
            weapon.AddAmmo(spent)  # the underbarrel's shots don't cost the gun's ammo
            log(f"Vladof #{uid}: gave back {spent} rounds the underbarrel used")
    wait = cooldown.value * used
    if wait > 0:
        _ready_at[uid] = game_now() + wait
    log(f"Vladof #{uid}: {kind} stowed ({reason}), cooldown {wait:.0f}s")
    from . import indicator

    indicator.bump()
    if not MODES.get(kind, {}).get("toggle"):
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
    else:
        shots = SHOTS[kind].value
        if gun_type(weapon) == "Pistol" and kind not in FULL_SHOTS:
            shots = max(1, round(shots * PISTOL_SHOTS))
        _total_shots[uid] = shots
    _mode[uid] = {"kind": kind, "ref": WeakPointer(weapon), "base": {}, "written": {}, "until": game_now() + fire_rate_time.value}
    _tune_mode(weapon)
    if _total_shots[uid]:
        weapon.ReloadCnt = _total_shots[uid] * _cost(weapon)


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
    return Block if weapon is not None and is_toggle(weapon) else None


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
    if MODES.get(kind, {}).get("toggle"):
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
    if path is None:
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
    entry = _mode.get(obj.DefinitionData.UniqueId)
    if entry is not None and MODES[entry["kind"]].get("semi"):
        held = held_weapon()
        if held is not None and held._get_address() == obj._get_address():
            return Block, False
    return None


@hook("WillowGame.WillowWeapon:ConsumeAmmo", Type.POST, hook_identifier="bl3w_vladof_shot")
def single_shot(obj: UObject, *_: object) -> None:
    """v0.33.2: one shot per pull for real. Refusing ShouldRefire (pump_refire) doesn't stop the engine's
    native refire (found on Dahl semi-auto); clearing the "trigger held" flag after each shot does."""
    entry = _mode.get(obj.DefinitionData.UniqueId)
    if entry is None or not MODES[entry["kind"]].get("semi"):
        return
    held = held_weapon()
    if held is not None and held._get_address() == obj._get_address():
        obj.ClearPendingFire(0)


@hook("WillowGame.WillowGameViewportClient:Tick")
def watch_underbarrel(*_: object) -> None:
    """Auto-stow when the shots/time run out or a reload starts; keep mode attributes on; stow a mode
    whose gun was put away (its attributes must not stay); repair a taser element left by a save."""
    weapon = held_weapon()
    held_uid = weapon.DefinitionData.UniqueId if weapon is not None else None
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
    if held_uid not in _deployed:
        # Deployed in a previous session (saved while out): no free shots, just stow.
        _stow(weapon, kind, original, 0, "stowed")
        return
    if is_toggle(weapon):
        _hold_speed()
        return
    timed = time_left(weapon)
    if timed is not None:
        if timed <= 0:
            _stow(weapon, kind, original, 1, "time up")
    elif weapon.ReloadCnt <= 0 or str(weapon.GetStateName()) == "WeaponReloading":
        _stow(weapon, kind, original, 1, "empty")


def reset() -> None:
    """Put mode underbarrel attributes and the bipod pose back (mod disabled)."""
    for uid in list(_muzzle):
        _muzzle_off(uid)
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
    target = 1.0 if weapon is not None and is_toggle(weapon) else 0.0
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
CLACK_BY_GUN = {"AR": "Rifle", "Sniper": "Rifle", "Pistol": "Pistol"}


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


HOOKS = [watch_underbarrel, mode_firing_mode, pump_refire, single_shot, no_sprint_on_bipod]
