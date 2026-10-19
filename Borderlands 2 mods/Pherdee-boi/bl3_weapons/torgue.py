"""Torgue: the mode key toggles BL3's Impact / Sticky gyrojets.

Impact is BL2's normal Torgue firing. Sticky swaps the gun's firing mode for BL2's own E-tech
"Spiker" projectile (GD_Weap_Pistol.Projectiles.Projectile_Spiker), which sticks to whatever it hits.
No parts change, so saves only ever hold the real gun; the chosen mode is remembered per gun.

BL3 sticky behaviour (v0.18):
  * Stickies wait: their own fuse explosion (Spiker graph: attach -> Behavior_Delay_15 -> detonate)
    is blocked in a WillowProjectile.Explode pre-hook until we release them. (Changing the Delay
    property did nothing - like the Tediore fuse, it is read once.) STICKY_FUSE is a safety timeout.
  * Reloading detonates every sticky you have out: WillowWeapon.BeginReload / StartReload /
    PlayReloadAnim (de-duplicated). (Watching for the WeaponReloading state never fired here.)
  * Stacking: at detonation stickies are grouped by the actor they're stuck to; while each one
    target is marked for STACK_WINDOW and every sticky hit on it is scaled by 1 + STACK_BONUS x (N-1)
    (stack_multiplier(victim), applied in anoint.scale_damage - the mod's single enemy-damage hook). (Raising Projectile.Damage didn't change the explosion.)
  * Stick hit: when a sticky attaches to an enemy (WillowProjectile.AttachToActor /
    WasAttachedToActor), it takes a small one-off hit (IMPACT_PCT of the gun's per-shot damage),
    outside the stack bonus and anointments.
  * Torgue look: while held, the Spiker definition borrows the Torgue AR rocket's body, flight trail
    and explosion (GD_Weap_AssaultRifle.Projectiles.Projectile_Rocket_Torgue: BodyComposition,
    InFlightEffects, DefaultExplosionDefinition), restored when you switch away - the same
    swap-while-held trick as the Tediore throws. The explosion swap also replaces the purple alien
    boom and its lower damage.
Fired stickies are caught in WillowProjectile.InitializeFromDefinition (post) and held weakly.

Uniques whose parts carry their own firing mode (Unkempt Harold's Calla barrel, the Unicornsplosion's
Swordsplosion barrel, Nukem, Flakker, ...) keep it: sticky would replace their special shot.

Hook: WillowWeapon.GetFiringModeDefinition() -> FiringModeDefinition (read from WillowGame.upk).
"""

import copy

import unrealsdk
from mods_base import SliderOption, get_pc, hook
from ui_utils import show_hud_message
from unrealsdk.hooks import Block, Type, prevent_hooking_direct_calls
from unrealsdk.unreal import UObject, WeakPointer

from .core import describe, held_weapon, log, maker_of, now as game_now, parts_of, state

STICKY_FM = "GD_Weap_Pistol.FiringModes.Bullet_Pistol_Spiker"
SPIKER = "GD_Weap_Pistol.Projectiles.Projectile_Spiker"
LOOK_FROM = "GD_Weap_AssaultRifle.Projectiles.Projectile_Rocket_Torgue"
STICKY_FUSE = 12.0  # s: stickies pop on their own after this if you never reload
# Part firing modes that are just plain Torgue gyrojets (fine to override)
PLAIN_FMS = {"GD_Weap_AssaultRifle.FiringModes.FM_Rocket_Torgue"}

stack_bonus = SliderOption(
    "Sticky Stack Bonus",
    25,
    0,
    100,
    5,
    True,
    description="Extra detonation damage per additional sticky on the same target (percent).",
)
impact_pct = SliderOption(
    "Sticky Impact Damage",
    10,
    0,
    50,
    1,
    True,
    description="Small hit when a sticky attaches to an enemy, in percent of the gun's per-shot damage (doesn't stack).",
)
OPTIONS = [stack_bonus, impact_pct]

_sticky_fm: WeakPointer | None = None
_hook_seen = False
_stuck: list[tuple[WeakPointer, float]] = []  # fired stickies still out: (projectile, fired at)
_overridden = False
_originals: dict[str, object] = {}
_releasing: set[int] = set()  # projectiles allowed to explode right now
# victim address -> (multiplier, until): explosion damage lands a moment after Detonate() returns
# (v0.18.1 scaled only 1 of 16 hits with a window around the call), so the bonus sticks to the target
STACK_WINDOW = 0.75  # s
_stack_targets: dict[int, tuple[float, float]] = {}
_scaled_hits = 0
_last_reload = -1e9
_logged_detonations = 0
_shot_damage: dict[int, float] = {}  # sticky address -> the gun's per-shot damage when fired
_impacted: set[int] = set()  # stickies that already dealt their stick hit
_logged_impact = False


def _key(weapon: UObject) -> str:
    return f"torgue:{weapon.DefinitionData.UniqueId}"


def special_fire(weapon: UObject) -> UObject | None:
    """The unique firing mode one of this gun's parts brings, if any."""
    for part in parts_of(weapon):
        fm = part.CustomFiringModeDefinition
        if fm is not None and fm._path_name() not in PLAIN_FMS:
            return fm
    return None


def _is_sticky(weapon: UObject) -> bool:
    return state.value.get(_key(weapon)) == ["sticky"]


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _sticky() -> UObject | None:
    global _sticky_fm
    if _sticky_fm is not None and _sticky_fm() is None:
        _sticky_fm = None
    if _sticky_fm is None:
        fm = _find("FiringModeDefinition", STICKY_FM)
        if fm is None:
            log(f"{STICKY_FM} not loaded")
        else:
            _sticky_fm = WeakPointer(fm)
    return _sticky_fm() if _sticky_fm is not None else None


def _sticky_gun_held() -> UObject | None:
    weapon = held_weapon()
    if weapon is None or maker_of(weapon) != "Torgue" or not _is_sticky(weapon) or special_fire(weapon) is not None:
        return None
    return weapon


def stuck_count() -> int:
    global _stuck
    _stuck = [(w, t) for w, t in _stuck if w() is not None]
    return len(_stuck)


def switch(weapon: UObject) -> None:
    special = special_fire(weapon)
    if special is not None:
        log(f"Torgue {describe(weapon)}: unique fire {special.Name}, sticky disabled")
        show_hud_message("Torgue", "This gun's special shot can't go sticky.", 1.5)
        return
    if _sticky() is None:
        show_hud_message("Torgue", "Sticky gyrojets unavailable (projectile not loaded).", 1.5)
        return
    sticky = not _is_sticky(weapon)
    if sticky:
        state.value = {**state.value, _key(weapon): ["sticky"]}
    else:
        state.value = {k: v for k, v in state.value.items() if k != _key(weapon)}
    state.save()
    log(f"Torgue {describe(weapon)}: {'sticky' if sticky else 'impact'}")
    from . import indicator

    indicator.bump()
    indicator.flick()  # quick gun flick on sticky / impact switch


@hook("WillowGame.WillowWeapon:GetFiringModeDefinition")
def sticky_firing_mode(obj: UObject, *_: object) -> tuple[type[Block], UObject] | None:
    global _hook_seen
    if not _hook_seen:
        _hook_seen = True
        log("GetFiringModeDefinition hook is live")
    fm = _sticky_fm() if _sticky_fm is not None else None
    if fm is None or not _is_sticky(obj):
        return None
    held = held_weapon()
    if held is None or held._get_address() != obj._get_address() or special_fire(obj) is not None:
        return None
    return Block, fm


# --------------------------------------------------------------------------- BL3 sticky behaviour

def _set_overrides(on: bool) -> None:
    """Torgue look + Torgue explosion on the shared Spiker definition, only while a sticky Torgue is held."""
    global _overridden
    if on == _overridden:
        return
    spiker, look = _find("ProjectileDefinition", SPIKER), _find("ProjectileDefinition", LOOK_FROM)
    if on:
        if spiker is None:
            return
        _originals.clear()
        if look is not None:
            _originals["body"] = copy.copy(spiker.BodyComposition)
            _originals["trail"] = spiker.InFlightEffects
            _originals["trail_scale"] = spiker.InFlightEffectsScale
            _originals["boom"] = spiker.DefaultExplosionDefinition
            spiker.BodyComposition = copy.copy(look.BodyComposition)
            spiker.InFlightEffects = look.InFlightEffects
            spiker.InFlightEffectsScale = look.InFlightEffectsScale
            spiker.DefaultExplosionDefinition = look.DefaultExplosionDefinition
        else:
            log(f"Torgue sticky: {LOOK_FROM} not loaded, keeping the Spiker look")
        _overridden = True
    else:
        if spiker is not None and "body" in _originals:
            spiker.BodyComposition = _originals["body"]
            spiker.InFlightEffects = _originals["trail"]
            spiker.InFlightEffectsScale = _originals["trail_scale"]
            spiker.DefaultExplosionDefinition = _originals["boom"]
        _originals.clear()
        _overridden = False


def restore() -> None:
    _set_overrides(False)


@hook("WillowGame.WillowProjectile:InitializeFromDefinition", Type.POST, hook_identifier="bl3w_torgue_track")
def track_sticky(obj: UObject, args, *_: object) -> None:
    if args.InDefinition is None or args.InDefinition.Name != "Projectile_Spiker" or _sticky_gun_held() is None:
        return
    pc = get_pc(possibly_loading=True)
    if pc is None or pc.Pawn is None or obj.Instigator is None or obj.Instigator._get_address() != pc.Pawn._get_address():
        return
    _stuck.append((WeakPointer(obj), game_now()))
    weapon = held_weapon()
    if weapon is not None:
        _shot_damage[obj._get_address()] = weapon.InstantHitDamage * max(1, weapon.ProjectilesPerShot)
    _impacted.discard(obj._get_address())  # projectiles are pooled: a reused one may stick again


def _tracked(obj: UObject) -> bool:
    addr = obj._get_address()
    for w, _t in _stuck:
        p = w()
        if p is not None and p._get_address() == addr:
            return True
    return False


def _stick_hit(proj: UObject, target: UObject | None) -> None:
    """One small, non-stacking hit on the enemy a sticky just attached to."""
    global _logged_impact
    addr = proj._get_address()
    if addr in _impacted or not _tracked(proj) or impact_pct.value <= 0:
        return
    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if target is None or pawn is None or not target.Class._inherits(unrealsdk.find_class("Pawn")):
        return
    if not target.IsAliveAndWell() or not pawn.IsEnemy(target):
        return
    _impacted.add(addr)
    damage = _shot_damage.get(addr, 0.0) * impact_pct.value / 100
    if damage <= 0:
        return
    with prevent_hooking_direct_calls():  # no anointment / stack bonus on the stick hit
        target.TakeDamage(
            damage,
            pc,
            target.Location,
            unrealsdk.make_struct("Vector", X=0, Y=0, Z=0),
            unrealsdk.find_class("WillowDmgSource_Bullet"),
        )
    if not _logged_impact:
        _logged_impact = True
        log(f"Torgue sticky: stick hit {damage:.0f} on {target.Class.Name}")


@hook("WillowGame.WillowProjectile:AttachToActor", Type.POST, hook_identifier="bl3w_torgue_attach")
def on_attach(obj: UObject, args, *_: object) -> None:
    _stick_hit(obj, args.ActorToAttachTo)


@hook("WillowGame.WillowProjectile:WasAttachedToActor", Type.POST, hook_identifier="bl3w_torgue_attached")
def on_attached(obj: UObject, *_: object) -> None:
    _stick_hit(obj, obj.GetAttachedToActor())


@hook("WillowGame.WillowProjectile:Explode", hook_identifier="bl3w_torgue_hold")
def hold_sticky(obj: UObject, *_: object) -> type[Block] | None:
    """A sticky's own fuse can't set it off; only reload (or the safety timeout) does."""
    if obj._get_address() in _releasing or not _tracked(obj):
        return None
    return Block


def detonate_all(reason: str) -> int:
    """Pop every sticky that's out, stacking damage per target. Returns how many."""
    global _logged_detonations, _scaled_hits
    live = []
    for w, _t in _stuck:
        p = w()
        if p is not None:
            live.append(p)
    _stuck.clear()
    _shot_damage.clear()
    if not live:
        return 0
    groups: dict[int, list[UObject]] = {}
    for proj in live:
        target = proj.GetAttachedToActor() if proj.IsAttached() else None
        key = target._get_address() if target is not None else -proj._get_address()
        groups.setdefault(key, []).append(proj)
    _scaled_hits = 0
    until = game_now() + STACK_WINDOW
    for key, members in groups.items():
        if key > 0 and len(members) > 1:  # stuck to an actor (not the world) with company
            _stack_targets[key] = (1 + stack_bonus.value / 100 * (len(members) - 1), until)
        for proj in members:
            _releasing.add(proj._get_address())
            try:
                proj.Detonate()
            finally:
                _releasing.discard(proj._get_address())
    if _logged_detonations < 4:
        _logged_detonations += 1
        sizes = sorted((len(m) for m in groups.values()), reverse=True)
        log(f"Torgue sticky: {reason}: {len(live)} detonated, per target {sizes}")
    return len(live)


def stack_multiplier(victim: UObject) -> float:
    """Damage multiplier for the player's hit on `victim`: >1 for STACK_WINDOW after the stickies stuck
    to it were detonated. Applied by anoint.scale_damage, the mod's single enemy-damage hook."""
    global _scaled_hits
    if not _stack_targets:
        return 1.0
    now = game_now()
    for addr in [a for a, (_m, until) in _stack_targets.items() if until < now]:
        del _stack_targets[addr]
    entry = _stack_targets.get(victim._get_address())
    if entry is None:
        return 1.0
    _scaled_hits += 1
    if _scaled_hits in (1, 4, 16):
        log(f"Torgue sticky: stacked hit #{_scaled_hits} x{entry[0]:.2f}")
    return entry[0]


def _on_reload(obj: UObject) -> None:
    global _last_reload
    weapon = _sticky_gun_held()
    if weapon is None or weapon._get_address() != obj._get_address():
        return
    now = game_now()
    if now - _last_reload < 0.4:  # BeginReload / StartReload / PlayReloadAnim all fire for one reload
        return
    _last_reload = now
    if stuck_count() > 0:
        detonate_all("reload")


@hook("WillowGame.WillowWeapon:BeginReload", Type.POST, hook_identifier="bl3w_torgue_reload1")
def reload_begin(obj: UObject, *_: object) -> None:
    _on_reload(obj)


@hook("WillowGame.WillowWeapon:StartReload", Type.POST, hook_identifier="bl3w_torgue_reload2")
def reload_start(obj: UObject, *_: object) -> None:
    _on_reload(obj)


@hook("WillowGame.WillowWeapon:PlayReloadAnim", Type.POST, hook_identifier="bl3w_torgue_reload3")
def reload_anim(obj: UObject, *_: object) -> None:
    _on_reload(obj)


@hook("WillowGame.WillowGameViewportClient:Tick", hook_identifier="bl3w_torgue_tick")
def sticky_tick(*_: object) -> None:
    _set_overrides(_sticky_gun_held() is not None)
    if _stuck and game_now() - min(t for _w, t in _stuck) > STICKY_FUSE:  # safety timeout
        detonate_all("timeout")


HOOKS = [sticky_firing_mode, track_sticky, on_attach, on_attached, hold_sticky, reload_begin, reload_start, reload_anim, sticky_tick]
