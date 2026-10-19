"""S2 freeze spike (dev): hold a BL2 enemy frozen, then release it cleanly (RESEARCH.md §6.6, §8 S2).

BL2 has no freeze: TPS's lives in TPS-only WillowAIPawn states and a frozen material parameter. BL2
does have Gearbox's AI hold system on the brain component (`AIComponent.Hold`/`Release`,
`HoldMovement`/`ReleaseMovement`, `HoldTarget`/`ReleaseTarget`, and `WillowAIComponent.HoldPhaseLock`
for Maya's Phaselock), keyed by a hold name and tag, so holds from different sources stack. A freeze
here is our own named hold on the brain and movement, plus paused animations for the pose. The tag
must be an object: with a None tag the calls return, but `OnHold()` stays False and nothing is held.

Measured 2026-09-30 on bullymongs in Three Horns - Divide:
- `CustomTimeDilation` (pawn + controller at 0.0001) does nothing to BL2 AI: held bullymongs ran
  ~5,900 units and killed the test character.
- `Mesh.bPauseAnims` freezes the pose but not movement (a pawn slid 1,700 units in a frozen pose).
- `bPauseNavigationUpdate` stops most walking but not leaps; the population system resets
  `bDisabledDueToPopulationIrrelevance` right away.

Measured 2026-10-05 (the user: "if they are mid animation, their animations complete before they stop moving"), on
bullymongs let loose in Windshear Waste, each frozen at a chosen moment, six of its bones read every tick in its own
frame:
- frozen while running: the pose stops within a few units;
- frozen in a leap or as one begins: with `bPauseAnims` alone the pose went on changing for up to a second (100 to
  180 units at a hand or a foot): the move that had begun played to its end. With `Mesh.bNoSkeletonUpdate` set as well
  the pose is not worked out again at all: 3 units. So a freeze sets both, and gives both back.
A frozen enemy still stands up as a statue: it is not a ragdoll (BL2's enemies go limp only when they die, and have no
way back up from it).
"""

from __future__ import annotations

from typing import Any

import unrealsdk
from mods_base import hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WeakPointer, WrappedStruct

from ..core import actors, maps

HOLD_NAME = "AureliaFreeze"
SLOW_FACTOR = 0.5          # default; element.py passes its own
_ROLE_AUTHORITY = 3        # ENetRole: this game runs the actor

# pawn path -> what to restore. The pawn and its brain by weak pointer: a map's actors are freed when the player
# travels on, and a freed one must not be asked anything (element.py's notes).
_held: dict[str, dict[str, Any]] = {}
_said: set[str] = set()
_pawn: list[Any] = [None]       # the Pawn class, found once
_body: list[Any] = [None]       # ... and the class of a pawn's body
_ROLE = actors.Field("Actor", "Role")
_VELOCITY = actors.Field("Actor", "Velocity")
_ACCELERATION = actors.Field("Actor", "Acceleration")
_PAUSED = actors.Field("SkeletalMeshComponent", "bPauseAnims")
_STILL = actors.Field("SkeletalMeshComponent", "bNoSkeletonUpdate")


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] freeze: {msg}")


def _is_pawn(actor: UObject) -> bool:
    cls = _pawn[0]
    if cls is None:
        cls = _pawn[0] = unrealsdk.find_class("Pawn")
    return actor.Class._inherits(cls)


def _brain(pawn: UObject) -> UObject | None:
    # An actor that is no pawn has no controller (and never freezes). A pawn's is read through its field: by name
    # it is one of the slow reads on a pawn (core/actors.py), and this is asked at every freeze.
    mind = actors.CONTROLLER_OF(pawn) if _is_pawn(pawn) else getattr(pawn, "Controller", None)
    return getattr(mind, "AIComponent", None) if mind is not None else None


def _mesh(pawn: UObject) -> Any:
    return actors.MESH_OF(pawn) if _is_pawn(pawn) else pawn.Mesh


def _own(key: str, pawn: UObject) -> dict[str, Any] | None:
    """What is kept for this pawn. A name is used again when its enemy has left the map without dying (the player
    travelled on, the map took it away): what an earlier one left under it is dropped, never taken for this one's
    (until 0.4.9 `freeze` would have taken such an enemy for held already: frost on it, and walking on)."""
    state = _held.get(key)
    if state is not None and state["pawn"]() != pawn:
        del _held[key]
        return None
    return state


def freeze(pawn: UObject) -> bool:
    """Hold a pawn still: its brain and movement on our named hold, its pose kept as it is. Idempotent.
    Who gets frozen, slowed or neither is rules.py's call (M3), not this module's."""
    key = pawn._path_name()
    state = _own(key, pawn)
    if state is not None and state["kind"] == "freeze":
        return True
    brain = _brain(pawn)
    if brain is None and int(_ROLE(pawn)) == _ROLE_AUTHORITY:
        return False
    # With no brain and not this game's to run, it is a joined game's copy of an enemy the host holds (proxies.py):
    # there is nothing to hold here, and its pose is kept as the host's is.
    if state is not None:                       # slowed -> frozen
        _restore(_held.pop(key))
    zero = unrealsdk.make_struct("Vector", X=0.0, Y=0.0, Z=0.0)
    mesh = _mesh(pawn)
    _held[key] = {"kind": "freeze", "pawn": WeakPointer(pawn), "brain": WeakPointer(brain) if brain is not None else None,
                  "paused": bool(mesh.bPauseAnims), "still": bool(mesh.bNoSkeletonUpdate)}
    if brain is not None:
        brain.Hold(HOLD_NAME, pawn)
        brain.HoldMovement(HOLD_NAME, pawn)
        _VELOCITY.set(pawn, zero)       # written through their fields: by name each write is a quarter of all a
        _ACCELERATION.set(pawn, zero)   # freeze's hold costs (core/actors.py)
    _pose(mesh, True, True)             # the pose as it is, at once: a leap or a swing under way no longer plays out
    return True


def _pose(mesh: UObject, paused: bool, still: bool) -> None:
    """A body's two pose switches (`bPauseAnims`, `bNoSkeletonUpdate`)."""
    if mesh.Class._inherits(_skeletal()):
        _PAUSED.set(mesh, paused)
        _STILL.set(mesh, still)
    else:
        mesh.bPauseAnims = paused
        mesh.bNoSkeletonUpdate = still


def _skeletal() -> Any:
    cls = _body[0]
    if cls is None:
        cls = _body[0] = unrealsdk.find_class("SkeletalMeshComponent")
    return cls


def slow(pawn: UObject, factor: float = SLOW_FACTOR) -> bool:
    """Slow a pawn's movement (Pawn.MovementSpeedModifier) and animations, so its attacks slow too."""
    key = pawn._path_name()
    state = _own(key, pawn)
    if state is not None and state["kind"] == "freeze":
        return True                              # frozen beats slowed
    if state is None:
        state = {"kind": "slow", "pawn": WeakPointer(pawn), "brain": None,
                 "speed": float(pawn.MovementSpeedModifier), "rate": float(pawn.Mesh.GlobalAnimRateScale)}
        _held[key] = state
    pawn.MovementSpeedModifier = state["speed"] * factor
    pawn.Mesh.GlobalAnimRateScale = state["rate"] * factor
    return True


def release(pawn: UObject) -> bool:
    state = _held.pop(pawn._path_name(), None)
    if state is None:
        return False
    _restore(state)
    return True


def _restore(state: dict[str, Any]) -> None:
    pawn = state["pawn"]()
    if pawn is None or not actors.is_live_actor(pawn):
        return
    if state["kind"] == "slow":
        pawn.MovementSpeedModifier = state["speed"]
        pawn.Mesh.GlobalAnimRateScale = state["rate"]
        return
    brain = state["brain"]() if state["brain"] is not None else None
    if brain is not None:
        brain.ReleaseMovement(HOLD_NAME, pawn)
        brain.Release(HOLD_NAME, pawn)
    mesh = _mesh(pawn)
    if mesh is not None:
        _pose(mesh, state["paused"], state.get("still", False))


def forget(key: str) -> None:
    """An enemy that is gone (not dead: gone with its map, or taken out of it) has nothing to give back."""
    _held.pop(key, None)


def release_all() -> int:
    count = 0
    for key in list(_held):
        _restore(_held.pop(key))
        count += 1
    _said.clear()
    return count


# A held pawn that dies is released first, so its death (ragdoll, death animation, drops) plays
# normally.
@hook("WillowGame.WillowAIPawn:Died")
def _release_on_death(obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    if not _held:
        return                          # nobody is held: every enemy's death comes through here
    if release(obj) and not _said:
        _said.add("death")              # said once: a line in the log at every frozen kill was a fifth of a
        _log(f"released {obj.Name} on death (said once)")       # millisecond each, in the middle of a fight


# A map change destroys every held actor, and those are forgotten; one that is still there is let go (core/maps.py).
@hook(maps.ARRIVED, Type.POST)
@hook(maps.NEW_WORLD, Type.POST)
def _forget_on_new_map(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    release_all()


HOOKS = (_release_on_death, _forget_on_new_map)
