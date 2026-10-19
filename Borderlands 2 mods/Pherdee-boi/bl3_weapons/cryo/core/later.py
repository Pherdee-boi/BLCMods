"""Work that can wait a frame or two: a queue, worked off inside a time budget each frame.

A freeze is two things. The hold on the enemy has to happen at once. Its looks (the frost coat, the icicles) cost twice
as much, and nobody sees whether they came this frame or five frames on. In a crowd the looks alone made one frame
150 ms long: 57 enemies frozen at once with Spawn Multiplier at 25x (2026-10-04, RESEARCH.md §6.46). So looks go through
here: each frame as many as fit in BUDGET, oldest first, and always at least one.

A job is called with the arguments it was queued with. An enemy is queued as a WeakPointer and looked at again when
its turn comes: by then it may be dead, thawed, or gone with the map.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any

import unrealsdk
from mods_base import hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

from . import maps

BUDGET = 0.0025                  # seconds of queued work in one frame (a frame at 60 a second is 0.0167)

_queue: deque[tuple[Callable[..., Any], tuple[Any, ...]]] = deque()
_said: set[str] = set()


def do(job: Callable[..., Any], *args: Any) -> None:
    """Queue `job(*args)` for one of the next frames."""
    _queue.append((job, args))
    _wake()


def first(job: Callable[..., Any], *args: Any) -> None:
    """Queue `job(*args)` ahead of what waits: for what a player would miss if it came late (a shatter's freeze of
    the enemies around it), where `do` is for looks."""
    _queue.appendleft((job, args))
    _wake()


def _wake() -> None:
    # The tick is switched on when the queue gets its first job, and now and then after (should anything have switched
    # it off under a queue): until 0.6.1 at every job, a call into the SDK each.
    if len(_queue) == 1 or len(_queue) % 32 == 0:
        _tick.enable()


def waiting() -> int:
    return len(_queue)


def stop() -> int:
    """Drop what is queued and turn the tick off (a new map; the mod switched off). Returns how many were dropped."""
    dropped = len(_queue)
    _queue.clear()
    _tick.disable()
    return dropped


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def _tick(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    """On only while something is queued: `do` turns it on, so it is not one of the mod's listed hooks."""
    end = time.perf_counter() + BUDGET
    while _queue:
        job, args = _queue.popleft()
        try:
            job(*args)
        except Exception as ex:  # noqa: BLE001 - looks only; never break a frame
            name = getattr(job, "__name__", str(job))
            if f"{name}:{type(ex).__name__}" not in _said:
                _said.add(f"{name}:{type(ex).__name__}")
                unrealsdk.logging.info(f"[Aurelia] later: {name} failed: {type(ex).__name__}: {ex}")
        if time.perf_counter() >= end:
            break
    if not _queue:
        _tick.disable()


@hook(maps.ARRIVED, Type.POST)
@hook(maps.NEW_WORLD, Type.POST)
def _on_new_map(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    stop()                       # what was queued belongs to the old map


HOOKS = (_on_new_map,)
