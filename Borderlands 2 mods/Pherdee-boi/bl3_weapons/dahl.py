"""Dahl: the mode key cycles BL3-style fire modes, saved per gun.

  Default  - BL2 as-is (ARs/SMGs full-auto; every Dahl bursts when aimed)
  Burst    - 3-round bursts from the hip too, using BL2's own burst system
             (WillowWeapon.AutomaticBurstCount, BurstInterval from the weapon type)
  Semi     - one shot per trigger pull, even when aimed

AutomaticBurstCount is an attribute that BL2 recomputes on zoom in/out, so the chosen value is
re-applied every frame while the gun is held.

Burst speed (v0.20.1): BL2 spaces burst shots by the gun's own FireInterval, so in burst mode the shots
inside a burst use BURST_SHOT_INTERVAL and the pause after a burst (WillowWeapon.BurstInterval) grows to
keep the gun's sustained fire rate about the same.

True semi-auto (v0.33.1): refusing ShouldRefire() or BeginFire() from Python didn't stop full-auto (user,
v0.33.0): the engine decides refire in native code that doesn't go through those hooks. What works is the
UE3 way of ending fire: after each shot (WillowWeapon.ConsumeAmmo, which fires per shot and is hookable,
as the COV heat uses) the gun's "trigger held" flag is cleared with Weapon.ClearPendingFire(0), so it stops
as if the button were released; the next press sets it again.

Aiming (v0.33.1): BL2's own aim-down-sights burst is a zoom attribute effect applied when you zoom
(WillowWeapon.ApplyAllZoomWeaponAttributeEffects / RemoveAllZoomWeaponAttributeEffects), which overrode the
chosen mode until the next frame and shots fired in between (user: "when aiming the firing mode changes").
The mode is now re-applied right after those calls and on every shot, besides every frame.

Mode bonuses (v0.33.0, BL3-style, so the slower modes keep up with full-auto): semi-auto and burst get a
damage bonus (applied in anoint.scale_damage, the mod's single damage hook) and tighter spread (the
Spread attribute, held every frame). Every attribute the mod changes is read from the gun each time BL2
recomputes it and restored when the gun leaves the mode or isn't held.
"""

from mods_base import SliderOption, hook
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import UObject, WeakPointer

from .core import describe, held_weapon, log, active_maker_of as maker_of, state

MODES = ("default", "burst", "semi")
BURST = 3
BURST_SHOT_INTERVAL = 0.07  # s between shots inside a burst (~860 rpm)
SPREAD = {"burst": 0.8, "semi": 0.6}  # spread multiplier per mode

semi_damage = SliderOption("Dahl Semi-Auto Damage", 140, 100, 250, 5, True, description="Dahl semi-auto damage, percent.")
burst_damage = SliderOption("Dahl Burst Damage", 115, 100, 200, 5, True, description="Dahl burst damage, percent.")
OPTIONS = [semi_damage, burst_damage]

# weapon address -> {"ref": WeakPointer, "mode": str, "base": {attr: value}, "written": {attr: value}}
_tuned: dict[int, dict] = {}


def _key(weapon: UObject) -> str:
    return f"dahl:{weapon.DefinitionData.UniqueId}"


def mode_of(weapon: UObject) -> str:
    saved = state.value.get(_key(weapon))
    return saved[0] if saved else "default"


def _label(weapon: UObject, mode: str) -> str:
    if mode == "default":
        wtype = weapon.DefinitionData.WeaponTypeDefinition
        auto = wtype is not None and ("AssaultRifle" in wtype.Name or "SMG" in wtype.Name)
        return "FULL-AUTO" if auto else "DEFAULT"
    return {"burst": f"{BURST}-ROUND BURST", "semi": "SEMI-AUTO"}[mode]


def switch(weapon: UObject) -> None:
    mode = MODES[(MODES.index(mode_of(weapon)) + 1) % len(MODES)]
    if mode == "default":
        state.value = {k: v for k, v in state.value.items() if k != _key(weapon)}
    else:
        state.value = {**state.value, _key(weapon): [mode]}
    state.save()
    log(f"Dahl {describe(weapon)}: {mode}")
    from . import indicator

    indicator.bump()


def damage_multiplier(weapon: UObject | None) -> float:
    """Semi-auto / burst damage bonus for the held Dahl gun (1.0 otherwise)."""
    if weapon is None or maker_of(weapon) != "Dahl":
        return 1.0
    mode = mode_of(weapon)
    if mode == "semi":
        return semi_damage.value / 100
    if mode == "burst":
        return burst_damage.value / 100
    return 1.0


def _held_dahl_mode() -> tuple[UObject | None, str]:
    weapon = held_weapon()
    if weapon is None or maker_of(weapon) != "Dahl":
        return None, "default"
    return weapon, mode_of(weapon)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-4 * max(1.0, abs(b))


def _restore(keep: int | None = None) -> None:
    """Give every tuned gun except `keep` its own values back (unconditionally: v0.20.1 only restored
    values still matching what it wrote, and a gun came back with a stretched burst pause as its own)."""
    for addr in [a for a in _tuned if a != keep]:
        entry = _tuned.pop(addr)
        weapon = entry["ref"]()
        if weapon is None:
            continue
        for attr, value in entry["base"].items():
            setattr(weapon, attr, value)


def _tune(weapon: UObject, mode: str) -> None:
    """Hold this mode's attributes on the held gun, re-reading BL2's own values when it recomputes them."""
    addr = weapon._get_address()
    entry = _tuned.get(addr)
    if entry is None or entry["mode"] != mode:
        if entry is not None:
            _restore()  # mode changed: start from the gun's own values
        entry = {"ref": WeakPointer(weapon), "mode": mode, "base": {}, "written": {}}
        _tuned[addr] = entry
    attrs = ["Spread"] + (["FireInterval", "BurstInterval"] if mode == "burst" else [])
    for attr in attrs:
        current = getattr(weapon, attr)
        if attr not in entry["written"] or not _close(current, entry["written"][attr]):
            entry["base"][attr] = current  # first time, or BL2 recomputed it (zoom, skills...)
    targets = {"Spread": entry["base"]["Spread"] * SPREAD[mode]}
    if mode == "burst":
        base_fire, base_burst = entry["base"]["FireInterval"], entry["base"]["BurstInterval"]
        shot = min(base_fire, BURST_SHOT_INTERVAL)
        # sustained rate as before: BURST shots per BURST * base_fire seconds
        targets["FireInterval"] = shot
        targets["BurstInterval"] = max(base_burst, BURST * base_fire - BURST * shot)
    for attr, value in targets.items():
        setattr(weapon, attr, value)
        entry["written"][attr] = getattr(weapon, attr)


@hook("WillowGame.WillowGameViewportClient:Tick")
def apply_mode(*_: object) -> None:
    weapon, mode = _held_dahl_mode()
    _restore(keep=weapon._get_address() if weapon is not None and mode != "default" else None)
    if weapon is None or mode == "default":
        return
    _apply(weapon, mode)


@hook("WillowGame.WillowWeapon:ShouldRefire")
def semi_refire(obj: UObject, *_: object) -> tuple[type[Block], bool] | None:
    weapon, mode = _held_dahl_mode()
    if mode != "semi" or weapon._get_address() != obj._get_address():
        return None
    return Block, False


def _apply(weapon: UObject, mode: str) -> None:
    """Put the mode's burst count and tuned attributes on the gun now."""
    want = BURST if mode == "burst" else 0
    if weapon.AutomaticBurstCount != want:
        weapon.AutomaticBurstCount = want
    _tune(weapon, mode)


def _held_match(obj: UObject) -> str | None:
    weapon, mode = _held_dahl_mode()
    if mode == "default" or weapon is None or weapon._get_address() != obj._get_address():
        return None
    return mode


@hook("WillowGame.WillowWeapon:ApplyAllZoomWeaponAttributeEffects", Type.POST)
def after_zoom_in(obj: UObject, *_: object) -> None:
    mode = _held_match(obj)
    if mode is not None:
        _apply(obj, mode)


@hook("WillowGame.WillowWeapon:RemoveAllZoomWeaponAttributeEffects", Type.POST)
def after_zoom_out(obj: UObject, *_: object) -> None:
    mode = _held_match(obj)
    if mode is not None:
        _apply(obj, mode)


@hook("WillowGame.WillowWeapon:ConsumeAmmo", Type.POST, hook_identifier="bl3w_dahl_shot")
def after_shot(obj: UObject, *_: object) -> None:
    """Per shot: re-assert the mode; in semi-auto, end firing as if the trigger were released."""
    mode = _held_match(obj)
    if mode is None:
        return
    _apply(obj, mode)
    if mode == "semi":
        obj.ClearPendingFire(0)


def reset() -> None:
    _restore()


HOOKS = [apply_mode, semi_refire, after_zoom_in, after_zoom_out, after_shot]
