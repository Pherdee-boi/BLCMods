"""Tediore: every normal Tediore gun rolls a BL3-style reload throw.

  Turret     - the thrown gun hovers and shoots the nearest visible enemy, then blows (built by this mod)
  MIRV       - Baby Maker throw (splits into child guns)
  Chaser     - Deliverance throw (chases enemies, then blows)
  Bouncing   - Bunny throw (bounces before blowing)

Each Tediore weapon type spawns its thrown gun from a Behavior_SpawnProjectile in the type's
BehaviorProviderDefinition (e.g. WeaponType_Tediore_Pistol:BehaviorProviderDefinition_6
.Behavior_SpawnProjectile_4 -> Projectile_TedioreReloadPistol). While a Tediore gun is held, the
"base" spawn behaviours of its type point at the gun's rolled projectile; they're restored as soon as
it isn't held. Uniques (Gunerang, Baby Maker, Deliverance, ...) use their own separate spawn
behaviours, so they keep their throws. No parts or saves change.

Turret keeps the base throw. WillowProjectile.InitializeFromDefinition (post) catches it; every
Tick after TURRET_FREEZE it is pinned in the air (SetLocation + zero Velocity: SetPhysics alone did
not hold it), it fires a real bullet (WillowWeapon.Behavior_Fire with the gun's own firing mode) at
the nearest enemy every TURRET_INTERVAL, so walls stop shots physically, and Detonate()s at the end. Its own explosions (the base throw's 1.7 s fuse,
Behavior_Delay) are blocked in a WillowProjectile.Explode pre-hook until then.
(Earlier tries: the Avenger throw is only a bouncing damage pulse; the Deliverance's FireShot never
fired from other guns.)

v0.36.0 (user test):
  * Turret: the thrown gun kept tumbling while it hovered. Its spin (Actor.RotationRate) is zeroed and it
    is turned to face its target (SetRotation) when it fires.
  * Chaser: the Deliverance projectile homes and hits but deals 0 on other guns (its damage comes from the
    Deliverance itself). Its explosion (WillowProjectile.Explode pre) now deals the throw's damage to
    enemies within CHASER_RADIUS (collected first, then damaged; Bullet damage source).
  * MIRV: the Baby Maker split made just one more blast. The first explosion is held; MIRV_CHILDREN Torgue
    gyrojet rounds (they explode on any impact) are fired from it at points scattered around it, then it
    blows. Each child does MIRV_CHILD_SHARE of the throw's damage.
  Throw damage (v0.36.2, user: "same damage no matter the ammo"): the gun's shot damage x the rounds that
  were in the magazine at the throw, like BL2/BL3 Tediore throws (at least MIN_THROW_ROUNDS). A Tediore
  reload never goes through WillowWeapon.BeginReload (v0.36.1 logged "None rounds left" every time), so
  the held gun's ReloadCnt is sampled every frame and the throw takes the lowest value of the last
  ROUNDS_WINDOW seconds: the magazine just before BL2 refilled it.
"""


import unrealsdk
from mods_base import DropdownOption, get_pc, hook
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import UObject, WeakPointer

from .core import describe, forget, held_frame, held_weapon, log, active_maker_of as maker_of, now as game_now, pawns, remembered

BASE_THROWS = {
    "Projectile_TedioreReloadPistol",
    "Projectile_TedioreReloadSMG",
    "Projectile_TedioreReloadShotgun",
    "Projectile_TedioreReloadLauncher",
}
THROWS = {
    "Turret": None,  # base throw + this mod's turret logic
    "MIRV": "GD_Weap_SMG.Projectiles.Projectile_TedioreReloadSMG_BabyMaker",
    "Chaser": "GD_Weap_Shotgun.Projectiles.Projectile_TedioreReloadShotgun_Legendary",
    "Bouncing": "GD_Weap_Launchers.Projectiles.Projectile_TedioreReloadLauncher_Bunny",
}
force_throw = DropdownOption(
    "Tediore Throw Type", "Random", ["Random", *THROWS],
    description="Random = each gun's own saved roll. Forcing a throw doesn't change any saved roll.",
)
OPTIONS = [force_throw]
CHASER_RADIUS = 300.0  # units
MIRV_CHILDREN = 5
MIRV_CHILD_SHARE = 0.45
MIRV_SCATTER = (250.0, 400.0)  # units from the main blast
MIRV_CHILD_RADIUS = 300.0  # v0.36.4: each child's own blast (a gyrojet's is tiny: "they hit nothing", user)
MIRV_POP = 200.0  # v0.36.3: the bomb pops up this far before splitting, so the children fly down and out over
# the enemy it hit instead of exploding on contact inside it (user)
GYROJET = "GD_Weap_AssaultRifle.FiringModes.Bullets_Assault_Torgue_GyroJet"
TURRET_FREEZE = 0.35  # s after the throw before it hovers
TURRET_TIME = 5.0  # s of shooting
TURRET_INTERVAL = 0.2  # s between shots (user: 0.6 was too slow)
TURRET_RANGE = 3000.0  # Unreal units
PHYS_NONE = 0

# Held weakly: weapon packages can unload on level change / quit-to-menu.
_spawners: dict[str, list[tuple[WeakPointer, WeakPointer]]] = {}  # weapon type path -> [(behaviour, original projectile)]
_fuses: dict[str, list[WeakPointer]] = {}  # base projectile path -> its Behavior_Delay fuses
_fuse_originals: dict[str, float] = {}
_active: str | None = None  # weapon type path currently overridden
_active_throw: str | None = None
_turrets: list[dict] = []
_thrown: list[dict] = []
_children: dict[int, tuple[WeakPointer, float]] = {}  # child projectile address -> (pointer, damage)
_expect_child: float | None = None  # damage for the projectile Behavior_Fire is spawning right now
_pending: list[tuple[float, object, float]] = []  # fallback child blasts: (time, where, damage)
_fallback_logged = False  # chaser / MIRV throws: {"kind", "proj", "weapon", "damage", "children", "next", "ending"}


def _projectile(path: str) -> UObject | None:
    try:
        return unrealsdk.find_object("ProjectileDefinition", path)
    except ValueError:
        return None


def _base_spawners(wtype: UObject) -> list[tuple[UObject, UObject]]:
    """Base thrown-gun spawn behaviours of a weapon type, found once and cached."""
    key = wtype._path_name()
    live = _live_spawners(key)
    if live is None:
        found = []
        for beh in unrealsdk.find_all("Behavior_SpawnProjectile", exact=False):
            proj = beh.ProjectileDefinition
            if proj is not None and proj.Name in BASE_THROWS and beh._path_name().startswith(key + ":"):
                found.append((WeakPointer(beh), WeakPointer(proj)))
        _spawners[key] = found
        log(f"Tediore {wtype.Name}: {len(found)} base throw behaviour(s)")
        live = _live_spawners(key) or []
    return live


def _live_spawners(key: str) -> list[tuple[UObject, UObject]] | None:
    """Cached spawners of a weapon type, or None if not cached / any of them is gone."""
    if key not in _spawners:
        return None
    pairs = [(b(), p()) for b, p in _spawners[key]]
    if any(b is None or p is None for b, p in pairs):
        del _spawners[key]
        return None
    return pairs


def _fuses_of(proj: UObject) -> list[UObject]:
    key = proj._path_name()
    live = [w() for w in _fuses.get(key, [])]
    if key not in _fuses or any(f is None for f in live):
        _fuses[key] = [WeakPointer(d) for d in unrealsdk.find_all("Behavior_Delay", exact=False) if d._path_name().startswith(key + ":")]
        live = [w() for w in _fuses[key]]
    return [f for f in live if f is not None]


def _stretch_fuses(wtype: UObject | None, on: bool) -> None:
    if on and wtype is not None:
        for _beh, proj in _base_spawners(wtype):
            for fuse in _fuses_of(proj):
                _fuse_originals.setdefault(fuse._path_name(), fuse.Delay)
                fuse.Delay = TURRET_FREEZE + TURRET_TIME + 0.5
    elif not on:
        for fuses in _fuses.values():
            for w in fuses:
                fuse = w()
                if fuse is not None and fuse._path_name() in _fuse_originals:
                    fuse.Delay = _fuse_originals.pop(fuse._path_name())


def throw_of(weapon: UObject) -> str:
    if force_throw.value in THROWS:  # testing (v0.35.1)
        return force_throw.value
    kind = remembered("tediore", weapon, lambda rng: [rng.choice(sorted(THROWS))])[0]
    if kind not in THROWS:  # rolled before a throw was retired (v0.10.0 Boomerang): roll again
        forget("tediore", weapon)
        kind = remembered("tediore", weapon, lambda rng: [rng.choice(sorted(THROWS))])[0]
    return kind


def restore() -> None:
    global _active, _active_throw
    if _active is not None:
        for beh, original in _live_spawners(_active) or []:  # skip it if the package unloaded
            beh.ProjectileDefinition = original
    _stretch_fuses(None, False)
    _active = _active_throw = None


def switch(weapon: UObject) -> None:
    from . import indicator

    indicator.bump()


@hook("WillowGame.WillowGameViewportClient:Tick")
def apply_throw(*_: object) -> None:
    global _active, _active_throw
    _tick_turrets()
    _tick_thrown()
    weapon = held_frame()
    wtype = weapon.DefinitionData.WeaponTypeDefinition if weapon is not None else None
    if weapon is None or wtype is None or maker_of(weapon) != "Tediore":
        restore()
        return
    _sample_rounds(weapon)
    kind = throw_of(weapon)
    key = wtype._path_name()
    if _active == key and _active_throw == kind:
        return
    restore()
    if kind == "Turret":
        _stretch_fuses(wtype, True)
    else:
        proj = _projectile(THROWS[kind])
        if proj is None:
            log(f"Tediore {describe(weapon)}: {THROWS[kind]} not loaded, normal throw")
        else:
            for beh, _original in _base_spawners(wtype):
                beh.ProjectileDefinition = proj
    _active, _active_throw = key, kind  # also when not loaded, so this logs once


@hook("WillowGame.WillowProjectile:InitializeFromDefinition", Type.POST)
def on_throw(obj: UObject, args, *_: object) -> None:
    if _expect_child is not None:  # a MIRV child being fired right now
        _children[obj._get_address()] = (WeakPointer(obj), _expect_child)
        return
    if _active_throw in ("Chaser", "MIRV") and args.InDefinition is not None:
        _track_thrown(obj, args.InDefinition)
        return
    if _active_throw != "Turret" or args.InDefinition is None or args.InDefinition.Name not in BASE_THROWS:
        return
    pc = get_pc(possibly_loading=True)
    weapon = held_weapon()
    if pc is None or pc.Pawn is None or weapon is None or obj.Instigator is None:
        log(f"Tediore turret: throw {args.InDefinition.Name} skipped (instigator {obj.Instigator})")
        return
    if obj.Instigator._get_address() != pc.Pawn._get_address():
        return
    log(f"Tediore turret: deployed from {describe(weapon)}")
    now = game_now()
    _turrets.append(
        {
            "proj": WeakPointer(obj),
            "start": now,
            "next": now + TURRET_FREEZE + 0.1,
            "frozen": None,  # hover location once pinned
            "ending": False,
            "damage": weapon.InstantHitDamage * max(1, weapon.ProjectilesPerShot),
            "weapon": WeakPointer(weapon),
            "shots": 0,
        },
    )


MIN_THROW_ROUNDS = 1
ROUNDS_WINDOW = 0.6  # s
_rounds: dict[int, list[tuple[float, int]]] = {}  # UniqueId -> recent (time, ReloadCnt) samples


def _sample_rounds(weapon: UObject) -> None:
    uid = weapon.DefinitionData.UniqueId
    now = game_now()
    samples = [x for x in _rounds.get(uid, []) if now - x[0] <= ROUNDS_WINDOW]
    samples.append((now, int(weapon.ReloadCnt)))
    _rounds[uid] = samples
    for other in [u for u in _rounds if u != uid]:
        del _rounds[other]


def rounds_at_throw(weapon: UObject) -> int:
    samples = _rounds.get(weapon.DefinitionData.UniqueId)
    return min(n for _t, n in samples) if samples else weapon.ClipSize // 2


def _throw_damage(weapon: UObject) -> float:
    shot = weapon.InstantHitDamage * max(1, weapon.ProjectilesPerShot)
    return shot * max(MIN_THROW_ROUNDS, rounds_at_throw(weapon))


def _track_thrown(obj: UObject, definition: UObject) -> None:
    """Remember the player's Chaser / MIRV throw so its explosion can be handled."""
    pc = get_pc(possibly_loading=True)
    weapon = held_weapon()
    if pc is None or pc.Pawn is None or weapon is None or obj.Instigator is None:
        return
    if obj.Instigator._get_address() != pc.Pawn._get_address():
        return
    if definition._path_name() != THROWS[_active_throw]:
        return
    _thrown.append({
        "kind": _active_throw, "proj": WeakPointer(obj), "weapon": WeakPointer(weapon),
        "damage": _throw_damage(weapon), "children": 0, "next": 0.0, "ending": False,
    })
    rounds = rounds_at_throw(weapon)
    log(f"Tediore {_active_throw}: thrown from {describe(weapon)} with {rounds} rounds left ({_thrown[-1]['damage']:.0f} dmg)")


def _area_damage(pc: UObject, where, radius: float, damage: float) -> int:
    """Damage enemies near `where` (targets collected before any damage: killing during a PawnList
    walk froze the game in BL3 Movement's slam)."""
    targets = []
    for pawn in pawns(pc.Pawn):
        if pawn._get_address() == pc.Pawn._get_address() or not pawn.IsAliveAndWell() or not pc.Pawn.IsEnemy(pawn):
            continue
        q = pawn.Location
        if (q.X - where.X) ** 2 + (q.Y - where.Y) ** 2 + (q.Z - where.Z) ** 2 <= radius * radius:
            targets.append(pawn)
    zero = unrealsdk.make_struct("Vector", X=0, Y=0, Z=0)
    for pawn in targets:
        pawn.TakeDamage(damage, pc, pawn.Location, zero, unrealsdk.find_class("WillowDmgSource_Bullet"))
    return len(targets)


def _fire_child(t: dict, proj: UObject, k: int) -> None:
    """One MIRV child: a gyrojet round (explodes on any impact) from the held blast to the ground nearby."""
    import math
    import random

    weapon = t["weapon"]()
    fm = None
    try:
        fm = unrealsdk.find_object("FiringModeDefinition", GYROJET)
    except ValueError:
        pass
    if weapon is None or fm is None:
        return
    loc = proj.Location
    ground = t.get("ground_z", loc.Z)
    angle = 2 * math.pi * k / MIRV_CHILDREN + random.uniform(-0.3, 0.3)
    dist = random.uniform(*MIRV_SCATTER)
    target = unrealsdk.make_struct("Vector", X=loc.X + math.cos(angle) * dist, Y=loc.Y + math.sin(angle) * dist, Z=ground - 60)
    global _expect_child, _fallback_logged
    _expect_child = t["damage"] * MIRV_CHILD_SHARE
    before = len(_children)
    try:
        weapon.Behavior_Fire(
            fm, target, proj, 1.0, 0.0, 0.0,  # its own hit is a token: the child's blast deals the damage
            unrealsdk.find_class("WillowDmgSource_Bullet"), weapon.GetDamageTypeDefinitionForFiringMode(fm),
            weapon.GetTraceImpact(), "None", True,
        )
    finally:
        damage, _expect_child = _expect_child, None
    if len(_children) == before:  # the child wasn't caught: blast where it lands, a moment later
        _pending.append((game_now() + 0.35, target, damage))
        if not _fallback_logged:
            _fallback_logged = True
            log("Tediore MIRV: child projectile not caught at spawn, using timed blasts")


def _tick_thrown() -> None:
    now = game_now()
    if _pending:
        pc = get_pc(possibly_loading=True)
        for item in [x for x in _pending if x[0] <= now]:
            _pending.remove(item)
            if pc is not None and pc.Pawn is not None:
                _area_damage(pc, item[1], MIRV_CHILD_RADIUS, item[2])
    if not _thrown:
        return
    for t in list(_thrown):
        proj = t["proj"]()
        if proj is None:
            _thrown.remove(t)
            continue
        if t["kind"] != "MIRV" or t["next"] <= 0 or now < t["next"]:
            continue
        if t["children"] < MIRV_CHILDREN:
            try:
                _fire_child(t, proj, t["children"])
            except Exception as e:  # noqa: BLE001 - never leave a held grenade hanging
                log(f"Tediore MIRV child failed: {e!r}")
                t["children"] = MIRV_CHILDREN
            t["children"] += 1
            t["next"] = now + 0.07
            proj.Velocity = unrealsdk.make_struct("Vector", X=0, Y=0, Z=0)
            if t.get("pop") is not None:
                proj.SetLocation(t["pop"])  # hold it up there while the children launch
        else:
            t["ending"] = True
            _thrown.remove(t)
            proj.Detonate()


def _nearest_enemy(player_pawn: UObject, where: UObject) -> UObject | None:
    """Nearest live enemy in range. No sight check: the turret fires real bullets, so walls stop them.

    (Every sight test called from the mod returned 'blocked' in v0.10.5-0.10.9, even a trace straight
    up into open air and the player's own view: FastTrace, Trace, Controller.LineOfSightTo.)
    """
    best, best_d = None, TURRET_RANGE**2
    loc = where.Location
    for pawn in pawns(player_pawn):
        if pawn._get_address() != player_pawn._get_address() and pawn.IsAliveAndWell() and player_pawn.IsEnemy(pawn):
            p = pawn.Location
            d = (p.X - loc.X) ** 2 + (p.Y - loc.Y) ** 2 + (p.Z - loc.Z) ** 2
            if d < best_d:
                best, best_d = pawn, d
    return best


_fire_failed = False


def _fire(pc: UObject, t: dict, proj: UObject, target: UObject) -> None:
    """A real bullet from the hovering gun at the target, with the thrown gun's own firing mode, damage
    type and impacts (WillowWeapon.Behavior_Fire). Falls back to direct damage if BL2 refuses it."""
    global _fire_failed
    weapon = t["weapon"]()
    if not _fire_failed and weapon is not None:
        try:
            fm = weapon.GetFiringModeDefinition()
            weapon.Behavior_Fire(
                fm,
                target.Location,
                proj,
                t["damage"],
                0.0,
                0.0,
                unrealsdk.find_class("WillowDmgSource_Bullet"),
                weapon.GetDamageTypeDefinitionForFiringMode(fm),
                weapon.GetTraceImpact(),
                "None",
                True,
            )
            if t["shots"] == 1:
                log(f"Tediore turret: first bullet fired via Behavior_Fire ({fm.Name if fm else None})")
            return
        except Exception as e:  # noqa: BLE001 - logged once, then direct damage
            _fire_failed = True
            log(f"Tediore turret: Behavior_Fire refused ({e!r}); falling back to direct damage")
    target.TakeDamage(
        t["damage"],
        pc,
        target.Location,
        unrealsdk.make_struct("Vector", X=0, Y=0, Z=0),
        unrealsdk.find_class("WillowDmgSource_Bullet"),
    )


@hook("WillowGame.WillowProjectile:Explode")
def hold_explosion(obj: UObject, *_: object) -> type[Block] | None:
    """A turret's own fuse/impact can't blow it up early; a Chaser's blast deals the throw's damage; a
    MIRV's first blast is held while its children fly."""
    for t in _turrets:
        proj = t["proj"]()
        if proj is not None and not t["ending"] and proj._get_address() == obj._get_address():
            return Block
    child = _children.pop(obj._get_address(), None)
    if child is not None and child[0]() is not None:
        pc = get_pc(possibly_loading=True)
        if pc is not None and pc.Pawn is not None:
            hits = _area_damage(pc, obj.Location, MIRV_CHILD_RADIUS, child[1])
            log(f"Tediore MIRV child: hit {hits} enemies for {child[1]:.0f}")
        return None
    for t in list(_thrown):
        proj = t["proj"]()
        if proj is None or proj._get_address() != obj._get_address() or t["ending"]:
            continue
        pc = get_pc(possibly_loading=True)
        if t["kind"] == "Chaser":
            _thrown.remove(t)
            if pc is not None and pc.Pawn is not None:
                hits = _area_damage(pc, proj.Location, CHASER_RADIUS, t["damage"])
                log(f"Tediore Chaser: blast hit {hits} enemies for {t['damage']:.0f}")
            return None  # its own explosion effect still plays
        if t["kind"] == "MIRV":
            if t["next"] <= 0:
                t["next"] = game_now() + 0.12  # children after the pop-up
                proj.SetPhysics(PHYS_NONE)
                loc = proj.Location
                t["ground_z"] = loc.Z
                t["pop"] = unrealsdk.make_struct("Vector", X=loc.X, Y=loc.Y, Z=loc.Z + MIRV_POP)
                proj.SetLocation(t["pop"])
            return Block
    return None


def _tick_turrets() -> None:
    if not _turrets:
        return
    pc = get_pc(possibly_loading=True)
    now = game_now()
    for t in list(_turrets):
        proj = t["proj"]()
        age = now - t["start"]
        if proj is None or pc is None or pc.Pawn is None:
            _turrets.remove(t)
            continue
        if age >= TURRET_FREEZE:
            if t["frozen"] is None:
                loc = proj.Location
                t["frozen"] = unrealsdk.make_struct("Vector", X=loc.X, Y=loc.Y, Z=loc.Z)
                proj.SetPhysics(PHYS_NONE)
                log(f"Tediore turret: pinned at ({loc.X:.0f},{loc.Y:.0f},{loc.Z:.0f}), physics {proj.Physics}")
            proj.SetLocation(t["frozen"])
            proj.Velocity = unrealsdk.make_struct("Vector", X=0, Y=0, Z=0)
            proj.RotationRate = unrealsdk.make_struct("Rotator", Pitch=0, Yaw=0, Roll=0)  # no tumbling
            if t.get("aim") is not None:
                proj.SetRotation(t["aim"])
        if age >= TURRET_FREEZE + TURRET_TIME:
            log(f"Tediore turret: {t['shots']} shot(s), {t['damage']:.0f} dmg each")
            t["ending"] = True
            _turrets.remove(t)
            proj.Detonate()
            continue
        if t["frozen"] is not None and now >= t["next"]:
            t["next"] = now + TURRET_INTERVAL
            target = _nearest_enemy(pc.Pawn, proj)
            if target is not None:
                t["shots"] += 1
                t["aim"] = _aim_at(proj, target)
                proj.SetRotation(t["aim"])
                _fire(pc, t, proj, target)


def _aim_at(proj: UObject, target: UObject):
    """Rotation pointing the hovering gun at its target."""
    import math

    a, b = proj.Location, target.Location
    dx, dy, dz = b.X - a.X, b.Y - a.Y, b.Z - a.Z
    return unrealsdk.make_struct(
        "Rotator",
        Pitch=int(math.atan2(dz, math.hypot(dx, dy)) * 32768 / math.pi),
        Yaw=int(math.atan2(dy, dx) * 32768 / math.pi),
        Roll=0,
    )


HOOKS = [apply_throw, on_throw, hold_explosion]
