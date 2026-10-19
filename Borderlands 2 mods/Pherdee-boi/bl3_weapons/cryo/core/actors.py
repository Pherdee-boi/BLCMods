"""Which objects are safe to call into: live actors only. And reading the game cheaply.

`find_all` returns archetypes and templates stored in packages next to the actors spawned from them, and a level
holds pawns that aren't in play. Calling script functions on those crashed the game more than once (below), so
everything that walks actors filters with these first. Reading properties is always fine.

And who may be hit by a call of the mod's own (`hit_for`): never a body that rides on another.

What reading costs (measured 2026-10-04, RESEARCH.md §6.44; 223,000 objects in memory):
- `find_all` walks every object there is: 7 ms a call, whatever it finds. Nothing that runs in play may use it; the
  enemies in play are the level's own list of pawns (`live_ai_pawns`), a tenth of a millisecond.
- A property read by name looks the name up among the class's fields first: 10 to 30 microseconds on the game's big
  classes (a controller, a pawn), and several times that in the middle of a fight, when the fields aren't fresh in
  the processor's memory (0.13 ms for a controller's `WorldInfo`, measured 2026-10-07). Through the field itself,
  found once, it is 1 to 3 (`Field`). Worth it where something is read every frame, for every pawn or for every
  hit: the fields below are the ones those places share.
- A function is found fast whatever the class (1.5 to 3 microseconds a call for `GetHealth`, `IsBoss`).
- The map's clock is `now()`: 7 microseconds, where `actor.WorldInfo.TimeSeconds` is the slow read above.
"""

from __future__ import annotations

from typing import Any

import unrealsdk
from mods_base import ENGINE, get_pc
from unrealsdk.unreal import UObject


class Field:
    """A property read through its field, found once: `Field("Pawn", "NextPawn")(pawn)`. The object must be of that
    class (or one that extends it): the read goes straight to the field's place in it."""

    def __init__(self, class_name: str, name: str) -> None:
        self.class_name, self.name = class_name, name
        self._field: Any = None

    def _found(self) -> Any:
        field = self._field
        if field is None:
            try:
                field = unrealsdk.find_class(self.class_name)._find(self.name)
            except Exception:  # noqa: BLE001 - not found that way: by name from now on, as before
                field = False
            self._field = field
        return field

    def __call__(self, obj: UObject) -> Any:
        field = self._found()
        return obj._get_field(field) if field else getattr(obj, self.name)

    def set(self, obj: UObject, value: Any) -> None:
        """Write the property (1 to 2 us; by name a write is 12 to 27 on a pawn or its body)."""
        field = self._found()
        if field:
            obj._set_field(field, value)
        else:
            setattr(obj, self.name, value)


_PAWN_LIST = Field("WorldInfo", "PawnList")
_NEXT_PAWN = Field("Pawn", "NextPawn")
_CONTROLLER = Field("Pawn", "Controller")
_DELETE_ME = Field("Actor", "bDeleteMe")
_LOCATION = Field("Actor", "Location")
_TIME = Field("WorldInfo", "TimeSeconds")
_PAUSER = Field("WorldInfo", "Pauser")
# For the places that read these of a pawn or a controller often (a pawn's body is `Pawn.Mesh`, a controller's
# pawn `Controller.Pawn`): the object must be one, as with any `Field`.
CONTROLLER_OF = _CONTROLLER
PAWN_OF = Field("Controller", "Pawn")
MESH_OF = Field("Pawn", "Mesh")
LOCATION_OF = _LOCATION
_MOST_PAWNS = 4096              # a list that long isn't one: stop
_actor: list[Any] = [None]      # the Actor class, found once
_controller: list[Any] = [None]     # ... and the Controller class
_placed: list[Any] = [-1.0, None, []]   # the map's clock, its world, and every pawn with its place at that moment


def now() -> float:
    """The map's clock, in seconds: what `actor.WorldInfo.TimeSeconds` reads, of the world that is running. It
    stands still while the game is paused and starts again with each new world. 0.0 when there is none."""
    world = ENGINE.GetCurrentWorldInfo()
    return float(_TIME(world)) if world is not None else 0.0


def is_live_actor(obj: UObject | None) -> bool:
    """A spawned actor in a level. find_all also returns archetypes and templates stored in packages;
    calling script functions on those can crash the game (2026-09-30), so filter with this first."""
    if obj is None:
        return False
    outer = obj.Outer
    if outer is None or outer.Class.Name != "Level":
        return False
    # Asked of every frozen enemy ten times a second: through the field for an actor (2 us, not 15 by name).
    actor = _actor[0]
    if actor is None:
        actor = _actor[0] = unrealsdk.find_class("Actor")
    return not (_DELETE_ME(obj) if obj.Class._inherits(actor) else obj.bDeleteMe)


def running_world(near: UObject | None = None) -> UObject | None:
    """The world that is running (asked of the engine: 3 us), or the one `near` is in when there is none yet."""
    world = ENGINE.GetCurrentWorldInfo()
    if world is None:
        if near is None:
            near = get_pc(possibly_loading=True)
        world = near.WorldInfo if near is not None else None
    return world


def pawns(near: UObject | None = None) -> list[UObject]:
    """Every pawn of the running world (`near`: any actor of it, asked only when the engine names no world):
    players, enemies, vehicles. The level's own list, which holds spawned pawns only."""
    world = running_world(near)
    out: list[UObject] = []
    pawn = _PAWN_LIST(world) if world is not None else None
    while pawn is not None and len(out) < _MOST_PAWNS:
        out.append(pawn)
        pawn = _NEXT_PAWN(pawn)
    return out


def placed(near: UObject | None = None) -> list[tuple[UObject, float, float, float]]:
    """Every pawn of the running world with the place it stands at: (pawn, x, y, z). The list is walked once for one
    moment of the map's clock, however often this is asked in it: a blast that kills six frozen enemies in a frame
    asks six times who stood near, and each walk is two reads of every pawn the map holds (1 ms with 300 of them).
    While the game is paused its clock stands still, and nothing is kept: the pawns kept could be freed meanwhile."""
    world = running_world(near)
    if world is None:
        return []
    stamp = float(_TIME(world))
    if _placed[0] == stamp and _placed[1] == world and _PAUSER(world) is None:
        return _placed[2]
    rows: list[tuple[UObject, float, float, float]] = []
    pawn = _PAWN_LIST(world)
    while pawn is not None and len(rows) < _MOST_PAWNS:
        where = _LOCATION(pawn)
        rows.append((pawn, float(where.X), float(where.Y), float(where.Z)))
        pawn = _NEXT_PAWN(pawn)
    _placed[0], _placed[1], _placed[2] = stamp, world, rows
    return rows


def forget_placed() -> None:
    """Nothing kept of where the pawns stood (a new map: the old one's are gone)."""
    _placed[0], _placed[1], _placed[2] = -1.0, None, []


def deleted(actor: UObject) -> bool:
    """An actor's `bDeleteMe`, through the field (the object must be an actor)."""
    return bool(_DELETE_ME(actor))


def pawn_of(controller: UObject | None) -> UObject | None:
    """A controller's pawn, through the field (3 us; by name 20 to 30 on a player's controller). None for nobody,
    and for what is no controller and has no pawn."""
    if controller is None:
        return None
    cls = _controller[0]
    if cls is None:
        cls = _controller[0] = unrealsdk.find_class("Controller")
    if controller.Class._inherits(cls):
        return PAWN_OF(controller)
    return getattr(controller, "Pawn", None)


def live_ai_pawns(near: UObject | None = None) -> list[UObject]:
    """Spawned AI pawns that are in play: with a controller. A level also holds dormant pawns it placed but hasn't
    activated, and a dying one loses its controller; calling into those (even GetHealth) crashed the game
    (2026-10-02, shatter's neighbour scan), so they're left out. `near`: any actor of that world (see `pawns`).

    Until 0.4.5 this asked for every WillowAIPawn object in memory, 7 ms at each frozen kill and each slam; the
    level's list gives the same pawns (27 of 27, with enemies of three kinds alive)."""
    enemy = unrealsdk.find_class("WillowAIPawn")
    return [p for p in pawns(near)
            if p.Class._inherits(enemy) and p.Outer is not None and p.Outer.Class.Name == "Level"
            and not _DELETE_ME(p) and _CONTROLLER(p) is not None]


def live_ai_pawns_within(near: UObject, x: float, y: float, z: float, radius: float) -> list[UObject]:
    """`live_ai_pawns`, of those within `radius` of a point. The distance is asked first, of every pawn (one read
    each); what makes a pawn one in play is asked only of the few that are near. In a crowd that is the difference:
    with 57 enemies the whole list cost 1 ms at every frozen kill, and three reads of each one's place 2 more.
    Since 0.6.2 the places are read once for all who ask in the same frame (`placed`): with 300 pawns in the map a
    frozen kill's look around was 3.6 ms, and six in one frame six times that."""
    enemy = unrealsdk.find_class("WillowAIPawn")
    limit = radius * radius
    out = []
    for p, px, py, pz in placed(near):          # where each stood at this moment of the clock: walked once (`placed`)
        if (px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2 > limit:
            continue
        if (p.Class._inherits(enemy) and p.Outer is not None and p.Outer.Class.Name == "Level"
                and not _DELETE_ME(p) and _CONTROLLER(p) is not None):
            out.append(p)
    return out


_MOST_PARENTS = 4               # a turret on a robot is one; a chain longer than this isn't one


def parent_of(pawn: UObject) -> UObject | None:
    """The body this one rides on, by the game's own `GetParent`: Saturn for each of its four turrets. None for one
    that stands alone, and for an actor that has no such function."""
    try:
        return pawn.GetParent()
    except Exception:  # noqa: BLE001 - not a pawn of the game's: it rides on nothing
        return None


def hit_for(pawn: UObject, amount: float) -> tuple[UObject | None, float]:
    """Who takes a hit the mod deals by a call of its own (`TakeDamage`), and how much of it.

    A body that rides on another hands part of every hit on to that one (`WillowPawn.ModifyParentDamage`, then the
    parent's own `TakeDamage`). When the hit came from a call made from Python, the game never comes back from that
    hand-over: its script interpreter goes round in the caller's code for ever, and the game stands still (Saturn's
    turrets with Custom Loads or the Contract, a Nexus report; reproduced and read out of the standing game,
    2026-10-06, RESEARCH.md §6.74). The game's own hits do the same hand-over and come back.

    So the mod never calls into such a body. The one it rides on takes what the game would have handed on: the
    hit times the rider's `DamageParentScale`, and no more than its `ParentDamageLimit` leaves room for (the two
    fields `ModifyParentDamage` reads). The rider itself goes without the mod's part of the hit. (None, 0.0) when
    there is nobody to hit."""
    for _ in range(_MOST_PARENTS):
        parent = parent_of(pawn)
        if parent is None:
            return pawn, amount
        if not is_live_actor(parent) or _CONTROLLER(parent) is None:
            return None, 0.0                    # the one it rides on is dying or gone: nothing is called into
        body = getattr(pawn, "BodyClass", None)
        if body is None:
            return None, 0.0
        amount *= float(body.DamageParentScale)
        limit = float(body.ParentDamageLimit)
        if limit > 0.0:
            amount = min(amount, float(parent.GetHealth()) - float(parent.GetMaxHealth()) * limit)
        if amount <= 0.0:
            return None, 0.0
        pawn = parent
    return None, 0.0
