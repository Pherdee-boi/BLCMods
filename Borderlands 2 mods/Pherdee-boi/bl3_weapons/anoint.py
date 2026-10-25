"""BL3 anointments: rare+ guns roll one bonus the first time you equip them (saved per gun).

Only the HELD gun's anointment counts, and it only boosts gun damage (bullets/rockets), like BL3.

Hooks (signatures read from WillowGame.upk / Engine.upk):
  WillowAIPawn.TakeDamage(Damage, InstigatedBy, HitLocation, Momentum, DamageType, ...)  scale gun damage
  WillowPlayerController.ActionSkillCallback(ActivatedSkill, bool bActivated)              skill start/end
  WillowPlayerController.ServerThrowGrenade / ThrowGrenade(ProjectileDefinition)          grenade thrown
  WillowWeapon.ReloadDone()                                                               reload buff
  WillowWeapon.OnEquip(EventInstigator, ...)                                              roll + announce

v0.34.0 adds (BL3-style):
  slide   - On Slide: sliding = the pawn crouched (Pawn.bIsCrouched) on the ground (Physics 1) and moving
            faster than crouch-walking, so it works with BL3 Movement's slide (or any slide mod)
  slam    - After Ground Slam: BL3 Movement's bl3_movement.slam._last_slam changes (read if that mod is
            loaded; never required)
  as_mags - After Action Skill ends: the next 2 magazines (counted in shots, WillowWeapon.ConsumeAmmo)
            deal +100%; rolled on elemental guns only
  kill    - On Kill: WillowAIPawn.TakeDamage post, the player's gun hit left the enemy not IsAliveAndWell()
  low_hp  - Below 50% health: Pawn.GetHealth() / GetMaxHealth()
  streak  - Consecutive hits: +5% per gun hit, up to +60%, reset after a second without a hit
"""


from mods_base import BoolOption, DropdownOption, get_pc, hook, keybind
from ui_utils import show_hud_message
from unrealsdk.hooks import Block, Type, prevent_hooking_direct_calls
from unrealsdk.unreal import UObject

from .core import describe, held_weapon, log, maker_of, now as game_now, remembered

# id -> (card text, damage bonus)
ANOINTMENTS = {
    "as_end": ("On Action Skill End: +100% weapon damage for 10s", 1.00),
    "as_active": ("While Action Skill active: +75% weapon damage", 0.75),
    "grenade": ("On Grenade Thrown: +50% weapon damage for 15s", 0.50),
    "reload": ("On Reload: +60% weapon damage for 4s", 0.60),
    "slide": ("On Slide: +50% weapon damage for 5s", 0.50),
    "slam": ("After Ground Slam: +100% weapon damage for 6s", 1.00),
    "as_mags": ("After Action Skill ends: next 2 magazines deal +100% damage", 1.00),
    "kill": ("On Kill: +30% weapon damage for 6s", 0.30),
    "low_hp": ("Below 50% health: +50% weapon damage", 0.50),
    "streak": ("Consecutive hits: +5% weapon damage per hit, up to +60%", 0.60),
}
SLIDE_TIME, SLAM_TIME, KILL_TIME = 5.0, 6.0, 6.0
SLIDE_SPEED = 330.0  # Unreal units/s: faster than any crouch-walk
STREAK_STEP, STREAK_GAP = 0.05, 1.0
ELEMENTS = ("Fire", "Shock", "Corrosive", "Slag", "Explosive")
AS_END_TIME, GRENADE_TIME, RELOAD_TIME = 10.0, 15.0, 4.0
# Chance to be anointed by BL2 rarity level (1 white .. 4 purple; legendary-class guaranteed, see make())
CHANCE = {3: 0.20, 4: 0.50}
CHANCE_LEGENDARY = 1.0
GUN_DAMAGE = {"Bullet", "Pistol", "SubMachineGun", "Shotgun", "Sniper", "MachineGun", "Rocket"}

enabled = BoolOption(
    "Anointments",
    False,
    description="Turn BL3 anointments on/off (rolls are kept; nothing triggers while off).",
)

test_anoint = DropdownOption(
    "Test Anointment", "Off", ["Off", *(v[0] for v in ANOINTMENTS.values())],
    description="Testing: give every gun this anointment (saved rolls are kept).",
)

_as_active = False
_as_end_at = -1e9
_grenade_at = -1e9
_reload_at: dict[int, float] = {}  # UniqueId -> time of last reload
_last_grenade_event = 0.0
_slide_at = -1e9
_sliding = False
_slam_at = -1e9
_seen_slam: object = None
_kill_at = -1e9
_streak = [0, -1e9]  # hits, time of the last hit
_mag_shots: dict[int, int] = {}  # UniqueId -> shots left of the after-action-skill magazines
_recent_dead: set[int] = set()


def _rarity(weapon: UObject) -> int:
    ret = weapon.StaticCalculateWeaponRarityLevel(weapon.DefinitionData)
    return ret[0] if isinstance(ret, tuple) else ret


def anointment_of(weapon: UObject) -> str | None:
    """Roll once per gun (seeded by UniqueId) and remember it; [] means 'not anointed'."""
    if not enabled.value:
        return None
    if test_anoint.value != "Off":  # testing: every gun gets this one; saved rolls untouched
        return next(k for k, v in ANOINTMENTS.items() if v[0] == test_anoint.value)

    def make(rng):
        level = _rarity(weapon)
        # BL2's RarityLevelColors: 5 and 7-10 legendary, 500 pearl, 501 seraph, 503/506 specials;
        # 6 is e-tech, rolled like purple (v0.22.4; before, every level >= 5 counted as legendary)
        legendary = level == 5 or 7 <= level <= 10 or level >= 500
        chance = CHANCE_LEGENDARY if legendary else CHANCE.get(4 if level == 6 else level, 0.0)
        if rng.random() >= chance:
            return []
        options = sorted(
            k for k in ANOINTMENTS
            if not (k == "reload" and maker_of(weapon) == "Bandit")  # COV guns never reload
            and not (k == "as_mags" and not _is_elemental(weapon))
        )
        return [rng.choice(options)]

    saved = remembered("anoint", weapon, make)
    return saved[0] if saved else None


def _is_elemental(weapon: UObject) -> bool:
    part = weapon.DefinitionData.ElementalPartDefinition
    return part is not None and part.Name.rsplit("_", 1)[-1] in ELEMENTS


def _is_player_pc(obj: UObject) -> bool:
    pc = get_pc(possibly_loading=True)
    return pc is not None and obj is not None and obj._get_address() == pc._get_address()


def _bonus(weapon: UObject) -> float:
    kind = anointment_of(weapon)
    now = game_now()
    if kind == "as_end" and now - _as_end_at < AS_END_TIME:
        return ANOINTMENTS[kind][1]
    if kind == "as_active" and _as_active:
        return ANOINTMENTS[kind][1]
    if kind == "grenade" and now - _grenade_at < GRENADE_TIME:
        return ANOINTMENTS[kind][1]
    if kind == "reload" and now - _reload_at.get(weapon.DefinitionData.UniqueId, -1e9) < RELOAD_TIME:
        return ANOINTMENTS[kind][1]
    if kind == "slide" and now - _slide_at < SLIDE_TIME:
        return ANOINTMENTS[kind][1]
    if kind == "slam" and now - _slam_at < SLAM_TIME:
        return ANOINTMENTS[kind][1]
    if kind == "as_mags" and _mag_shots.get(weapon.DefinitionData.UniqueId, 0) > 0:
        return ANOINTMENTS[kind][1]
    if kind == "kill" and now - _kill_at < KILL_TIME:
        return ANOINTMENTS[kind][1]
    if kind == "low_hp":
        pc = get_pc(possibly_loading=True)
        pawn = pc.Pawn if pc is not None else None
        if pawn is not None and pawn.GetMaxHealth() > 0 and pawn.GetHealth() / pawn.GetMaxHealth() < 0.5:
            return ANOINTMENTS[kind][1]
    if kind == "streak":
        return min(ANOINTMENTS[kind][1], STREAK_STEP * _streak[0])
    return 0.0


def _announce(weapon: UObject, msg: str) -> None:
    if anointment_of(weapon):
        show_hud_message("Anointment", msg, 2)


@hook("WillowGame.WillowAIPawn:TakeDamage")
def scale_damage(obj: UObject, args, _ret, func) -> type[Block] | None:
    """The one place the mod scales the player's damage to enemies: anointment bonus x Torgue sticky
    stacking, re-issued once. (Two separate block-and-re-call hooks would apply a hit twice.)"""
    if not _is_player_pc(args.InstigatedBy):
        return None
    mult = 1.0
    weapon = held_weapon()
    source = args.DamageType.Name.removeprefix("WillowDmgSource_") if args.DamageType is not None else ""
    if source in GUN_DAMAGE and weapon is not None:
        now = game_now()
        _streak[0] = _streak[0] + 1 if now - _streak[1] < STREAK_GAP else 1
        _streak[1] = now
        from . import gear

        mult *= 1 + _bonus(weapon) + gear.weapon_bonus()
    if source == "Grenade":
        from . import gear

        mult *= 1 + gear.grenade_bonus()
    from . import torgue

    mult *= torgue.stack_multiplier(obj)
    from . import vladof

    from . import dahl

    mult *= dahl.damage_multiplier(weapon) if source in GUN_DAMAGE else 1.0
    from . import hyperion

    mult *= hyperion.amp_multiplier(weapon) if source in GUN_DAMAGE else 1.0
    if source in GUN_DAMAGE:
        try:
            vladof.on_player_hit(obj, args.DamageType, weapon)
        except Exception:  # noqa: BLE001
            pass
    ub = vladof.damage_multiplier(weapon)
    if ub != 1.0:
        mult *= ub
        vladof.log_hit(weapon, args.Damage * mult)
    if mult == 1.0:
        return None
    args.Damage = args.Damage * mult
    with prevent_hooking_direct_calls():
        func(args)
    return Block


@hook("WillowGame.WillowPlayerController:ActionSkillCallback", Type.POST)
def on_action_skill(obj: UObject, args, *_: object) -> None:
    global _as_active, _as_end_at
    if not _is_player_pc(obj):
        return
    _as_active = bool(args.bActivated)
    weapon = held_weapon()
    if not _as_active:
        _as_end_at = game_now()
        if weapon is not None and anointment_of(weapon) == "as_end":
            _announce(weapon, "+100% weapon damage for 10s")
        if weapon is not None and anointment_of(weapon) == "as_mags":
            _mag_shots[weapon.DefinitionData.UniqueId] = 2 * max(1, weapon.ClipSize)
            _announce(weapon, "next 2 magazines deal +100% damage")
    elif weapon is not None and anointment_of(weapon) == "as_active":
        _announce(weapon, "+75% weapon damage while your skill is active")


def _grenade(obj: UObject) -> None:
    global _grenade_at, _last_grenade_event
    now = game_now()
    if not _is_player_pc(obj) or now - _last_grenade_event < 0.5:  # client+server versions both fire
        return
    _last_grenade_event = _grenade_at = now
    from . import gear

    gear.on_grenade()
    weapon = held_weapon()
    if weapon is not None and anointment_of(weapon) == "grenade":
        _announce(weapon, "+50% weapon damage for 15s")


@hook("WillowGame.WillowPlayerController:ServerThrowGrenade", Type.POST)
def on_grenade_server(obj: UObject, *_: object) -> None:
    _grenade(obj)


@hook("WillowGame.WillowPlayerController:ThrowGrenade", Type.POST)
def on_grenade(obj: UObject, *_: object) -> None:
    _grenade(obj)


@hook("WillowGame.WillowWeapon:ReloadDone", Type.POST)
def on_reload(obj: UObject, *_: object) -> None:
    weapon = held_weapon()
    if weapon is None or weapon._get_address() != obj._get_address() or anointment_of(weapon) != "reload":
        return
    _reload_at[weapon.DefinitionData.UniqueId] = game_now()
    _announce(weapon, "+60% weapon damage for 4s")


@hook("WillowGame.WillowWeapon:ConsumeAmmo", Type.POST, hook_identifier="bl3w_anoint_shot")
def on_shot(obj: UObject, *_: object) -> None:
    uid = obj.DefinitionData.UniqueId
    left = _mag_shots.get(uid, 0)
    if left > 0:
        weapon = held_weapon()
        if weapon is not None and weapon._get_address() == obj._get_address():
            _mag_shots[uid] = left - 1


@hook("WillowGame.WillowAIPawn:TakeDamage", Type.POST_UNCONDITIONAL, hook_identifier="bl3w_anoint_kill")
def on_hit_done(obj: UObject, args, *_: object) -> None:
    """On Kill: the player's gun hit left the enemy dead."""
    global _kill_at
    if not _is_player_pc(args.InstigatedBy) or args.DamageType is None:
        return
    if args.DamageType.Name.removeprefix("WillowDmgSource_") not in GUN_DAMAGE:
        return
    addr = obj._get_address()
    if obj.IsAliveAndWell() or addr in _recent_dead:
        return
    _recent_dead.add(addr)
    if len(_recent_dead) > 256:
        _recent_dead.clear()
    _kill_at = game_now()
    from . import gear

    gear.on_kill()
    weapon = held_weapon()
    if weapon is not None and anointment_of(weapon) == "kill":
        _announce(weapon, "+30% weapon damage for 6s")


@hook("WillowGame.WillowGameViewportClient:Tick", hook_identifier="bl3w_anoint_tick")
def watch_moves(*_: object) -> None:
    """Slide (crouched on the ground and fast) and BL3 Movement's ground slam."""
    global _slide_at, _sliding, _slam_at, _seen_slam
    import math
    import sys

    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if pawn is None:
        return
    now = game_now()
    v = pawn.Velocity
    sliding = bool(pawn.bIsCrouched) and pawn.Physics == 1 and math.hypot(v.X, v.Y) > SLIDE_SPEED
    if sliding:
        _slide_at = now
        if not _sliding:
            weapon = held_weapon()
            if weapon is not None and anointment_of(weapon) == "slide":
                _announce(weapon, "+50% weapon damage for 5s")
    _sliding = sliding
    slam = sys.modules.get("bl3_movement.slam")
    last = getattr(slam, "_last_slam", None) if slam is not None else None
    if last is not None and last != _seen_slam:
        if _seen_slam is not None:  # the first read is just where it stands
            _slam_at = now
            weapon = held_weapon()
            if weapon is not None and anointment_of(weapon) == "slam":
                _announce(weapon, "+100% weapon damage for 6s")
        _seen_slam = last


@hook("WillowGame.WillowWeapon:OnEquip", Type.POST)
def on_equip(obj: UObject, *_: object) -> None:
    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc else None
    if pawn is None or obj.Owner is None or obj.Owner._get_address() != pawn._get_address():
        return
    kind = anointment_of(obj)
    log(f"equip {describe(obj)} rarity {_rarity(obj)}: {kind}")
    if kind:
        show_hud_message("Anointed", ANOINTMENTS[kind][0], 3)


@keybind("Show Anointment", "N", description="Show the held gun's BL3 anointment.")
def show_anointment() -> None:
    weapon = held_weapon()
    if weapon is None:
        return
    if not enabled.value:
        show_hud_message("Anointment", "Anointments are turned off in the mod options.", 2)
        return
    kind = anointment_of(weapon)
    show_hud_message("Anointment", ANOINTMENTS[kind][0] if kind else "This gun is not anointed.", 2.5)


HOOKS = [scale_damage, on_action_skill, on_grenade_server, on_grenade, on_reload, on_shot, on_hit_done, watch_moves, on_equip]
