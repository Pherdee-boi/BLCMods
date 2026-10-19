"""What must be in place before the game reads a save: built once, in a fixed order, from every feature's steps.

A save names skills, items and missions by path, and the game drops what it can't find when it reads one. So each
feature builds its runtime objects, loads its packages and registers its items before the first save is read. Done at
every roster read, the scans starved the main menu (2026-10-01), so it is a one-time setup, retried only while a step
that has to succeed hasn't.

The order crosses features, so it isn't any one feature's to keep. Each feature lists its own steps (`Step`), the mod
puts the lists together (`use`), and `run()` goes through them phase by phase, a feature's steps staying in the order
it listed them:

- **objects**: what a converted package imports and BL2 lacks, built before the package loads so its imports find them;
- **packages**: the converted packages, and what is patched on them as soon as they are loaded;
- **looks**: the effects packages, and what is dressed with them;
- **materials**: the particle materials' compile queue, once every package with particle systems is loaded;
- **world**: what must exist before a map starts (a mission: the tracker lists the missions that exist when it starts;
  nothing is in it since Marcus's cryo mission went, 0.6.2);
- **items**: saveable items, appended to the base game's libraries in this order (a save with no layout record is
  read in the order of the day, so the order stays the same from one session to the next);
- **records**: what is written out for other tools once all of it exists.

Who calls `run()`: whoever needs it done before a save is read. Today that is the Baroness's roster, at every roster
read and load, and only when her files are installed; so `started()` also answers "is the mod's content in the game?"
for the parts that must stay out of it otherwise.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import unrealsdk

PHASES =("objects", "packages", "looks", "materials", "world", "items", "records")


@dataclass(frozen=True)
class Step:
    """One thing the setup does. A guarded step's failure is logged and the setup goes on (looks, work in progress);
    an unguarded one's is raised, and the setup is retried at the next call. A needed step must also return something
    true, or the setup stops there and is retried (the item libraries, while the game hasn't made them)."""

    phase: str
    label: str
    run: Callable[[], Any]
    guarded: bool = True
    needed: bool = False


_steps: list[Step] = []
_state = {"done": False}


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] setup: {msg}")


def use(*steps: Step) -> None:
    """Add a feature's steps, after those already there."""
    for step in steps:
        if step.phase not in PHASES:
            raise ValueError(f"{step.label}: no setup phase {step.phase!r}")
    _steps.extend(steps)


def done() -> bool:
    return _state["done"]


def run() -> bool:
    """Idempotent and cheap once done. True when every step has run; False while a needed step isn't ready."""
    if _state["done"]:
        return True
    for phase in PHASES:
        for step in _steps:
            if step.phase != phase:
                continue
            if step.guarded:
                try:
                    result = step.run()
                except Exception as ex:  # noqa: BLE001 - see Step: never let it block what comes after
                    _log(f"{step.label} failed: {type(ex).__name__}: {ex}")
                    continue
            else:
                result = step.run()
            if step.needed and not result:
                return False
    _state["done"] = True
    return True
