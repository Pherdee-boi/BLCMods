"""The frozen look in a joined player's game (RESEARCH.md §6.77).

Freezing is the host's: the cryo status lands there (element.py's hooks on `ApplyStatusEffect`), the enemy is held
there, and its looks hang on that (the tint, the frost layer, the icicles; the burst when it dies frozen). A joined
game is never told of the status itself: its copy of the enemy has an empty status list, and `ApplyStatusEffect`
doesn't run there. So up to 0.5.2 a joined player saw a frozen enemy as an enemy standing still, with the slow
status' own green swirl around it.

What a joined game is given are the status' emitters: actors of their own (`StatusEffectReplicatedEmitter`), four
on a bullymong, each saying by a flag which kind of status it draws. The cryo status is BL2's slow kind (`bSlow`),
which no status of BL2's own is. They register with the enemy's status component (`RegisterEmitter`) when the status
lands, and are gone from its list when it ends. An enemy that dies comes torn off (and, burst by the host, hidden).

So in a game that is not the host's:
- an enemy whose component is given a slow emitter is frozen or slowed here as cryo's rules have it for its kind
  (rules.py goes by the enemy's class, which is the same in every game; the host's rolls and resistances have been
  through already, or there would be no status): its pose held and its looks drawn, through element.py's own `chill`;
- ten times a second, while any is: one that has come torn off bursts as on the host, and one whose slow emitters
  are gone thaws;
- the slow status' own swirl is switched off in that emitter's particle system, as the host does it for its own.

Nothing here touches what an enemy does: that is the host's, and reaches this game as the enemy standing still.

A slow status of another mod's, in a host's game without this one, would be drawn as a freeze here; none is known.
"""

from __future__ import annotations

from typing import Any

from mods_base import hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WeakPointer, WrappedStruct

from ..core import actors, maps
from . import element, rules

ROLE_AUTHORITY = 3          # ENetRole: this game runs the actor (the host's, and anyone's own torn-off copy)
HOLD = 3600.0               # a copy's freeze has no clock here: it lasts while the host's status does
PLAYER = "WillowPlayerPawn"

_held: dict[str, WeakPointer] = {}      # the copies frozen or slowed here, by path
_ROLE = actors.Field("Actor", "Role")


def _slow_emitters(pawn: UObject) -> bool:
    component = pawn.GetStatusEffectsComponent()
    if component is None:
        return False
    return any(emitter is not None and emitter.bSlow for emitter in component.ClientEmitters)


@hook("WillowGame.StatusEffectsComponent:RegisterEmitter", Type.POST)
def _emitter(obj: UObject, args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    """A status' emitter has come for an actor of this game: a slow one on a copy of an enemy is the cryo status."""
    pawn = obj.Owner
    # The role through its field: four emitters register at every status, in the host's game too, and by name the
    # read is among the slow ones on a pawn (core/actors.py). A status component's owner is always an actor.
    if pawn is None or int(_ROLE(pawn)) == ROLE_AUTHORITY:
        return                                      # the host's own: its status hooks have done all this
    emitter = args.Emitter
    if emitter is None or not getattr(emitter, "bSlow", False) or not element._is_pawn(pawn):
        return
    try:
        template = emitter.EmitterTemplate
        if template is not None:
            element._mute(template)                 # the slow status' own swirl, off in this game too
        if pawn.Class.Name != PLAYER:
            chill(pawn)
    except Exception as ex:  # noqa: BLE001 - looks; never a joined game's business to stop over
        element._once(f"proxy:{type(ex).__name__}", f"a frozen enemy's look in a joined game failed: {type(ex).__name__}: {ex}")


def chill(pawn: UObject) -> str:
    """This game's copy of an enemy the host has put the cryo status on: frozen or slowed here, for its looks."""
    if not actors.is_live_actor(pawn) or bool(pawn.bTearOff):
        return rules.NONE
    was_frozen = element.is_frozen(pawn)
    mode = element.chill(pawn, HOLD)
    if mode in (rules.FREEZE, rules.SLOW):
        _held[pawn._path_name()] = WeakPointer(pawn)
    if mode == rules.FREEZE and not was_frozen:
        element._once("proxy", f"first frozen enemy drawn in a joined game: {pawn.Name}")
        element._notify(element.ON_FREEZE_LOOK, pawn, None)
    return mode


def _watch() -> None:
    """Ten times a second while anything is frozen or slowed (element.WHILE_CHILLED): the copies' ends."""
    if not _held:
        return
    for key, ref in list(_held.items()):
        pawn = ref()
        if pawn is None or not actors.is_live_actor(pawn):
            del _held[key]                          # gone with its actor: element's own tick drops what it kept
            continue
        if bool(pawn.bTearOff):                     # it died in the host's game
            del _held[key]
            frozen = element.is_frozen(pawn)
            element.thaw(pawn)
            if frozen:
                element._notify(element.ON_SHATTER, pawn, bool(pawn.bHidden))
        elif not _slow_emitters(pawn):              # the host's status has ended
            del _held[key]
            element.thaw(pawn)


element.WHILE_CHILLED.append(_watch)


@hook(maps.ARRIVED, Type.POST)
@hook(maps.NEW_WORLD, Type.POST)
def _on_new_map(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    _held.clear()


HOOKS = (_emitter, _on_new_map)
